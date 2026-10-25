"""追问对话（SPEC 13、19.6，设计 docs/design/M6.md 第七节）。

- 用户问题先在服务端脱敏（对照表 → 已记住名称与自定义敏感词 → 9.3 第 2、4～7 步），预览与发送各算一次，
  发送时不接受前端传来的脱敏结果。对照表里没有的对象换成按任务编号的代号（[公司_Q1]），发送前写入本任务对照表
  （加密），因此用户问题和模型回复都能还原，同一对象在后续追问中沿用同一代号（Q-97）；结果经模型网关（上限 → 出口复检 → 发送 → 记录）发出，不自动重试。
- chat_messages 只存代号形式；返回给前端时按对照表还原。失败时不写对话。
- 日志只写任务 id、llm_call id 和状态，不写问题与回复。
"""
import logging
import re
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.db.base import utcnow
from app.db.models import ChatMessage, Finding, RedactionMap, RedactionTerm, ReviewRun, Task, TaskFile, User
from app.llm import gateway, usage
from app.redaction import detect, ner, parties, placeholders, recheck, suspects
from app.redaction.engine import _load_full_map, build_outbound, load_redaction_map, write_full_map
from app.redaction.outbound import escape_document_tags
from app.redaction.restore import restore
from app.review.prompt import chat_system_prompt, split_outbound
from app.security import crypto
from app.security.errors import AppError

logger = logging.getLogger("app.chat")

MAX_CHARS = 1000
HISTORY_LIMIT = 10  # 最近 10 条对话（SPEC 13）
TIMEOUT_S = 120  # 同步返回，超时 120 秒（SPEC 13）
_SEVERITY = {"high": "高", "medium": "中", "low": "低"}
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


_ERRORS = {
    "BLOCKED": (409, "CHAT_BLOCKED", "问题中仍含未脱敏的信息，已拦截，未发送"),
    "CAP_REACHED": (409, "CHAT_CAP_REACHED", gateway.MESSAGES["CAP_REACHED"]),
    "USER_CAP_REACHED": (409, "USER_CAP_REACHED", gateway.MESSAGES["USER_CAP_REACHED"]),
    "NOT_CONFIGURED": (409, "CHAT_NOT_CONFIGURED", gateway.MESSAGES["NOT_CONFIGURED"]),
}
_FAILED = (502, "CHAT_FAILED", "回复失败，请稍后重试")

REASON_ORIGINAL_DELETED = "原件已按保留期限删除，不能追问"
REASON_LOCAL_ONLY = "该任务的外发策略为不外发，不提供追问"
REASON_NO_RUN = "该任务尚未完成任何审核轮次，暂不能追问"
REASON_BUSY = "任务正在重新审核或等待确认，完成后可以追问"


# ---------------------------------------------------------------- 可用条件（Q-88）


def availability(db: Session, task: Task) -> tuple[ReviewRun | None, str | None]:
    """(用于问题清单摘要的轮次, 不可用原因)。可用时原因为 None。"""
    if db.scalar(select(exists().where(TaskFile.task_id == task.id, TaskFile.original_deleted_at.is_not(None)))):
        return None, REASON_ORIGINAL_DELETED  # 保留期清理后不再提供追问（SPEC 16.4，M7-06）
    if task.policy != "ai":
        return None, REASON_LOCAL_ONLY
    run = db.scalar(select(ReviewRun).where(ReviewRun.task_id == task.id,
                                            ReviewRun.status.in_(("completed", "partial")))
                    .order_by(ReviewRun.run_no.desc()).limit(1))
    if run is None:
        return None, REASON_NO_RUN
    if task.status != "reviewed":
        return None, REASON_BUSY
    return run, None


def _require(db: Session, task: Task) -> ReviewRun:
    run, reason = availability(db, task)
    if run is None:
        raise AppError(409, "CHAT_UNAVAILABLE", reason or REASON_NO_RUN)
    return run


# ---------------------------------------------------------------- 问题脱敏（Q-89）


