"""增量复审（V35-D-03，设计 docs/v35/design/diff.md 第四节）。

本机先算：本稿与上一稿的变化（drafts.compare）→ 变化所在的一级条款 → 基准轮问题分三类（4.2）：
已忽略的原样带；所在条款没动的直接记“仍存在 · 条款未改动”；条款改了、删了，或缺失型问题且本稿有新增或修改，才交模型。
外发的只有本稿与上一稿节点的脱敏文本（共用一份对照表）和上轮问题的代号文本，整包照常经出口复检与网关。
plan() 只读不写；日志只记会话、文件序号与计数。
"""
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocNode, Finding, ReviewRun, Task, TaskFile
from app.redaction.outbound import escape_document_tags
from app.review import drafts
from app.review.chunking import outbound_lines
from app.review.clauses import first_level

logger = logging.getLogger(__name__)

MAX_SHARE = 0.6  # 变化超过本稿正文的 60% 时默认全文重审（4.1）
_CATEGORY = {"wording": "表述", "logic": "逻辑", "legal": "法律", "compliance": "合规与制裁", "commercial": "商务"}
_SEVERITY = {"high": "高", "medium": "中", "low": "低"}
NOTE_UNJUDGED = "模型未判断，按仍存在处理"
NOTE_IGNORED = "已忽略，本轮未重新判断"


@dataclass
class Prior:
    finding_id: object
    seq: int
    action: str  # send / unchanged / ignored / drop
    locs: list[str]  # 换算成本稿位置后的 locs
    touched: bool = False
    pid: str = ""  # 发给模型的编号 P1、P2…（只有 send 才有）
    anchor: str | None = None  # 本稿中的位置（分段时决定放在哪一段）
    line: str = ""


@dataclass
class DeltaPlan:
    available: bool
    reason: str = ""
    recommended: bool = False
    identical: bool = False
    base_run: ReviewRun | None = None
    file: TaskFile | None = None
    prev: TaskFile | None = None
    no: int = 2
    prev_no: int = 1
    summary: dict = field(default_factory=dict)
    changed_clauses: int = 0
    total_clauses: int = 0
    head: str = ""  # 【主体说明】（及日期平移说明）
    lines: list[str] = field(default_factory=list)  # 【待审核文件 · 变化】的各行
    locs: set[str] = field(default_factory=set)  # 本次发送的本稿位置
    boundaries: set[str] = field(default_factory=set)
    priors: list[Prior] = field(default_factory=list)

    @property
    def sent_priors(self) -> list[Prior]:
        return [p for p in self.priors if p.action == "send"]

    @property
    def empty(self) -> bool:
        """没有任何要交给模型的内容：本机直接出结果，不调用模型。"""
        return not self.lines and not self.sent_priors

    def body(self) -> str:
        return "\n".join(self.lines)

    def prior_lines(self, chunk=None) -> list[str]:
        """分段时每段只带位置落在本段的上轮问题；没有位置（缺失型、条款已删）的放第 1 段。"""
        return [p.line for p in self.priors_for(chunk)]

    def priors_for(self, chunk=None) -> list[Prior]:
        out = []
        for p in self.sent_priors:
            inside = p.anchor is not None and p.anchor in self.locs
            if chunk is None or chunk.total <= 1 or (p.anchor in chunk.locs if inside else chunk.index == 1):
                out.append(p)
        return out

    def text(self) -> str:
        """外发预览用：主体说明 + 变化 + 上轮问题（与实际发送的这两部分逐字相同）。"""
        if self.empty:
            return ""
        return "\n".join(x for x in (self.head, self.preview_body()) if x)

    def preview_body(self) -> str:
        if self.empty:
            return ""
        return self.message_body(self.body())

    def message_body(self, body: str, chunk=None) -> str:
        parts = [f"【待审核文件 · 变化】\n<document>\n{body}\n</document>"]
        priors = self.prior_lines(chunk)
        if priors:
            parts += ["【上轮问题】", *priors]
        return "\n".join(parts)

    def coverage(self) -> dict:
        """写进轮次 coverage 的增量信息：只有位置编号与数字，不含原文。"""
        counts = {a: sum(p.action == a for p in self.priors) for a in ("send", "unchanged", "ignored")}
        return {"base_run_id": str(self.base_run.id), "base_run_no": self.base_run.run_no, "no": self.no,
                "prev_no": self.prev_no, "changed_clauses": self.changed_clauses,
                "unchanged_clauses": max(0, self.total_clauses - self.changed_clauses),
                "priors_sent": counts["send"], "priors_unchanged": counts["unchanged"],
                "priors_ignored": counts["ignored"], "summary": self.summary, "locs": sorted(self.locs)}

    def info(self) -> dict:
        """设置页“审核范围”用的说明（4.1）。"""
        if not self.available:
            return {"available": False, "reason": self.reason}
        return {"available": True, "recommended": self.recommended, "identical": self.identical, "empty": self.empty,
                "no": self.no, "prev_no": self.prev_no, "base_run_no": self.base_run.run_no,
                "changed_clauses": self.changed_clauses, "total_clauses": self.total_clauses,
                "priors_sent": len(self.sent_priors), "priors_total": len([p for p in self.priors if p.action != "drop"]),
                "summary": self.summary,
                "advice": "" if self.recommended or self.identical else "改动较多，建议全文重审"}


