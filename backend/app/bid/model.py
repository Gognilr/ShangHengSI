"""标书模型核对（bid.md 3.2，B 段）：只把“模型判断”类要求与各家最相关的片段脱敏后发给模型。

流程：本地检查完成后在后台准备（preparing）——检索片段（bge-m3 向量 + 关键词）、只对片段跑完整脱敏，
投标人名称一律换成 [投标人A]……；用户在片段确认页核对后确认（awaiting → running）；按家合并调用，
每次调用都经模型网关（上限 → 出口复检 → 发送 → llm_calls，purpose bid）；引文校验后写入 bid_cells
（source model）与疑似不响应的问题条目。B 段失败只影响“模型判断”类单元格，A 段结果始终可用。

不发送：原件、图片、文件名、整份投标文件；日志只写 id 与数量。
"""
import json
import logging
import re
import uuid
from collections import defaultdict

from sqlalchemy import delete, select, update

from app.admin.settings_store import get_setting
from app.bid.requirements import _ROW
from app.db import jobs
from app.db.models import BidBidder, BidCell, BidItem, BidProject, BidRequirement, DocNode, Task, TaskClauseVector, TaskFile
from app.llm import gateway
from app.llm.usage import remaining_calls
from app.redaction import parties as P
from app.redaction.normalize import normalize_value
from app.redaction.recheck import _digit_bounded
from app.redaction.restore import restore
from app.review.validate import normalize_for_quote, quote_matches
from app.security import crypto
from app.security.errors import AppError

logger = logging.getLogger(__name__)
WINDOW = 400   # 检索片段：同一文件相邻节点合并到约 400 字，标题处断开，表格同一行不拆开
TOP = 3        # 每家、每条要求最多取 3 个片段
CHUNK = 20000  # 每次调用的字数上限（脱敏后）；一家的要求超过时分批
GROUP = {"qualification": "资格条件", "substantive": "实质性条款", "veto": "否决条款", "pricing": "报价要求",
         "validity": "有效期与保证金", "signing": "签章与格式页", "format": "格式范围"}
VERDICTS = {"响应": "yes", "疑似不响应": "no", "不响应": "no", "未找到": "missing", "无法判断": "unclear",
            "yes": "yes", "no": "no", "missing": "missing", "unclear": "unclear"}
# 不响应即可能否决投标的分组（参照第五十一条（三）（六））；其余为一般问题
_VETO = {"qualification": "（参照第五十一条（三））", "substantive": "（参照第五十一条（六））", "veto": "（参照第五十一条（六））"}
_GENERIC = {"投标", "标人", "招标", "标文", "文件", "要求", "必须", "应当", "不得", "提供", "具有", "按照", "规定", "符合",
            "的投", "人须", "人应", "须具", "人必", "本项", "项目", "以上", "以下", "相关", "进行", "其投", "将被", "被否",
            "否决", "参照", "条例", "第五", "五十", "十一", "一条", "实施", "招投", "标投", "投标法"}
_QUOTE_QUERY = re.compile(r"报价|税率|付款|完成时间|工期|交货|供货周期")
_QUOTE_CONTEXT = re.compile(r"^[^\n]{0,30}(?:报价单|投标一览表|开标一览表)\s*$", re.M)

SYSTEM = """你是招标采购的初审助手。用户给出招标文件的若干要求，以及在某一投标人的投标文件中检索到的相关片段。
所有内容已在本机脱敏，方括号中的代号（如 [投标人A]、[[ORG_1]]）请原样保留；投标人只以代号出现。
请逐条判断该投标人是否响应了每条要求，只依据给出的片段，不要推测片段以外的内容：
- 响应：片段中明确写出满足该要求的内容；
- 疑似不响应：片段中的内容与要求不符或明显低于要求（偏离表写“负偏离”也属此类）；
- 未找到：片段中没有与该要求相关的内容；
- 无法判断：片段相关但信息不足以判断。
先核对是否为本项目、同一设备位号及同一供货范围；历史业绩合同、其他项目和通用厂家资料不能替代本次响应。
条件条款先确认触发条件；比较数值须使用要求指定的分子、分母和单位，不能把额定流量替代最佳效率点流量。
没有明确完成时间、未检索到材料或未见附属设备数据，不等于存在相反承诺；证据不足时用“未找到”或“无法判断”，不要据此判“疑似不响应”。
要求中若混有编制、会签或日期等表头，不把表头日期当作技术响应条件；汇总材料须结合给出的多份文件，不能仅凭一份目录判缺失。
若偏离表（技术或商务偏离表）中的写法与正文其他片段矛盾，在 conflict 中写明；否则留空。
quote 必须从所引片段中逐字摘录（10～60 字）；“未找到”时 snippet 与 quote 留空。不要打分、排序或比较各投标人。
只输出 JSON：{"items":[{"req":"R1","verdict":"响应|疑似不响应|未找到|无法判断","snippet":"A1","quote":"逐字摘录","reason":"一句话理由","conflict":""}]}"""


# ---------------------------------------------------------------- 状态与入口

def _project(db, task):
    return db.get(BidProject, task.id)


def _state(project) -> dict:
    return json.loads(crypto.decrypt_text(project.model_enc)) if project.model_enc is not None else {}