def _value_spans(text: str, value: str) -> list[tuple[int, int]]:
    """真实值在问题中的位置，判定规则与出口复检（recheck._value_positions）一致，但返回起止位置。"""
    value = value.strip()
    if len(value) < 2:
        return []
    if not recheck._NUMERIC_VALUE.match(value):
        return [m.span() for m in recheck._text_pattern(value).finditer(text)]
    out = []
    n_digits = len(recheck._digits(value))
    for m in re.finditer(re.escape(value), text):
        if recheck._digit_bounded(text, m.start(), m.end()) and (
                n_digits >= recheck._SHORT_DIGITS or recheck._has_currency(text, m.start(), m.end())):
            out.append(m.span())
    if n_digits >= recheck._LONG_DIGITS:
        compact = recheck._digits(value)
        for m in re.finditer(r"(?<!\d)\d[\d \-]*\d(?!\d)", text):
            if compact in recheck._digits(m.group()):
                out.append(m.span())
    if recheck._AMOUNT_VALUE.match(value):
        target = recheck._decimal(value)
        for m in recheck._NUMBER_IN_TEXT.finditer(text):
            if recheck._decimal(m.group(1)) == target and recheck._has_currency(text, m.start(1), m.end(1)):
                out.append(m.span(1))
    return out


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    code: str


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


@dataclass
class Redacted:
    text: str  # 代号形式的问题
    changed: bool  # 与原文不同
    new_entries: dict[str, str]  # 本次新分配的追问代号 → 真实值（发送前写入对照表）
    suspects: list[dict]  # 原文未替换区间；偏移量相对 message.strip()


def _redact(message: str, full_map: dict[str, str], terms: list[tuple[str, str]],
            decisions: list[dict] | None = None) -> Redacted:
    """纯函数：按 full_map（本任务对照表）和 terms（(名称, 类别)）脱敏。已被前一步替换的文字不再参与后续识别。"""
    text = message.strip()
    accepted: list[_Span] = []
    new_entries: dict[str, str] = {}
    next_no = max((n for k in full_map if (n := placeholders.chat_number(k)) is not None), default=0) + 1

    def take(start: int, end: int, code: str) -> None:
        if start >= end or any(s.start < end and start < s.end for s in accepted):
            return
        accepted.append(_Span(start, end, code))

    # 1. 本任务对照表（含以前追问分配的代号）：真实值 → 代号（“代号#序号”的键取 # 前的代号），长的优先
    mapping = [(key.split("#", 1)[0], value) for key, value in full_map.items() if value]
    by_value = {_norm(value): code for code, value in mapping}
    for code, value in sorted(mapping, key=lambda kv: len(kv[1]), reverse=True):
        for start, end in _value_spans(text, value):
            take(start, end, code)

    def code_for(surface: str, category: str, country: str | None = None) -> str:
        """对照表中已有的用原代号；没有的分配新的追问代号，同一对象（规范化后相同）只分配一次。"""
        nonlocal next_no
        known = by_value.get(_norm(surface))
        if known:
            return known
        code = placeholders.chat_code(category, next_no, country)
        next_no += 1
        new_entries[code] = surface
        by_value[_norm(surface)] = code
        return code

    # 2. 启用的已记住名称与自定义敏感词
    for term, category in sorted(terms, key=lambda t: len(t[0]), reverse=True):
        spans = _value_spans(text, term)
        if spans:
            code = code_for(text[spans[0][0]:spans[0][1]], category)
            for start, end in spans:
                take(start, end, code)

    # 3. 9.3 第 2 步：带企业后缀的名称（标签后的签约方、正文中的其他企业名称）
    for start, end in [(s_, e_) for _label, s_, e_ in parties.find_labeled_parties(text)] + parties.find_other_org_names(text):
        if not any(s.start < end and start < s.end for s in accepted):
            take(start, end, code_for(text[start:end], "ORG"))

    # V2-B-03：只从已有主体的完整名称派生，沿用原代号。
    short_derived = []
    for code, value in mapping:
        auto, short = parties.derived_names(value)
        short_derived.extend(short)
        for alias in auto:
            for start, end in _value_spans(text, alias):
                take(start, end, code)

    # 4. 9.3 第 4～7 步：格式化信息、金额、人名、地址
    for find in (detect.find_formatted, detect.find_amounts, detect.find_bare_amounts, detect.find_persons, detect.find_addresses, detect.find_projects):
        for hit in find(text):
            if hit.category == 'PROJECT':
                # V2Q-50：与正文相同，完整包含的任何类别均让位；部分交叉仍先到先得。
                overlaps = [s for s in accepted if s.start < hit.end and hit.start < s.end]
                if any(not (hit.start <= s.start and s.end <= hit.end) for s in overlaps):
                    continue
                accepted = [s for s in accepted if s not in overlaps]
            if any(s.start < hit.end and hit.start < s.end for s in accepted):
                continue
            country = hit.key if hit.category == "ADDR" and hit.key else None
            take(hit.start, hit.end, code_for(text[hit.start:hit.end], hit.category, country))

    model_suspects = []
    for hit in ner.recognize(ner.mask(text, [(s.start, s.end) for s in accepted])):
        if any(s.start < hit.end and hit.start < s.end for s in accepted):
            continue
        if hit.score >= ner.HIGH:
            surface = text[hit.start:hit.end]
            country = detect.find_country(surface) if hit.label == 'ADDR' else None
            take(hit.start, hit.end, code_for(surface, hit.label, country))
        elif hit.score >= ner.MEDIUM:
            model_suspects.append({'start': hit.start, 'end': hit.end,
                                   'text': text[hit.start:hit.end], 'reason': 'ner'})

    found = [{"start": s.start, "end": s.end, "text": text[s.start:s.end], "reason": s.reason}
             for s in suspects.find_suspects(text, [(s.start, s.end) for s in accepted], derived=short_derived)]
    for suspect in model_suspects:
        if not any((s['start'], s['end']) == (suspect['start'], suspect['end']) for s in found):
            found.append(suspect)
    if decisions is not None:  # None 只作预览；发送即使没传决定也用空列表重新校验。
        for suspect in found:
            matches = [d for d in decisions if (d["start"], d["end"]) == (suspect["start"], suspect["end"])]
            if not matches:
                raise AppError(409, "CHAT_SUSPECTS_PENDING", "问题中有尚未确认的疑似敏感信息")
            # 未写明重复决定的取舍：同一区间多条决定一律拒绝，避免依赖请求顺序（Q-159）。
            if len(matches) != 1:
                raise AppError(422, "VALIDATION_ERROR", "同一疑似敏感信息只能提交一个决定")
            decision = matches[0]
            if decision["decision"] == "replace":
                category = decision.get("category")
                if category not in ("ORG", "PERSON", "CUSTOM"):
                    raise AppError(422, "VALIDATION_ERROR", "替换疑似敏感信息时必须选择类别")
                take(suspect["start"], suspect["end"], code_for(suspect["text"], category))

    out, pos = [], 0
    for span in sorted(accepted, key=lambda s: s.start):
        out.append(text[pos:span.start])
        out.append(span.code)
        pos = span.end
    out.append(text[pos:])
    redacted = escape_document_tags("".join(out))
    used = {s.code for s in accepted}
    return Redacted(redacted, redacted != text, {k: v for k, v in new_entries.items() if k in used}, found)


