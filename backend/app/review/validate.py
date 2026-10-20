"""模型输出校验（SPEC 11.4 全部 9 条，设计 docs/design/M2.md 第八节）。

validate_output() 是纯函数：输入模型原文、审核上下文和本任务正文节点，输出可直接入库的 ValidatedReview
（类别、严重程度、结论已映射为数据库代码，文字仍为代号形式）。结构不对时抛出 OutputError。
review_once() 调用模型网关；输出无法解析或不符合结构时，把错误说明加入消息重试 1 次（11.4 第 1 条）。
分段审核（M5，设计 docs/design/M5.md 第三、五节）：validate_output 传入分段时只保留本段内的位置；
merge_reviews 按 11.5 合并各段结果；报价明细表汇总行的位置映射到表头行第一个单元格（Q-72 ⑥）。
"""
import difflib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocNode, ReviewRun, TaskFile
from app.llm.gateway import call_model
from app.parsing.model import with_clause_no
from app.redaction.quotes import QuoteTable, summary_text
from app.redaction.restore import restore
from app.review.chunking import Chunk
from app.review.prompt import ReviewContext, build_messages
from app.review.schema import FindingOut, ModelOutput
from app.security import crypto

CATEGORY_MAP = {"表述": "wording", "逻辑": "logic", "法律": "legal", "合规与制裁": "compliance", "商务": "commercial"}
SEVERITY_MAP = {"高": "high", "中": "medium", "低": "low"}
SANCTIONS_MAP = {"无明显风险": "none", "需注意": "attention", "高风险": "high"}
VERDICTS = ("issue", "ok", "na", "unable")
_VERDICT_ALIASES = {"有问题": "issue", "未发现问题": "ok", "不适用": "na", "无法判断": "unable"}
# 同一要求出现多条结论时的取舍顺序（与 11.5 分段合并规则一致）
_VERDICT_RANK = {"issue": 3, "unable": 2, "ok": 1, "na": 0}
_SEVERITY_RANK = {"high": 2, "medium": 1, "low": 0}
_CITE_RANK = {"verified": 2, "missing": 1, "unverified": 0}
MISSING_NOTE = "模型未返回结论"
QUOTE_SIMILARITY = 0.9
TITLE_SIMILARITY = 0.8
TITLE_MAX = 200
_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)
_SPACES = re.compile(r"\s+")


class OutputError(Exception):
    """模型输出无法解析或不符合结构。消息是给模型看的错误说明（代号形式，不含原文）。

    review_once 最终失败时附带已消耗的 token 数和 llm_calls 记录 id，供轮次累计用量。
    """

    tokens_in: int = 0
    tokens_out: int = 0
    llm_call_ids: list[int] = []


@dataclass
class DocNodeView:
    loc: str
    redacted_text: str
    text: str  # 原文（解密后）
    clause_no: str | None = None
    target_loc: str | None = None  # 报价明细表汇总行：入库时换成的位置（表头行第一个单元格）


_SANCTIONS_RANK = {"high": 2, "attention": 1, "none": 0}


@dataclass
class RequirementResultView:
    req_id: str
    req_source: str
    req_text: str
    dimension: str | None
    verdict: str
    note: str | None
    returned: bool = True  # False：模型没有给出、按第 6 条补记的 unable


@dataclass
class FindingView:
    title: str
    category: str
    severity: str
    req_ids: list[str]
    locs: list[str]
    missing: bool
    cite_status: str  # verified / unverified / missing
    quote_model: str | None
    quote_snapshot: str | None  # 原文快照（明文，入库时加密）
    problem: str
    impact: str | None
    suggestion: str | None
    suggested_text: str | None
    basis: str | None


@dataclass
class ValidatedReview:
    doc_summary: str
    requirement_results: list[RequirementResultView]
    findings: list[FindingView]
    sanctions_level: str | None
    sanctions_reasons: list[str]
    tokens_in: int = 0
    tokens_out: int = 0
    llm_call_ids: list[int] = field(default_factory=list)


# ---------------------------------------------------------------- 取值映射


def map_category(value: str) -> str:
    """11.4 第 2 条：不在约束范围内的类别记为最接近的一类，无法判断时记为 commercial。"""
    v = (value or "").strip()
    if v in CATEGORY_MAP:
        return CATEGORY_MAP[v]
    if v.lower() in CATEGORY_MAP.values():
        return v.lower()
    for keywords, code in ((("合规", "制裁", "管制", "compliance", "sanction"), "compliance"),
                           (("法", "legal", "law"), "legal"),
                           (("逻辑", "一致", "矛盾", "logic"), "logic"),
                           (("表述", "措辞", "文字", "用语", "wording"), "wording")):
        if any(k in v.lower() for k in keywords):
            return code
    return "commercial"