def _save(project, state):
    project.model_enc = crypto.encrypt_text(json.dumps(state, ensure_ascii=False))


def model_requirements(db, task):
    return list(db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id, BidRequirement.enabled.is_(True),
                                                        BidRequirement.method == "model").order_by(BidRequirement.seq)))


def start(db, task, *, auto=False):
    """开始准备片段（后台检索与脱敏）；不提交。auto：本地检查完成时自动准备，条件不满足时静默跳过。"""
    project = _project(db, task)
    problem = None
    if task.policy == "local_only":
        problem = AppError(409, "LOCAL_ONLY", "本项目只做本地检查，不调用模型")
    elif project.check_state != "done" or project.stage != "checked":
        problem = AppError(409, "NOT_CHECKED", "本地检查完成后才能准备模型核对")
    elif not model_requirements(db, task):
        problem = AppError(422, "NO_MODEL_ROWS", "要求清单中没有“模型判断”类要求")
    elif project.model_state in ("preparing", "running"):
        problem = AppError(409, "MODEL_BUSY", "模型核对正在进行，请稍候")
    elif auto and project.model_state is not None:
        return
    if problem is not None:
        if auto:
            return
        raise problem
    project.model_state, project.model_error = "preparing", None
    jobs.enqueue(db, "bid", {"task_id": str(task.id), "stage": "snippets"})


def cancel(db, task):
    project = _project(db, task)
    if project.model_state in ("awaiting", "failed"):
        project.model_state, project.model_error = None, None


def confirm(db, task):
    """确认发送：按当前片段重新生成全部消息并逐批出口复检，任一批不过整轮不发；不提交。
    失败后再次确认只发送尚未完成的批次。"""
    project = _project(db, task)
    if task.policy == "local_only":
        raise AppError(409, "LOCAL_ONLY", "本项目只做本地检查，不调用模型")
    state = _state(project)
    if project.model_state not in ("awaiting", "failed") or not state.get("batches"):
        raise AppError(409, "MODEL_STATE", "请先等待片段准备完成")
    from app.models_api import select_model
    model = select_model(db, None, "review", structured=True)
    todo = [i for i in range(len(state["batches"])) if i not in state.get("done", [])]
    left = remaining_calls(db, task.owner_id, model.id)
    short = [v for v in left.values() if v is not None and v < len(todo)]
    if short:
        raise AppError(409, "CALLS_SHORT", f"本次需要 {len(todo)} 次调用，剩余 {min(short)} 次，额度不足，请联系管理员")
    try:
        for i in todo:
            gateway.precheck(db, model_id=model.id, purpose="bid", messages=messages(db, task, state, i),
                             user_id=task.owner_id, task_id=task.id, task_title=task.title)
    except gateway.GatewayError as exc:
        project.model_state = "failed"
        project.model_error = (f"出口复检拦截：{gateway.describe_reasons(exc.reasons)}" if exc.reasons else str(exc))[:200]
        return
    except _Stale:
        project.model_state, project.model_error = "failed", "片段有变化，请重新准备"
        return
    project.model_state, project.model_error = "running", None
    jobs.enqueue(db, "bid", {"task_id": str(task.id), "stage": "model"})


def view(db, task) -> dict:
    """片段确认页：各家的片段（原文与 AI 视野）、要求与回执。只在本人页面显示，不进日志。"""
    project = _project(db, task)
    state = _state(project)
    out = {"state": project.model_state, "error": project.model_error, "progress": progress(db, task),
           "receipt": None, "bidders": [], "requirements": []}
    if not state.get("batches") or project.model_state not in ("awaiting", "failed", "running", "done"):
        return out
    names = {str(b.id): crypto.decrypt_text(b.name_enc) for b in _bidders(db, task)}
    rows = {str(r.id): r for r in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id))}
    out["requirements"] = [{"id": rid, "n": r["n"], "group": r["group"], "text": crypto.decrypt_text(rows[rid].text_enc),
                            "ai_text": r["text"]} for rid, r in state["requirements"].items() if rid in rows]
    locs = [l for snips in state["snippets"].values() for s in snips for l in s["locs"]]
    nodes = _nodes_by_loc(db, task, locs)
    files = {str(f.id): f.original_name for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id))}
    original, redacted = {l: v[0] for l, v in nodes.items()}, {l: v[1] for l, v in nodes.items()}
    for bid, snips in state["snippets"].items():
        if bid not in names:
            continue
        links = state["links"].get(bid, {})
        out["bidders"].append({
            "id": bid, "name": names[bid], "code": state["codes"][bid],
            "snippets": [{"id": s["id"], "file": files.get(s["file_id"], "文件"), "page": s.get("page"),
                          "text": join(s["locs"], original),
                          "ai_text": join(s["locs"], redacted),
                          "requirements": [state["requirements"][rid]["n"] for rid, ids in links.items() if s["id"] in ids]}
                         for s in snips]})
    if project.model_state in ("awaiting", "failed"):
        out["receipt"] = receipt(db, task, state)
    return out