def _terms(db: Session) -> list[tuple[str, str]]:
    return [(crypto.decrypt_text(r.term_enc), r.category)
            for r in db.scalars(select(RedactionTerm).where(RedactionTerm.enabled.is_(True)))]


def redact_question(db: Session, task: Task, message: str) -> tuple[str, bool]:
    """预览用：(代号形式的问题, 是否与原文不同)；不写对照表。"""
    r = _redact(message, _load_full_map(db, task.id), _terms(db))
    return r.text, r.changed


def _redact_and_record(db: Session, task: Task, message: str, decisions: list[dict] | None = None) -> str:
    """发送用：锁住本任务对照表后脱敏，新分配的追问代号写入对照表并提交，再返回代号形式的问题。

    在调用模型之前提交：同一任务并发追问时不会把同一代号分给不同对象；发送失败时多出的条目不影响还原。
    """
    db.scalar(select(RedactionMap).where(RedactionMap.task_id == task.id).with_for_update())
    full_map = _load_full_map(db, task.id)
    r = _redact(message, full_map, _terms(db), decisions=decisions or [])
    if r.new_entries:
        write_full_map(db, task.id, {**full_map, **r.new_entries})
    db.commit()
    return r.text


# ---------------------------------------------------------------- 消息


def _document(db: Session, task: Task) -> str:
    """最新外发文本（reviewed 状态下脱敏未变，与最新轮次一致）；超过分段长度时在行边界截断（Q-90）。"""
    parties, body = split_outbound(build_outbound(db, task).text)
    limit = int(get_setting(db, "model.chunk_chars") or 60000)
    note = ""
    if len(body) > limit:
        cut = body.rfind("\n", 0, limit)
        body = body[:cut if cut > 0 else limit]
        note = f"\n（文件较长，以上只含前 {len(body)} 字）"
    head = parties + "\n" if parties else ""
    return f"{head}【待审核文件】\n<document>\n{body}\n</document>{note}"