def _unavailable(reason: str) -> DeltaPlan:
    return DeltaPlan(available=False, reason=reason)


def base_run(db: Session, task: Task, prev: TaskFile) -> ReviewRun | None:
    """基准轮：上一稿作主文件的最新一轮（completed 或 partial，4.1）。"""
    for run in db.scalars(select(ReviewRun).where(ReviewRun.task_id == task.id,
                                                  ReviewRun.status.in_(("completed", "partial")))
                          .order_by(ReviewRun.run_no.desc())):
        if run.file_ids and str(run.file_ids[0]) == str(prev.id):
            return run
    return None


def plan(db: Session, task: Task, file: TaskFile, *, stance: str | None = None,
         base_run_id=None) -> DeltaPlan:
    """本稿 file 的增量复审计划。base_run_id 给出时（worker 重算）用该基准轮，不再重新挑选。"""
    from app.redaction.engine import build_outbound
    from app.review.prompt import split_outbound

    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
    by_id = {f.id: f for f in files}
    prev = by_id.get(file.prev_file_id)
    if prev is None:
        return _unavailable("这份文件没有上一稿")
    base = db.get(ReviewRun, base_run_id) if base_run_id else base_run(db, task, prev)
    if (base is None or base.task_id != task.id or base.status not in ("completed", "partial")
            or not base.file_ids or str(base.file_ids[0]) != str(prev.id)):
        return _unavailable("上一稿还没有完成的审核，请全文审核")
    from app.redaction.engine import load_redaction_map, run_redaction_map
    if run_redaction_map(db, base).get("[我方]") != load_redaction_map(db, task.id).get("[我方]"):
        # 上轮问题里的 [我方] 指另一方，不能和本稿正文一起发（V36-R-03）
        return _unavailable("我方已改，请全文审核")
    old, new = drafts.body_nodes(db, prev), drafts.body_nodes(db, file)
    if old is None or new is None:
        return _unavailable("原文已按保留期清理，不能对比")
    result = drafts.compare(old, new, mode=drafts._mode(file), with_map=True)
    chains = drafts.chain_info(files)
    out = DeltaPlan(available=True, base_run=base, file=file, prev=prev, no=chains.get(file.id, (2,))[0],
                    prev_no=chains.get(prev.id, (1,))[0], summary=result["summary"])

    # 本稿一级条款（4.2：每处变化发所在一级条款的全文）
    rows = list(db.execute(select(DocNode.loc, DocNode.part, DocNode.kind, DocNode.clause_no, DocNode.redacted_text)
                           .where(DocNode.file_id == file.id, DocNode.part == "body").order_by(DocNode.ord)))
    clauses = first_level([SimpleNamespace(loc=r.loc, part=r.part, kind=r.kind, clause_no=r.clause_no,
                                           text=r.redacted_text or "") for r in rows])
    clause_of = {loc: i for i, c in enumerate(clauses) for loc in c.locs}
    old_clauses = first_level([SimpleNamespace(**vars(n), part="body") for n in old])
    old_clause_of = {loc: i for i, c in enumerate(old_clauses) for loc in c.locs}
    loc_map = result["map"]
    prev_rows = {r.loc: r for r in db.execute(select(DocNode.loc, DocNode.redacted_text)
                                              .where(DocNode.file_id == prev.id, DocNode.part == "body"))}
    prev_text = lambda loc: escape_document_tags(" ".join((prev_rows[loc].redacted_text or "").split())) \
        if loc in prev_rows else ""

    substantive = [c for c in result["changes"] if c["type"] != "format"]
    out.identical = not substantive
    changed: set[int] = set()
    deleted_clauses: dict[int, list[str]] = {}
    before: dict[str, list[str]] = {}  # 本稿某段之前要插的说明（删除的段落）
    after: dict[str, list[str]] = {}  # 本稿某段之后的说明
    changed_chars = 0
    for c in substantive:
        if c["type"] == "del":
            note = f"（上一稿{c['clause'] or '此处'}已删除：{prev_text(c['prev_loc'])}）"
            old_index = old_clause_of[c["prev_loc"]]
            # 整条删除时只送上一稿的文字，不能把纸面显示用的相邻条款也当成变化。
            surviving = [loc_map[loc] for loc in old_clauses[old_index].locs
                         if loc in loc_map and loc_map[loc] in clause_of]
            if surviving:
                anchor = c.get("anchor")
                if anchor in surviving:
                    (after if c.get("after") else before).setdefault(anchor, []).append(note)
                else:
                    anchor = surviving[-1]
                    after.setdefault(anchor, []).append(note)
                changed.add(clause_of[anchor])
            else:
                deleted_clauses.setdefault(old_index, []).append(note)
            changed_chars += len(c["segs"][0]["s"])
            continue
        changed.add(clause_of.get(c["loc"], -1))
        changed_chars += sum(len(s["s"]) for s in c["segs"] if s["t"] != "eq") if c["type"] == "mod" \
            else len(c["segs"][0]["s"])
        if c["type"] == "mod":
            after.setdefault(c["loc"], []).append(f"（上一稿此段：{prev_text(c['prev_loc'])}）")
        elif c["type"] == "add":
            after.setdefault(c["loc"], []).append("（此段为本稿新增）")
        else:
            after.setdefault(c["loc"], []).append(f"（此段由上一稿{c.get('prev_clause') or '别处'}移来，文字未改）")
    changed.discard(-1)
    total_chars = sum(len(n.text) for n in new) or 1
    out.changed_clauses = len(changed) + len(deleted_clauses)
    out.total_clauses = len(clauses) + len(deleted_clauses)
    out.recommended = bool(substantive) and changed_chars / total_chars <= MAX_SHARE

    # 变化所在条款的外发行：与全文审核的行逐字相同（同一份对照表、同样的报价表汇总）
    built = build_outbound(db, task, "review", file_ids=[str(file.id)], stance=stance)
    out.head, body = split_outbound(built.text)
    line_of = {x.loc: x.text for x in outbound_lines(body, set())}
    table_of = {loc: t.loc for t in built.quote_tables if not t.detailed for loc in t.row_locs}
    for i in sorted(changed):
        clause = clauses[i]
        out.boundaries.add(clause.first_loc)
        for loc in clause.locs:
            out.lines += before.get(loc, [])
            target = table_of.get(loc, loc)
            if target in line_of:
                out.lines.append(line_of.pop(target))
                out.locs.add(target)
            out.lines += after.get(loc, [])
    for lines in deleted_clauses.values():
        out.lines += lines

    # 上轮问题分三类（4.2）
    old_labels = dict(zip((n.loc for n in old), drafts._labels(old)))
    has_new_text = any(c["type"] in ("add", "mod") for c in substantive)
    prefix = f"F{prev.seq}-"
    pn = 0
    for f in db.scalars(select(Finding).where(Finding.run_id == base.id, Finding.seq > 0).order_by(Finding.seq)):
        if f.carry == "resolved":
            continue  # 已解决的不再往下带（4.3）
        locs = [str(x) for x in (f.locs or [])]
        own = [x for x in locs if x.startswith(prefix)]
        mapped = [loc_map.get(x) for x in own]
        new_locs = list(dict.fromkeys([m for m in mapped if m] + [x for x in locs if not x.startswith(prefix)]))
        deleted = bool(own) and all(m is None for m in mapped)
        if f.missing or not locs:
            touched = has_new_text
        else:
            touched = any(m is None for m in mapped) or any(clause_of.get(m) in changed for m in mapped if m)
        prior = Prior(f.id, f.seq, "send", new_locs, touched)
        if f.handling_status == "ignored":
            prior.action = "drop" if deleted else "ignored"
        elif not touched:
            prior.action = "unchanged"
        else:
            pn += 1
            prior.pid = f"P{pn}"
            prior.anchor = next((table_of.get(m, m) for m in mapped if m and table_of.get(m, m) in out.locs), None)
            prior.anchor = prior.anchor or next((m for m in mapped if m), None)
            if prior.anchor is not None:
                where = f"[{prior.anchor}]"
            elif deleted:
                where = f"原{old_labels.get(own[0]) or own[0]}（本稿已删除）"
            else:
                where = "缺失项"
            esc = lambda v: escape_document_tags(" ".join((v or "").split()))
            prior.line = (f"{prior.pid} {esc(f.title)}｜{_CATEGORY.get(f.category, f.category)}｜"
                          f"{_SEVERITY.get(f.severity, f.severity)}｜{where}｜问题：{esc(f.problem)}｜"
                          f"建议：{esc(f.suggestion) or '无'}")
        out.priors.append(prior)
    logger.info("增量复审计划 task=%s file=F%d prev=F%d base=%d changes=%d clauses=%d/%d priors=%d",
                task.id, file.seq, prev.seq, base.run_no, len(substantive), out.changed_clauses,
                out.total_clauses, len(out.sent_priors))
    return out


def main_file(db: Session, run: ReviewRun) -> TaskFile | None:
    import uuid
    return db.get(TaskFile, uuid.UUID(str(run.file_ids[0]))) if run.file_ids else None