def receipt(db, task, state):
    todo = [i for i in range(len(state["batches"])) if i not in state.get("done", [])]
    from app.models_api import select_model
    try:
        model = select_model(db, None, "review", structured=True)
        left = remaining_calls(db, task.owner_id, model.id)
    except AppError:
        left = {"monthly": None, "daily": None}
    return {"snippets": sum(len(s) for s in state["snippets"].values()), "chars": state.get("chars", 0),
            "protected": state.get("protected", 0), "calls": len(todo), "total_calls": len(state["batches"]),
            "remaining": left, "notes": state.get("notes", [])}


def progress(db, task) -> dict:
    """{done, total}：已完成全部批次的投标人数 / 参与模型核对的投标人数（状态条“模型核对 3 / 5 家”）。"""
    project = _project(db, task)
    if project.model_enc is None or project.model_state is None:
        return {"done": 0, "total": 0}
    state = _state(project)
    per = defaultdict(list)
    for i, b in enumerate(state.get("batches", [])):
        per[b["bidder_id"]].append(i)
    done = set(state.get("done", []))
    return {"done": sum(all(i in done for i in idx) for idx in per.values()), "total": len(per)}


# ---------------------------------------------------------------- 材料

def _bidders(db, task):
    return list(db.scalars(select(BidBidder).where(BidBidder.task_id == task.id).order_by(BidBidder.seq)))


def _bid_nodes(db, task):
    """{bidder_id: [(file_id, seq, loc, kind, page, text)]}，按文件录入顺序与 ord。"""
    out = defaultdict(list)
    rows = db.execute(select(TaskFile.bidder_id, TaskFile.id, TaskFile.seq, DocNode.loc, DocNode.kind, DocNode.page, DocNode.text_enc)
                      .join(TaskFile, TaskFile.id == DocNode.file_id)
                      .where(DocNode.task_id == task.id, DocNode.part.in_(("body", "textbox")), TaskFile.bidder_id.is_not(None),
                             TaskFile.parser_version.is_not(None))
                      .order_by(TaskFile.seq, DocNode.ord))
    for bidder_id, file_id, seq, loc, kind, page, enc in rows:
        text = crypto.decrypt_text(enc) if enc is not None else ""
        if text.strip():
            out[bidder_id].append((file_id, seq, loc, kind, page, text))
    return out


def _nodes_by_loc(db, task, locs):
    """{loc: (原文, 脱敏文本)}。"""
    out = {}
    wanted = list(dict.fromkeys(locs))
    for start in range(0, len(wanted), 1000):
        for loc, enc, red in db.execute(select(DocNode.loc, DocNode.text_enc, DocNode.redacted_text)
                                        .where(DocNode.task_id == task.id, DocNode.loc.in_(wanted[start:start + 1000]))):
            out[loc] = (crypto.decrypt_text(enc) if enc is not None else "", red)
    return out


def passages(nodes):
    """把一家的节点合并成检索片段：[{file_id, seq, locs, page, section, text}]。"""
    out, current, section = [], None, ""
    for file_id, seq, loc, kind, page, text in nodes:
        row = _ROW.match(loc) if kind == "table_cell" else None
        same_row = row and current and current.get("row") == row.group(1)
        if kind == "heading" and len(text) <= 60:
            section = text.strip()
        if (current is None or current["file_id"] != file_id or current["page"] != page or kind == "heading"
                or (not same_row and len(current["text"]) + len(text) > WINDOW)):
            current = {"file_id": file_id, "seq": seq, "locs": [], "page": page, "section": section, "text": ""}
            out.append(current)
        current["locs"].append(loc)
        current["text"] += (" ｜ " if same_row else "\n" if current["text"] else "") + text
        current["row"] = row.group(1) if row else None
    for p in out:
        p.pop("row", None)
    return out


def _grams(text):
    t = re.sub(r"[\W_]", "", normalize_value(text or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)} - _GENERIC


def _embed(db, task, items):
    """片段向量写入 task_clause_vectors（file_seq 区分投标人文件）；位置相同的已有向量直接复用。
    向量模型不可用时返回 None，退回只按关键词检索。"""
    try:
        import numpy as np
        from app.embedding import engine
        model = engine.model_id()
        seqs = sorted({p["seq"] for p in items})
        stored = {(v.file_seq, tuple(v.locs)): v for v in db.scalars(select(TaskClauseVector).where(
            TaskClauseVector.task_id == task.id, TaskClauseVector.file_seq.in_(seqs)))}
        keep = {(p["seq"], tuple(p["locs"])) for p in items}
        stale = [v.id for k, v in stored.items() if k not in keep or v.model != model]
        if stale:
            db.execute(delete(TaskClauseVector).where(TaskClauseVector.id.in_(stale)))
        missing = [p for p in items if (p["seq"], tuple(p["locs"])) not in stored or stored[(p["seq"], tuple(p["locs"]))].id in stale]
        vectors = engine.embed([p["text"][:1000] for p in missing], "passage") if missing else []
        for p, v in zip(missing, vectors):
            db.add(TaskClauseVector(task_id=task.id, file_seq=p["seq"], first_loc=p["locs"][0][:20], locs=p["locs"],
                                    embedding=v, model=model))
        db.flush()
        found = {(v.file_seq, tuple(v.locs)): np.asarray(v.embedding, dtype=np.float32) for v in db.scalars(
            select(TaskClauseVector).where(TaskClauseVector.task_id == task.id, TaskClauseVector.file_seq.in_(seqs),
                                           TaskClauseVector.model == model))}
        return np.stack([found[(p["seq"], tuple(p["locs"]))] for p in items]) if items else None, engine
    except Exception:
        logger.warning("标书片段向量不可用，只按关键词检索 task=%s", task.id)
        db.rollback()
        return None, None