def _findings_summary(db: Session, run: ReviewRun) -> str:
    rows = sorted(db.scalars(select(Finding).where(Finding.run_id == run.id)),
                  key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.seq))
    if not rows:
        return "【问题清单摘要】本轮没有列出问题"
    lines = []
    for i, f in enumerate(rows, start=1):
        where = "、".join(f.locs or []) or ("缺失项" if f.missing else "无位置")
        lines.append(f"{i}. [{_SEVERITY.get(f.severity, f.severity)}] {escape_document_tags(f.title)}（{where}）")
    return "【问题清单摘要】\n" + "\n".join(lines)


def _history(db: Session, task: Task) -> list[ChatMessage]:
    rows = list(db.scalars(select(ChatMessage).where(ChatMessage.task_id == task.id)
                           .order_by(ChatMessage.id.desc()).limit(HISTORY_LIMIT)))
    return rows[::-1]


def build_messages(db: Session, task: Task, run: ReviewRun, question: str) -> list[dict]:
    """system（模板第 1–7 条 + 自然语言回答）→ 外发文本与问题清单摘要 → 最近 10 条对话 → 新问题（均为代号形式）。"""
    _version, system = chat_system_prompt()
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": _document(db, task) + "\n" + _findings_summary(db, run)}]
    messages += [{"role": m.role, "content": m.content_redacted} for m in _history(db, task)]
    messages.append({"role": "user", "content": question})
    return messages


# ---------------------------------------------------------------- 对外函数


def message_out(m: ChatMessage, mapping: dict[str, str]) -> dict:
    return {"id": m.id, "role": m.role, "content": restore(m.content_redacted, mapping), "created_at": m.created_at}


def list_messages(db: Session, task: Task) -> dict:
    _run, reason = availability(db, task)
    mapping = load_redaction_map(db, task.id)
    rows = db.scalars(select(ChatMessage).where(ChatMessage.task_id == task.id).order_by(ChatMessage.id))
    return {"available": reason is None, "reason": reason, "items": [message_out(m, mapping) for m in rows]}


def preview(db: Session, task: Task, message: str) -> dict:
    _require(db, task)
    r = _redact(message, _load_full_map(db, task.id), _terms(db))
    return {"redacted": r.text, "changed": r.changed, "suspects": r.suspects}


def send(db: Session, task: Task, user: User, message: str, decisions: list[dict] | None = None) -> dict:
    run = _require(db, task)
    reached = usage.cap_reached(db, user.id, "chat")
    if reached:
        status, code, text = _ERRORS[reached]
        raise AppError(status, code, text)
    question = (_redact_and_record(db, task, message, decisions) if decisions
                else _redact_and_record(db, task, message))
    messages = build_messages(db, task, run, question)
    try:
        result = gateway.call_model(db, purpose="chat", messages=messages, user_id=user.id, task_id=task.id,
                                    task_title=task.title, json_mode=False, timeout_s=TIMEOUT_S, retry=False)
    except gateway.GatewayError as exc:
        logger.info("task %s 追问失败 llm_call=%s 错误=%s", task.id, exc.llm_call_id, exc.code)
        status, code, text = _ERRORS.get(exc.code, _FAILED)
        raise AppError(status, code, text) from None
    asked = ChatMessage(task_id=task.id, role="user", content_redacted=question)
    db.add(asked)
    db.flush()
    answer = ChatMessage(task_id=task.id, role="assistant", content_redacted=result.content,
                         tokens_in=result.tokens_in, tokens_out=result.tokens_out)
    db.add(answer)
    task.last_activity_at = utcnow()
    db.commit()
    logger.info("task %s 追问已回复 llm_call=%s", task.id, result.llm_call_id)
    mapping = load_redaction_map(db, task.id)
    return {"user": message_out(asked, mapping), "assistant": message_out(answer, mapping)}