def map_severity(value: str) -> str:
    v = (value or "").strip()
    if v in SEVERITY_MAP:
        return SEVERITY_MAP[v]
    return v.lower() if v.lower() in SEVERITY_MAP.values() else "medium"


def map_verdict(value: str) -> str | None:
    v = (value or "").strip()
    if v.lower() in VERDICTS:
        return v.lower()
    return _VERDICT_ALIASES.get(v)


def map_sanctions(value: str) -> str | None:
    v = (value or "").strip()
    if v in SANCTIONS_MAP:
        return SANCTIONS_MAP[v]
    return v.lower() if v.lower() in SANCTIONS_MAP.values() else None


# ---------------------------------------------------------------- 引用校验


def normalize_for_quote(text: str) -> str:
    """去掉空白、统一全半角（NFKC）后比较。"""
    return _SPACES.sub("", unicodedata.normalize("NFKC", text or ""))


def quote_matches(quote: str, text: str) -> bool:
    """quote 是 text 的子串，或与 text 中某一段的 difflib 相似度 ≥ 0.9。"""
    q, t = normalize_for_quote(quote), normalize_for_quote(text)
    if not q or not t:
        return False
    if q in t:
        return True
    n = len(q)
    if len(t) <= n:
        return difflib.SequenceMatcher(None, q, t).ratio() >= QUOTE_SIMILARITY
    matcher = difflib.SequenceMatcher(None, "", q)  # b 固定为 quote（difflib 缓存的是 b）
    for i in range(len(t) - n + 1):
        matcher.set_seq1(t[i:i + n])
        if matcher.real_quick_ratio() >= QUOTE_SIMILARITY and matcher.quick_ratio() >= QUOTE_SIMILARITY \
                and matcher.ratio() >= QUOTE_SIMILARITY:
            return True
    return False


def _cite_status(f: FindingOut, locs: list[str], nodes: dict[str, DocNodeView]) -> str:
    if f.missing:
        return "missing"
    if not locs or not f.quote:
        return "unverified"
    texts = [with_clause_no(nodes[loc].redacted_text, nodes[loc].clause_no) for loc in locs]
    candidates = list(texts)
    if len(texts) > 1:  # 引用可能跨越多个位置：带编号和不带编号两种拼法都比较
        candidates += ["".join(texts), "".join(nodes[loc].redacted_text for loc in locs)]
    return "verified" if any(quote_matches(f.quote, t) for t in candidates) else "unverified"


# ---------------------------------------------------------------- 主流程


def strip_fence(raw: str) -> str:
    m = _FENCE.match(raw or "")
    return m.group(1) if m else (raw or "").strip()


def parse_output(raw: str) -> ModelOutput:
    try:
        data = json.loads(strip_fence(raw))
    except json.JSONDecodeError as exc:
        raise OutputError(f"输出不是合法的 JSON（第 {exc.lineno} 行第 {exc.colno} 列）") from None
    if not isinstance(data, dict):
        raise OutputError("输出必须是一个 JSON 对象")
    try:
        return ModelOutput.model_validate(data)
    except ValidationError as exc:
        problems = "；".join(f"{'.'.join(str(p) for p in e['loc'])}：{e['msg']}" for e in exc.errors()[:10])
        raise OutputError(f"输出不符合规定结构：{problems}") from None


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_for_quote(a), normalize_for_quote(b)).ratio()


def _merge_duplicates(findings: list[FindingView]) -> list[FindingView]:
    """11.4 第 7 条：同一位置、同一类别且标题相似度 ≥ 0.8 的问题合并为一条（保留第一条，合并要求和位置，取较高严重程度）。"""
    out: list[FindingView] = []
    for f in findings:
        same = next((g for g in out if g.category == f.category
                     and ((set(g.locs) & set(f.locs)) or (not g.locs and not f.locs))
                     and _similar(g.title, f.title) >= TITLE_SIMILARITY), None)
        if same is None:
            out.append(f)
            continue
        same.req_ids += [r for r in f.req_ids if r not in same.req_ids]
        same.locs += [loc for loc in f.locs if loc not in same.locs]
        if _SEVERITY_RANK[f.severity] > _SEVERITY_RANK[same.severity]:
            same.severity = f.severity
        if _CITE_RANK[f.cite_status] > _CITE_RANK[same.cite_status]:
            same.cite_status, same.quote_model, same.quote_snapshot = f.cite_status, f.quote_model, f.quote_snapshot
            # 升级引用时，首个定位随快照一起采用已校验问题的位置（A-02）；其他位置仍保留。
            same.locs = list(dict.fromkeys(f.locs + same.locs))
    return out