def retrieve(requirements, per_bidder, matrix=None, queries=None):
    """每家、每条要求取最相关的片段：分数 = 向量余弦 + 0.6 × 关键词二元组覆盖率（偏离表略加分）。
    requirements: [(rid, 原文)]；per_bidder: {bidder: [passage]}；matrix: {bidder: 片段向量}；queries: {rid: 向量}。"""
    links = {}
    for bid, items in per_bidder.items():
        grams = [_grams(p["text"]) for p in items]
        links[bid] = {}
        for rid, text in requirements:
            want = _grams(text)
            quote = {i for i, p in enumerate(items) if _QUOTE_CONTEXT.search(p['text'])} if _QUOTE_QUERY.search(text) else set()
            scores = []
            for i, p in enumerate(items):
                kw = len(want & grams[i]) / len(want) if want else 0.0
                score = 0.6 * kw + (0.05 if kw and "偏离" in p["section"] else 0.0)
                if matrix is not None and matrix.get(bid) is not None and queries is not None:
                    score += float(matrix[bid][i] @ queries[rid])
                elif kw == 0 and i not in quote:
                    continue
                scores.append((score, i))
            scores.sort(key=lambda s: (-s[0], s[1]))
            best = scores[:TOP]
            if best:
                best = [s for s in best if s[0] >= best[0][0] * 0.8]  # 明显不如第一名的不带上
            # 工期、税率等常在本次报价页；留一个明确报价上下文，防止历史合同包揽前三名。
            anchor = next((s for s in scores if s[1] in quote), None)
            if anchor is not None and anchor[1] not in {i for _s, i in best}:
                best = [*best[:TOP - 1], anchor]
            links[bid][rid] = [i for _s, i in best]
    return links


# ---------------------------------------------------------------- 片段脱敏

def _names(bidder):
    from app.bid.checks import short_names
    full = crypto.decrypt_text(bidder.name_enc)
    return full, sorted(short_names(full), key=len, reverse=True)


def _sweep(text, pairs, codes=None):
    """兜底：把仍以原文出现的投标人名称、对照表中的真实值换成代号；返回 (文本, 替换数)。
    数字开头或结尾的真实值按出口复检的数字边界匹配（旧金额 1000 不动“10000次”）；已有代号内部不替换。"""
    count = 0
    for value, code in pairs:
        if value not in text:
            continue
        inside = [m.span() for m in codes.finditer(text)] if codes is not None else []
        digit = value[0].isdigit() or value[-1].isdigit()
        hits = [m.span() for m in re.finditer(re.escape(value), text)
                if not any(s < m.end() and m.start() < e for s, e in inside)
                and (not digit or _digit_bounded(text, m.start(), m.end()))]
        for start, end in reversed(hits):
            text = text[:start] + code + text[end:]
        count += len(hits)
    return text, count


def _close_snippets(db, task, groups, texts, mapping):
    """按实际拼接语境补齐遗漏，再把替换投影回各节点；原节点与拼接分隔符不移动。
    跨节点命中分别登记各节点的原文字段，保证每个代号仍能在本机还原。"""
    from app.redaction.placeholders import NeutralAllocator
    from app.redaction.normalize import normalize_with_map
    from app.redaction.recheck import SegmentChecker, recheck_context
    ctx = recheck_context(db, task.id)
    protected = 0
    for _ in range(3):  # 与通用脱敏闭环相同；最终发送仍由网关独立复检
        alloc = NeutralAllocator()
        alloc.seed(mapping)
        codes = [k for k in mapping if not k.startswith('@') and '#' not in k]
        marks = re.compile('|'.join(map(re.escape, sorted(codes, key=len, reverse=True)))) if codes else None
        checker = SegmentChecker(real_values=[v for k, v in mapping.items() if not k.startswith('@')],
                                 terms=ctx.terms, placeholders=codes, kept=ctx.kept)
        patterns = {category: [P.name_pattern(normalize_value(v)) for v in values if len(v.strip()) >= 2]
                    for category, values in [('MAP', [v for k, v in mapping.items() if not k.startswith('@')]),
                                             ('TERM', ctx.terms)]}
        changed = 0
        for locs in groups:
            text, offsets = _joined(locs, texts)
            inside = [m.span() for m in marks.finditer(text)] if marks else []
            edits = defaultdict(list)
            normalized = normalize_with_map(text)
            for reason in checker(text):
                start, end = reason.position, min(len(text), reason.end)
                if reason.category in patterns:
                    # MAP / TERM 的 end 只是预览约数；必须重新取得精确区间，不能据约数删掉邻接正文。
                    ends = [normalized.index_map[m.end() - 1] + 1
                            for pattern in patterns[reason.category] for m in pattern.finditer(normalized.text)
                            if m.end() > m.start() and normalized.index_map[m.start()] == start]
                    if not ends:
                        continue  # 无精确匹配时交给最终网关拦截，不猜测替换范围
                    end = max(ends)
                if end <= start or any(s < end and start < e for s, e in inside):
                    continue  # 不拆开已有代号；跨代号的问题保留给网关拦截
                category = reason.category if reason.category not in ('MAP', 'TERM', 'INVISIBLE') else 'CUSTOM'
                for loc, a, b in offsets:
                    lo, hi = max(start, a), min(end, b)
                    if lo < hi:
                        edits[loc].append((lo - a, hi - a, category))
            for loc, spans in edits.items():
                merged = []
                for start, end, category in sorted(set(spans)):
                    if merged and start < merged[-1][1]:
                        a, b, c = merged[-1]
                        merged[-1] = (a, max(b, end), c)
                    else:
                        merged.append((start, end, category))
                for start, end, category in reversed(merged):
                    surface = texts[loc][start:end]
                    code = alloc.numbered(category, surface)
                    mapping.setdefault(code, surface)
                    if surface != mapping[code] and surface not in mapping.values():
                        n = 1
                        while f'{code}#{n}' in mapping:
                            n += 1
                        mapping[f'{code}#{n}'] = surface
                    texts[loc] = texts[loc][:start] + code + texts[loc][end:]
                    changed += 1
        protected += changed
        if not changed:
            break
    return protected


