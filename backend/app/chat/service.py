"""追问对话（SPEC 13、19.6，设计 docs/design/M6.md 第七节）。

- 用户问题先在服务端脱敏（对照表 → 已记住名称与自定义敏感词 → 9.3 第 4～7 步），预览与发送各算一次，
  发送时不接受前端传来的脱敏结果；结果经模型网关（上限 → 出口复检 → 发送 → 记录）发出，不自动重试。
- chat_messages 只存代号形式；返回给前端时按对照表还原。失败时不写对话。
- 日志只写任务 id、llm_call id 和状态，不写问题与回复。
"""
import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.db.models import ChatMessage, Finding, RedactionTerm, ReviewRun, Task, User
from app.llm import gateway
from app.redaction import detect, placeholders, recheck
from app.redaction.engine import _load_full_map, build_outbound, load_redaction_map
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

# 对照表里没有的对象用类别通用代号（Q-89）
_TERM_CODES = {"ORG": "[公司]", "PERSON": "[人员]", "CUSTOM": "[敏感词]"}
_HIT_CODES = {"PHONE": "[电话]", "EMAIL": "[邮箱]", "URL": "[网址]", "IDNO": "[证件号]", "ACCOUNT": "[账号]",
              "SWIFT": "[SWIFT]", "REFNO": "[编号]", "AMOUNT": "[金额]", "PRICE": "[单价]", "PERSON": "[人员]"}

_ERRORS = {
    "BLOCKED": (409, "CHAT_BLOCKED", "问题中仍含未脱敏的信息，已拦截，未发送"),
    "CAP_REACHED": (409, "CHAT_CAP_REACHED", gateway.MESSAGES["CAP_REACHED"]),
    "NOT_CONFIGURED": (409, "CHAT_NOT_CONFIGURED", gateway.MESSAGES["NOT_CONFIGURED"]),
}
_FAILED = (502, "CHAT_FAILED", "回复失败，请稍后重试")

REASON_LOCAL_ONLY = "该任务的外发策略为不外发，不提供追问"
REASON_NO_RUN = "该任务尚未完成任何审核轮次，暂不能追问"
REASON_BUSY = "任务正在重新审核或等待确认，完成后可以追问"


# ---------------------------------------------------------------- 可用条件（Q-88）


def availability(db: Session, task: Task) -> tuple[ReviewRun | None, str | None]:
    """(用于问题清单摘要的轮次, 不可用原因)。可用时原因为 None。"""
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


def redact_question(db: Session, task: Task, message: str) -> tuple[str, bool]:
    """(代号形式的问题, 是否与原文不同)。已被前一步替换的文字不再参与后续识别。"""
    text = message.strip()
    accepted: list[_Span] = []

    def take(start: int, end: int, code: str) -> None:
        if start >= end or any(s.start < end and start < s.end for s in accepted):
            return
        accepted.append(_Span(start, end, code))

    # 1. 本任务对照表：真实值 → 代号（“代号#序号”的键取 # 前的代号），长的优先
    mapping = [(key.split("#", 1)[0], value) for key, value in _load_full_map(db, task.id).items() if value]
    by_value = {_norm(value): code for code, value in mapping}
    for code, value in sorted(mapping, key=lambda kv: len(kv[1]), reverse=True):
        for start, end in _value_spans(text, value):
            take(start, end, code)

    # 2. 启用的已记住名称与自定义敏感词：对照表里有的用原代号，没有的用类别通用代号
    terms = [(crypto.decrypt_text(r.term_enc), r.category)
             for r in db.scalars(select(RedactionTerm).where(RedactionTerm.enabled.is_(True)))]
    for term, category in sorted(terms, key=lambda t: len(t[0]), reverse=True):
        code = by_value.get(_norm(term), _TERM_CODES.get(category, "[敏感词]"))
        for start, end in _value_spans(text, term):
            take(start, end, code)

    # 3. 9.3 第 4～7 步：格式化信息、金额、人名、地址
    def hit_code(hit: detect.Hit) -> str:
        known = by_value.get(_norm(text[hit.start:hit.end]))
        if known:
            return known
        if hit.category == "ADDR":
            return placeholders.address_code(hit.key) if hit.key else "[地址]"
        return _HIT_CODES.get(hit.category, "[敏感词]")

    for find in (detect.find_formatted, detect.find_amounts, detect.find_persons, detect.find_addresses):
        for hit in find(text):
            take(hit.start, hit.end, hit_code(hit))

    out, pos = [], 0
    for span in sorted(accepted, key=lambda s: s.start):
        out.append(text[pos:span.start])
        out.append(span.code)
        pos = span.end
    out.append(text[pos:])
    redacted = escape_document_tags("".join(out))
    return redacted, redacted != text


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
    redacted, changed = redact_question(db, task, message)
    return {"redacted": redacted, "changed": changed}


def send(db: Session, task: Task, user: User, message: str) -> dict:
    run = _require(db, task)
    question, _changed = redact_question(db, task, message)
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
    db.commit()
    logger.info("task %s 追问已回复 llm_call=%s", task.id, result.llm_call_id)
    mapping = load_redaction_map(db, task.id)
    return {"user": message_out(asked, mapping), "assistant": message_out(answer, mapping)}