def validate_output(raw: str, ctx: ReviewContext, nodes: dict[str, DocNodeView],
                    chunk: Chunk | None = None) -> ValidatedReview:
    """按 11.4 校验并整理模型输出。nodes 为本任务的正文节点（loc → 视图，含报价明细表汇总行）。

    chunk 不为 None 时，第 3 条只保留本段内的位置编号。
    """
    allowed = nodes if chunk is None else {loc: v for loc, v in nodes.items() if loc in chunk.locs}
    out = parse_output(raw)
    sent = {r.id: r for r in ctx.requirements}

    # 第 6 条：每条发送的要求恰好一条结论；未发送的要求编号丢弃；同一要求多条时取 issue > unable > ok > na
    verdicts: dict[str, tuple[str, str | None]] = {}
    for rr in out.requirement_results:
        req_id = rr.req_id.strip()
        verdict = map_verdict(rr.verdict)
        if req_id not in sent or verdict is None:
            continue
        old = verdicts.get(req_id)
        if old is None or _VERDICT_RANK[verdict] > _VERDICT_RANK[old[0]]:
            verdicts[req_id] = (verdict, rr.note)
    results = []
    for req in ctx.requirements:
        returned = req.id in verdicts
        verdict, note = verdicts.get(req.id, ("unable", MISSING_NOTE))
        results.append(RequirementResultView(req.id, req.source, req.text, req.dimension, verdict, note, returned))

    findings = []
    for f in out.findings:
        locs = list(dict.fromkeys(loc.strip() for loc in f.locs if loc.strip() in allowed))  # 第 3 条
        status = _cite_status(f, locs, nodes)  # 第 4 条
        snapshot = None
        if status == "verified":  # 第 5 条：按第一个 loc 取原文快照
            first = nodes[locs[0]]
            snapshot = with_clause_no(first.text, first.clause_no)
        locs = list(dict.fromkeys(nodes[loc].target_loc or loc for loc in locs))  # 汇总行 → 表头行（Q-72 ⑥）
        findings.append(FindingView(
            title=f.title.strip()[:TITLE_MAX] or "未命名问题",
            category=map_category(f.category), severity=map_severity(f.severity),  # 第 2 条
            req_ids=[r for r in dict.fromkeys(x.strip() for x in f.req_ids) if r in sent],
            locs=locs, missing=f.missing, cite_status=status, quote_model=f.quote, quote_snapshot=snapshot,
            problem=f.problem, impact=f.impact, suggestion=f.suggestion, suggested_text=f.suggested_text,
            basis=f.basis,
        ))
    sanctions = out.sanctions
    return ValidatedReview(
        doc_summary=out.doc_summary,
        requirement_results=results,
        findings=_merge_duplicates(findings),  # 第 7 条
        sanctions_level=map_sanctions(sanctions.level) if sanctions else None,
        sanctions_reasons=list(sanctions.reasons) if sanctions else [],
    )


# ---------------------------------------------------------------- 分段合并（11.5）


@dataclass
class ChunkOutcome:
    chunk: Chunk
    review: ValidatedReview | None  # 失败时为 None
    error: str | None = None  # 失败原因（给用户看的中文，不含原文）
    tokens_in: int = 0  # 失败分段已消耗的 token（成功分段的在 review 中）
    tokens_out: int = 0