def redact(db, task, bidders, nodes: dict, req_texts: dict, groups=None):
    """只对待发送的片段节点与要求文字跑完整脱敏（规则 + 实体识别 + 词条）。
    nodes: {loc: 原文}；req_texts: {rid: 原文}。返回 ({loc: 脱敏文本}, {rid: 脱敏文本}, 本机保护处数)。
    对照表只增：沿用任务已有对照表（招标文件的代号不变），新增代号用 [[类别_n]] 形式，投标人统一为 [投标人A]。"""
    from app.redaction import engine as E
    from app.redaction import keep_terms
    old = E._load_full_map(db, task.id)
    names = {b.id: _names(b) for b in bidders}
    user_terms = [E.UserTerm(n, "ORG") for full, short in names.values() for n in dict.fromkeys([full, *short])]
    req_locs = {rid: f"REQ-{i}" for i, rid in enumerate(req_texts, 1)}
    node_in = [E.NodeIn(loc, text) for loc, text in nodes.items()] + [E.NodeIn(req_locs[rid], t) for rid, t in req_texts.items()]
    result = E.compute(node_in, task.stance, terms=E._load_terms(db, task.owner_id), user_terms=user_terms,
                       mask_quantity=bool(get_setting(db, "redaction.mask_quantity")), style="neutral", pinned=old,
                       ratio=False, keeps=list(keep_terms.effective(db, task.owner_id).values()))
    mapping, texts = dict(result.mapping), dict(result.redacted)
    sweep = []
    for b in bidders:
        full, short = names[b.id]
        code = f"[投标人{b.code}]"
        wanted = {P.normalize(n) for n in (full, *short)}
        codes = [k for k, v in mapping.items() if "#" not in k and k.startswith("[[") and P.normalize(v) in wanted]
        surfaces = []
        for c in codes:
            aliases = [k for k in mapping if k.startswith(c + "#")]
            surfaces += [mapping[c]] + [mapping[k] for k in aliases]
            for k in [c, *aliases]:  # 对照表只增：已落库的旧代号保留（旧文本仍能还原），本次新建的才去掉
                if k not in old:
                    mapping.pop(k)
            texts = {loc: t.replace(c, code) for loc, t in texts.items()}
        mapping[code] = full
        have = {v for k, v in mapping.items() if k.startswith(code + "#")}
        seq = 0
        for value in dict.fromkeys(v for v in [*surfaces, *short] if v != full and v not in have):
            seq += 1
            while f"{code}#{seq}" in mapping:
                seq += 1
            mapping[f"{code}#{seq}"] = value
        sweep += [(n, code) for n in (full, *short)]
    for key, value in old.items():  # 招标文件对照表中的真实值在片段里原样出现时也换成同一代号
        if not key.startswith("@") and "#D" not in key and len(value) >= 2 and not re.fullmatch(r"[\d.,，]{1,3}", value):
            sweep.append((value, key.split("#", 1)[0]))
    sweep.sort(key=lambda p: -len(p[0]))
    marks = sorted({k.split("#", 1)[0] for k in mapping if not k.startswith("@")}, key=len, reverse=True)
    marks = re.compile("|".join(map(re.escape, marks))) if marks else None
    protected = len(result.items)
    for loc in texts:
        texts[loc], n = _sweep(texts[loc], sweep, marks)
        protected += n
    protected += _close_snippets(db, task, groups or [[loc] for loc in nodes], texts, mapping)
    E.write_full_map(db, task.id, mapping)
    by_loc = {loc: texts[loc] for loc in nodes}
    return by_loc, {rid: texts[req_locs[rid]] for rid in req_texts}, protected


# ---------------------------------------------------------------- 准备（后台）