def merge_reviews(ctx: ReviewContext, outcomes: list[ChunkOutcome]) -> ValidatedReview:
    """合并各段结果：结论 issue > unable > ok > na（只看模型给出的结论，都没给出记 unable）；问题按段拼接后再合并重复；
    制裁取最高等级、原因去重；摘要取第一个成功分段。至少要有一个成功分段。"""
    reviews = [o.review for o in outcomes if o.review is not None]
    if not reviews:
        raise ValueError("没有成功的分段")
    results = []
    for req in ctx.requirements:
        best: RequirementResultView | None = None
        for r in reviews:
            rr = next((x for x in r.requirement_results if x.req_id == req.id), None)
            if rr is None or not rr.returned:
                continue
            if best is None or _VERDICT_RANK[rr.verdict] > _VERDICT_RANK[best.verdict]:
                best = rr
        if best is None:
            results.append(RequirementResultView(req.id, req.source, req.text, req.dimension, "unable", MISSING_NOTE,
                                                 False))
        else:
            results.append(RequirementResultView(req.id, req.source, req.text, req.dimension, best.verdict, best.note))
    levels = [r.sanctions_level for r in reviews if r.sanctions_level is not None]
    level = max(levels, key=lambda v: _SANCTIONS_RANK.get(v, -1)) if levels else None
    reasons = list(dict.fromkeys(x for r in reviews for x in r.sanctions_reasons))
    merged = ValidatedReview(
        doc_summary=reviews[0].doc_summary,
        requirement_results=results,
        findings=_merge_duplicates([f for r in reviews for f in r.findings]),
        sanctions_level=level,
        sanctions_reasons=reasons,
    )
    merged.tokens_in = sum(r.tokens_in for r in reviews) + sum(o.tokens_in for o in outcomes if o.review is None)
    merged.tokens_out = sum(r.tokens_out for r in reviews) + sum(o.tokens_out for o in outcomes if o.review is None)
    merged.llm_call_ids = [i for r in reviews for i in r.llm_call_ids]
    return merged


# ---------------------------------------------------------------- 与数据库、网关的衔接


def load_node_views(db: Session, task_id: uuid.UUID,
                    quote_tables: list[QuoteTable] | tuple = ()) -> dict[str, DocNodeView]:
    """本任务正文节点（part=body），按文档顺序；另加报价明细表的汇总行（Q-72 ⑥）：

    汇总行按外发时的文字校验引用，原文快照为汇总行文字按对照表还原的结果，入库位置换成表头行第一个单元格。
    """
    from app.redaction.engine import load_redaction_map  # 避免循环导入（engine 不依赖 review）

    rows = db.scalars(
        select(DocNode).join(TaskFile, TaskFile.id == DocNode.file_id)
        .where(DocNode.task_id == task_id, DocNode.part == "body").order_by(TaskFile.seq, DocNode.ord)
    )
    views = {r.loc: DocNodeView(r.loc, r.redacted_text or "", crypto.decrypt_text(r.text_enc) if r.text_enc else "",
                                r.clause_no) for r in rows}
    if quote_tables:
        mapping = load_redaction_map(db, task_id)
        for t in quote_tables:
            line = summary_text(t)
            views[t.loc] = DocNodeView(t.loc, line, restore(line, mapping), None, target_loc=t.first_loc)
    return views


def retry_message(error: OutputError) -> str:
    return f"你上一次的输出有问题：{error}。请重新输出，只输出一个符合规定结构的 JSON 对象，不要输出任何其他文字。"


def review_once(db: Session, ctx: ReviewContext, *, run: ReviewRun, user_id: uuid.UUID,
                chunk: Chunk | None = None, nodes: dict[str, DocNodeView] | None = None) -> ValidatedReview:
    """调用模型并校验；输出无法解析或不符合结构时重试 1 次，仍失败抛出 OutputError。网关错误（GatewayError）原样抛出。

    chunk 为分段审核的一段（只有 1 段时传 None，消息与 M2 相同）；nodes 可由调用方预先加载，分段时各段共用。
    """
    messages = build_messages(ctx, chunk)
    if nodes is None:
        nodes = load_node_views(db, ctx.task.id, ctx.quote_tables)
    tokens_in = tokens_out = 0
    call_ids: list[int] = []
    for attempt in range(2):
        result = call_model(db, purpose="review", messages=messages, user_id=user_id, task_id=ctx.task.id,
                            task_title=ctx.task.title, run_id=run.id)
        tokens_in += result.tokens_in
        tokens_out += result.tokens_out
        call_ids.append(result.llm_call_id)
        try:
            review = validate_output(result.content, ctx, nodes, chunk)
        except OutputError as exc:
            if attempt == 1:
                exc.tokens_in, exc.tokens_out, exc.llm_call_ids = tokens_in, tokens_out, call_ids
                raise
            messages = messages + [{"role": "assistant", "content": result.content},
                                   {"role": "user", "content": retry_message(exc)}]
            continue
        review.tokens_in, review.tokens_out, review.llm_call_ids = tokens_in, tokens_out, call_ids
        return review
    raise AssertionError("unreachable")