def prepare(db, task):
    """检索片段并脱敏，写入待确认状态；不提交。"""
    from app.bid.checks import ocr_progress
    project = _project(db, task)
    rows = model_requirements(db, task)
    bidders = [b for b in _bidders(db, task)]
    nodes = _bid_nodes(db, task)
    bidders = [b for b in bidders if nodes.get(b.id)]
    per = {b.id: passages(nodes[b.id]) for b in bidders}
    texts = {str(r.id): crypto.decrypt_text(r.text_enc) for r in rows}
    flat = [p for b in bidders for p in per[b.id]]
    matrix, engine = _embed(db, task, flat)
    notes = []
    queries = None
    if matrix is not None:
        split, offset = {}, 0
        for b in bidders:
            split[b.id] = matrix[offset:offset + len(per[b.id])]
            offset += len(per[b.id])
        matrix = split
        queries = dict(zip(texts, engine.embed(list(texts.values()), "query")))
    else:
        notes.append("向量模型不可用，片段只按关键词检索，可能漏掉写法不同的内容")
    links = retrieve(list(texts.items()), per, matrix, queries)
    # 去重编号：同一家的同一片段只发一次
    snippets, link_ids, chosen = {}, {}, {}
    for b in bidders:
        used = sorted({i for ids in links[b.id].values() for i in ids})
        ids = {i: f"{b.code}{n}" for n, i in enumerate(used, 1)}
        snippets[str(b.id)] = [{"id": ids[i], "file_id": str(per[b.id][i]["file_id"]), "page": per[b.id][i]["page"],
                                "locs": per[b.id][i]["locs"]} for i in used]
        link_ids[str(b.id)] = {rid: [ids[i] for i in got] for rid, got in links[b.id].items()}
        wanted = {loc for i in used for loc in per[b.id][i]["locs"]}
        chosen.update({loc: t for _f, _s, loc, _k, _p, t in nodes[b.id] if loc in wanted})
    # 只对待发送片段脱敏：先清掉上一次的片段脱敏结果
    db.execute(update(DocNode).where(DocNode.task_id == task.id, DocNode.redacted_text.is_not(None),
                                     DocNode.file_id.in_(select(TaskFile.id).where(TaskFile.task_id == task.id,
                                                                                   TaskFile.bidder_id.is_not(None))))
               .values(redacted_text=None))
    groups = [s['locs'] for items in snippets.values() for s in items]
    red_nodes, red_reqs, protected = redact(db, task, bidders, chosen, texts, groups)
    from app.redaction.engine import _write_redacted
    locs = list(red_nodes)
    ids = {}
    for start in range(0, len(locs), 1000):
        ids.update(db.execute(select(DocNode.loc, DocNode.id).where(DocNode.task_id == task.id,
                                                                    DocNode.loc.in_(locs[start:start + 1000]))).all())
    _write_redacted(db, {ids[loc]: text for loc, text in red_nodes.items() if loc in ids})
    requirements = {str(r.id): {"n": f"R{i}", "group": r.group, "text": red_reqs[str(r.id)]} for i, r in enumerate(rows, 1)}
    state = {"requirements": requirements, "snippets": snippets, "links": link_ids,
             "codes": {str(b.id): f"[投标人{b.code}]" for b in bidders}, "protected": protected, "done": []}
    state["batches"] = _batches(state, {loc: t for loc, t in red_nodes.items()})
    state["chars"] = sum(len(m["content"]) for i in range(len(state["batches"])) for m in _messages(state, i, red_nodes))
    done, total = ocr_progress({f.id: f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id))})
    if total > done:
        notes.append(f"扫描页识别中 {done} / {total}，尚未识别的页不在检索范围内；识别完成后可“重新准备”")
    state["notes"] = notes
    _save(project, state)
    project.model_state, project.model_error = "awaiting", None
    logger.info("标书模型核对片段已准备 task=%s 投标人=%s 片段=%s 批次=%s", task.id, len(bidders),
                sum(len(s) for s in snippets.values()), len(state["batches"]))


def _joined(locs, texts):
    """实际片段及节点在其中的位置；表格同一行的单元格用“｜”连接，其余分行。"""
    out, last, offsets = "", None, []
    for loc in locs:
        row = _ROW.match(loc)
        key = row.group(1) if row else None
        sep = " ｜ " if key and key == last else "\n"
        out += sep if out else ""
        start = len(out)
        out += texts.get(loc) or ""
        offsets.append((loc, start, len(out)))
        last = key
    return out, offsets


def join(locs, texts):
    return _joined(locs, texts)[0]


def _snippet_text(snippet, red):
    return join(snippet["locs"], red)


def _batches(state, red):
    """按家合并：一家的全部要求尽量一次发完，超过字数上限时分批。"""
    out = []
    for bid, links in state["links"].items():
        sizes = {s["id"]: len(_snippet_text(s, red)) + 8 for s in state["snippets"][bid]}
        current, size, seen = None, 0, set()
        for rid in state["requirements"]:
            ids = links.get(rid, [])
            cost = len(state["requirements"][rid]["text"]) + 40 + sum(sizes[i] for i in ids if i not in seen)
            if current is None or (current["reqs"] and size + cost > CHUNK):
                current, size, seen = {"bidder_id": bid, "reqs": [], "snippets": []}, 0, set()
                out.append(current)
                cost = len(state["requirements"][rid]["text"]) + 40 + sum(sizes[i] for i in ids)
            current["reqs"].append(rid)
            current["snippets"] += [i for i in ids if i not in seen]
            seen.update(ids)
            size += cost
    return out


class _Stale(Exception):
    pass


def _messages(state, index, red):
    batch = state["batches"][index]
    bid = batch["bidder_id"]
    parts = sum(1 for b in state["batches"] if b["bidder_id"] == bid)
    number = sum(1 for b in state["batches"][:index] if b["bidder_id"] == bid) + 1
    snippets = {s["id"]: s for s in state["snippets"][bid]}
    lines = [f"投标人代号：{state['codes'][bid]}" + (f"（第 {number} / {parts} 批）" if parts > 1 else ""), "", "【要求】"]
    for rid in batch["reqs"]:
        r = state["requirements"][rid]
        ids = state["links"][bid].get(rid, [])
        lines.append(f"[{r['n']}]（{GROUP.get(r['group'], r['group'])}）{r['text']}")
        lines.append("  相关片段：" + ("、".join(ids) if ids else "（未检索到相关片段）"))
    lines += ["", "【投标文件片段】"]
    for sid in batch["snippets"]:
        text = _snippet_text(snippets[sid], red)
        if any(red.get(l) is None for l in snippets[sid]["locs"]):
            raise _Stale
        lines.append(f"[{sid}] {text}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def messages(db, task, state, index):
    """由数据库中的片段脱敏文本重新生成第 index 批的消息（确认与发送时都重新生成）。"""
    snippets = {s["id"]: s for s in state["snippets"][state["batches"][index]["bidder_id"]]}
    locs = [l for sid in state["batches"][index]["snippets"] for l in snippets[sid]["locs"]]
    red = {loc: pair[1] for loc, pair in _nodes_by_loc(db, task, locs).items()}
    return _messages(state, index, red)


# ---------------------------------------------------------------- 结果

def _parse(content):
    data = json.loads(content)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError
    return items


def apply(db, task, state, index, items):
    """校验一批的模型输出并写入单元格与问题条目；返回写入的单元格数。不提交。
    引文须出自所引片段（quote 校验），对不上的仍记录判断，标“待核实”。"""
    from app.redaction import engine as E
    batch = state["batches"][index]
    bid = batch["bidder_id"]
    bidder_uuid = uuid.UUID(bid)
    snippets = {s["id"]: s for s in state["snippets"][bid] if s["id"] in batch["snippets"]}
    by_n = {state["requirements"][rid]["n"]: rid for rid in batch["reqs"]}
    locs = [l for s in snippets.values() for l in s["locs"]]
    nodes = _nodes_by_loc(db, task, locs)
    pages = dict(db.execute(select(DocNode.loc, DocNode.page).where(DocNode.task_id == task.id, DocNode.loc.in_(locs))).all())
    mapping = E.load_redaction_map(db, task.id)
    reqs = {str(r.id): r for r in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id))}
    old_cells = {(c.requirement_id, c.bidder_id): c for c in db.scalars(select(BidCell).where(
        BidCell.task_id == task.id, BidCell.bidder_id == bidder_uuid, BidCell.source == "model"))}
    old_items = {}
    for it in db.scalars(select(BidItem).where(BidItem.task_id == task.id, BidItem.source == "model")):
        if it.bidder_ids == [bid] and str(it.requirement_id) in batch["reqs"]:
            old_items[(it.type, str(it.requirement_id))] = (it.review_status, it.note_enc)
            db.delete(it)
    written = 0
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        rid = by_n.get(str(item.get("req") or "").strip("[] "))
        verdict = VERDICTS.get(str(item.get("verdict") or "").strip())
        if rid is None or verdict is None or rid in seen or rid not in reqs:
            continue
        seen.add(rid)
        raw_ids = item.get("snippet") or []
        cited = [s.strip("[] ") for s in (raw_ids if isinstance(raw_ids, list) else re.split(r"[,，、\s]+", str(raw_ids)))]
        cited = [s for s in cited if s in snippets]
        quote = str(item.get("quote") or "").strip()
        where, cite, source = [], None, ""
        if verdict != "missing":
            cite = "unverified"
            for sid in cited:
                locs_ = snippets[sid]["locs"]
                hit = next((l for l in locs_ if l in nodes and _cites(quote, nodes[l][1] or "")), None)
                if hit is not None:
                    source = nodes[hit][0]
                elif _cites(quote, join(locs_, {l: v[1] for l, v in nodes.items()})):
                    hit, source = locs_[0], join(locs_, {l: v[0] for l, v in nodes.items()})
                if hit:
                    cite, where = "verified", [hit]
                    break
            if not where and cited:
                where = [snippets[cited[0]]["locs"][0]]
                source = nodes.get(where[0], ("", ""))[0]
        place = [{"file_id": snippets_file(snippets, l), "loc": l, "page": pages.get(l)} for l in where]
        reason = restore(str(item.get("reason") or "").strip(), mapping)[:500]
        cell = old_cells.pop((uuid.UUID(rid), bidder_uuid), None)
        if cell is None:
            cell = BidCell(task_id=task.id, requirement_id=uuid.UUID(rid), bidder_id=bidder_uuid, source="model")
            db.add(cell)
        cell.verdict, cell.locs, cell.cite = verdict, place, cite
        cell.reason_enc = crypto.encrypt_text(reason or "（模型未给出理由）")
        written += 1
        # 证据摘录优先取本机原文：还原后的引文逐字出现在原文中才用它，否则用所引节点的原文
        excerpt = restore(quote, mapping)
        if cite != "verified" or not excerpt or normalize_for_quote(excerpt) not in normalize_for_quote(source):
            excerpt = source
        excerpt = excerpt[:200]
        group = reqs[rid].group
        text = crypto.decrypt_text(reqs[rid].text_enc)
        rows = [{"label": "判断理由", "value": reason}] if reason else []
        if place:
            rows.append({"label": "所引片段" + ("（引文未能在片段中核实）" if cite == "unverified" else ""),
                         "places": {bid: [{**place[0], "excerpt": excerpt}]}})
        if verdict == "no":
            _model_item(db, task, bid, rid, "model_response", "veto" if group in _VETO else "general",
                        _VETO.get(group), f"疑似未响应：{_short(text)}", rows + [{"label": "要求", "value": text}], old_items)
        conflict = restore(str(item.get("conflict") or "").strip(), mapping)
        if conflict:
            _model_item(db, task, bid, rid, "deviation_conflict", "general", None, f"偏离表与正文不一致：{_short(conflict)}",
                        [{"label": "说明", "value": conflict[:500]}] + rows[1:], old_items)
    return written


_MARK = re.compile(r"\[\[[A-Z]+_\d+\]\]|\[投标人[A-Z]+\]")


def _cites(quote, text):
    """引文出自片段：整体相似，且引文中的代号（投标人、[[类别_n]]）都在片段中——换了代号的引文还原后会指向另一家。"""
    if not quote or not quote_matches(quote, text):
        return False
    return set(_MARK.findall(normalize_for_quote(quote))) <= set(_MARK.findall(normalize_for_quote(text)))


def snippets_file(snippets, loc):
    return next(s["file_id"] for s in snippets.values() if loc in s["locs"])


def _short(text, n=60):
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n] + "…"


def _model_item(db, task, bid, rid, type_, level, article, title, rows, old):
    status, note = old.get((type_, rid), ("pending", None))
    db.add(BidItem(task_id=task.id, kind="issue", bidder_ids=[bid], type=type_, level=level, article=article,
                   requirement_id=uuid.UUID(rid), review_status=status, note_enc=note, source="model",
                   evidence_enc=crypto.encrypt_text(json.dumps({"title": title, "rows": rows}, ensure_ascii=False))))


def _fail(db, task_id, message):
    db.rollback()
    project = db.get(BidProject, task_id, populate_existing=True)
    if project is not None:
        project.model_state, project.model_error = "failed", message[:200]
        db.commit()


def run(db, task):
    """逐批发送；每批写入后提交（状态条“模型核对 n / N 家”随之更新）。任一批失败即停，已完成的批次保留。"""
    from app.models_api import select_model
    task_id = task.id
    try:
        model = select_model(db, None, "review", structured=True)
    except AppError as exc:
        _fail(db, task_id, str(exc))
        return
    project = _project(db, task)
    state = _state(project)
    db.commit()
    for index in range(len(state["batches"])):
        if index in state.get("done", []):
            continue
        try:
            outcome = gateway.call_model(db, model_id=model.id, purpose="bid", messages=messages(db, task, state, index),
                                         user_id=task.owner_id, task_id=task.id, task_title=task.title, json_mode=True)
            items = _parse(outcome.content)
        except gateway.GatewayError as exc:
            _fail(db, task_id, f"出口复检拦截：{gateway.describe_reasons(exc.reasons)}" if exc.reasons else str(exc))
            return
        except _Stale:
            _fail(db, task_id, "片段有变化，请重新准备")
            return
        except (ValueError, TypeError, AttributeError):
            _fail(db, task_id, "模型返回的内容无法识别，可再次确认继续未完成的部分")
            return
        project = db.get(BidProject, task_id, populate_existing=True, with_for_update=True)
        if project is None or project.model_state != "running":
            db.rollback()
            return
        state = _state(project)
        written = apply(db, task, state, index, items)
        state.setdefault("done", []).append(index)
        _save(project, state)
        db.commit()
        logger.info("标书模型核对一批完成 task=%s 批次=%s 单元格=%s", task_id, index + 1, written)
    project = db.get(BidProject, task_id, populate_existing=True)
    if project is not None and project.model_state == "running":
        project.model_state, project.model_error = "done", None
        db.commit()


def handle(db, task, project, stage):
    """worker 入口（extract.handle_bid 分派）：snippets 准备片段，model 发送。"""
    if stage == "snippets":
        if project.model_state != "preparing":
            return
        try:
            prepare(db, task)
            db.commit()
        except Exception:
            logger.warning("标书模型核对片段准备失败 task=%s", task.id)
            _fail(db, task.id, "片段准备失败，请重试")
        return
    if stage == "model" and project.model_state == "running" and task.policy != "local_only":
        run(db, task)
