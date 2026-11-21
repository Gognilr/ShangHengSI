"""review 处理函数（SPEC 7.1 第 4 步、7.3、7.5，设计 docs/design/M2.md 第一、八节）。

worker 取到 review 任务后：run=running → 拼接上下文 → review_once（经网关：上限检查 → 出口复检 → 发送 → 记录）→ 写结果。
- 成功：全部要求都有非 unable 的结论时 run=completed，否则 partial；task=reviewed，high_count=本轮高风险问题数。
- 网关拦截（BLOCKED）：run=blocked，error_message 只写类别和位置；task 回到 awaiting_confirm，用户处理后可重新提交（T-07）。
- 其他网关错误、输出无法解析：run=failed；task=reviewed。其他异常：同样置 failed 后再抛出，job 记为 failed。
- 外发策略不是 ai 的任务一律不调用模型（A-04）；任务或轮次已被删除时直接结束。
- 模型返回的文字以代号形式入库，原文引用快照加密存储。日志只写轮次 id、状态、数量，不写任何文本。
- 分段审核（M5，设计 docs/design/M5.md 第一至三节）：外发文本超过分段长度时逐段调用；任一段被拦截立即整轮 blocked；
  其他失败的分段跳过并记入 failed_chunks，全部失败为 failed，部分失败为 partial；每段结束后更新进度。
"""
import copy
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.exc import IntegrityError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.llm import gateway, usage
from app.llm.gateway import load_model
from app.db import jobs
from app.db.base import utcnow
from app.db.models import DocNode, Finding, LlmCall, RequirementResult, ReviewRun, Task, TaskFile
from app.llm.gateway import GatewayError, describe_reasons
from app.review.chunking import Chunk, effective_limit, outbound_lines, split_chunks
from app.review.coverage import build_coverage
from app.review.prompt import ReviewContext, build_messages, split_outbound
from app.review.requirements import Requirement, group_requirements
from app.review.service import build_context
from app.review.validate import (
    ChunkOutcome,
    DocNodeView,
    OutputError,
    ValidatedReview,
    load_node_views,
    merge_reviews,
    normalize_unable_reason,
    review_once,
)
from app.security import crypto
from app.worker import job_handler

logger = logging.getLogger("app.review.job")

MSG_LOCAL_ONLY = "该任务的外发策略为不外发，未调用模型"
MSG_BAD_OUTPUT = "模型返回的内容不符合要求，请稍后重新审核"
MSG_INTERNAL = jobs.REVIEW_INTERNAL_MSG
CHUNK_PARALLEL = 3  # 一轮最多同时发送的分段数（V31-U5F-01）
PROGRESS_WRITE_S = 1.0  # 流式接收字数写入进度的最短间隔（V31-U5F-03）


def _reload(db: Session, run_id: uuid.UUID) -> tuple[ReviewRun, Task] | None:
    """放弃未提交的改动后重读轮次和任务；任一已被删除时返回 None。"""
    db.rollback()
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update().execution_options(populate_existing=True))
    task = db.get(Task, run.task_id, populate_existing=True) if run is not None else None
    return (run, task) if run is not None and task is not None else None


def _finish(db: Session, run_id: uuid.UUID, *, status: str, message: str, task_status: str,
            tokens_in: int = 0, tokens_out: int = 0, chunk_count: int | None = None,
            failed: list[dict] | None = None, coverage: dict | None = None) -> None:
    """把轮次置为 blocked / failed，并让任务进入 task_status。"""
    found = _reload(db, run_id)
    if found is None:
        return
    run, task = found
    if run.status not in ("queued", "running"):
        db.rollback()
        return
    db.execute(delete(Finding).where(Finding.run_id == run_id, Finding.seq < 0))
    run.status, run.error_message, run.finished_at = status, message, utcnow()
    run.tokens_in += tokens_in
    run.tokens_out += tokens_out
    if chunk_count is not None:
        run.chunk_count = chunk_count
    if failed is not None:
        run.failed_chunks = failed
    run.coverage = None if status == "blocked" else coverage  # blocked 未发送；其他带结果的终态写入覆盖说明
    if task.kind != "assistant":  # 会话的状态不随审核轮次变化（V32-A-02）
        task.status = task_status
        if task_status == 'awaiting_confirm':
            from app.redaction.stats import enter
            enter(task)
    task.last_activity_at = utcnow()
    db.commit()
    logger.info("run %s 结束 状态=%s", run_id, status)


def _failed_chunks(outcomes: list[ChunkOutcome]) -> list[dict]:
    return [{"index": o.chunk.index, "first_loc": o.chunk.first_loc, "last_loc": o.chunk.last_loc, "reason": o.error}
            for o in outcomes if o.review is None]


def _partial_message(failed: list[dict]) -> str:
    return f"第 {'、'.join(str(f['index']) for f in failed)} 段审核失败，结果不完整"


_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _ordered(db: Session, task_id: uuid.UUID, findings: list) -> list:
    """问题按等级、再按原文位置（文件顺序、段落顺序）排序；缺失类问题排在同级末尾（V321Q-25）。

    四组并发返回的先后不同，按位置排序后两次审核的列表顺序才可对照；入库序号即此顺序。
    """
    order = {loc: i for i, loc in enumerate(db.scalars(
        select(DocNode.loc).join(TaskFile, TaskFile.id == DocNode.file_id)
        .where(DocNode.task_id == task_id, DocNode.part == "body").order_by(TaskFile.seq, DocNode.ord)))}
    last = len(order)
    return sorted(findings, key=lambda f: (_SEVERITY_RANK.get(f.severity, 3),
                                           min((order.get(loc, last) for loc in f.locs), default=last + 1)))


def _save(db: Session, run_id: uuid.UUID, ctx: ReviewContext, review: ValidatedReview, *, chunk_count: int = 1,
          failed: list[dict] | None = None) -> None:
    failed = failed or []
    found = _reload(db, run_id)
    if found is None:
        return
    run, task = found
    if run.status != "running":  # 心跳最终失败后迟到的处理结果不得覆盖终态
        db.rollback()
        return
    db.execute(delete(Finding).where(Finding.run_id == run_id, Finding.seq < 0))
    if ctx.delta is not None:
        _save_delta(db, run, task, ctx, review, chunk_count, failed)
        return
    for rr in review.requirement_results:
        db.add(RequirementResult(run_id=run.id, req_id=rr.req_id, req_source=rr.req_source, req_text=rr.req_text,
                                 dimension=rr.dimension, verdict=rr.verdict, note=rr.note,
                                 unable_reason=normalize_unable_reason(rr.verdict, rr.unable_reason)))
    review.findings = _ordered(db, task.id, review.findings)
    for seq, f in enumerate(review.findings, start=1):
        db.add(Finding(
            run_id=run.id, task_id=task.id, seq=seq, title=f.title, category=f.category, severity=f.severity,
            req_ids=f.req_ids, locs=f.locs, missing=f.missing, cite_status=f.cite_status, quote_model=f.quote_model,
            quote_snapshot_enc=crypto.encrypt_text(f.quote_snapshot) if f.quote_snapshot is not None else None,
            quote_orig_enc=crypto.encrypt_text(f.quote_original) if f.quote_original is not None else None,
            problem=f.problem, impact=f.impact, suggestion=f.suggestion, suggested_text=f.suggested_text,
            basis=f.basis,
            law_refs=f.law_refs, law_unverified=f.law_unverified,
            source=f.source,
        ))
    high = sum(1 for f in review.findings if f.severity == "high")
    now = utcnow()
    run.doc_summary = review.doc_summary
    run.sanctions_level = review.sanctions_level
    run.sanctions_reasons = review.sanctions_reasons
    run.tokens_in += review.tokens_in
    run.tokens_out += review.tokens_out
    run.chunk_count = chunk_count
    run.failed_chunks = failed
    run.coverage = build_coverage(db, task, ctx, review, chunk_count, failed)
    unable = any(r.verdict == "unable" for r in review.requirement_results)
    run.status = "partial" if failed or unable else "completed"
    run.error_message = _partial_message(failed) if failed else None
    run.finished_at = now
    if task.kind != "assistant":  # 会话的状态不随审核轮次变化（V32-A-02）
        task.status = "reviewed"
        task.high_count = high
        task.last_completed_at = now
    task.error_message = None
    task.last_activity_at = now
    try:
        db.commit()
    except IntegrityError:  # 保存期间任务被删除
        if _reload(db, run_id) is not None:
            raise
        return
    logger.info("run %s 结束 状态=%s 问题=%s 高风险=%s", run_id, run.status, len(review.findings), high)


_CARRIED = ("title", "category", "severity", "req_ids", "missing", "cite_status", "quote_model", "quote_snapshot_enc",
            "quote_orig_enc", "problem", "impact", "suggestion", "suggested_text", "basis", "law_refs", "law_unverified",
            "source", "handling_status", "discussed_text", "discussed_message_id", "discussed_at", "use_discussed",
            "handling_note_enc")
_VERDICT_NOTE_RESOLVED = "上轮问题已解决"


def _save_delta(db: Session, run: ReviewRun, task: Task, ctx: ReviewContext, review: ValidatedReview,
                chunk_count: int, failed: list[dict]) -> None:
    """增量轮的结果（diff.md 4.3）：本轮新增 + 仍存在（模型判断 / 条款未改动）+ 已解决，全部写进本轮 findings。

    逐条结论沿用基准轮，有新增或仍存在问题的要求记为有问题；制裁取基准轮与本轮的较高一级。
    """
    from app.review.delta import NOTE_IGNORED, NOTE_UNJUDGED
    from app.review.validate import TITLE_SIMILARITY, _SANCTIONS_RANK, RequirementResultView, _similar
    plan = ctx.delta
    base = db.get(ReviewRun, plan.base_run.id)
    olds = {f.id: f for f in db.scalars(select(Finding).where(Finding.run_id == base.id))}
    rows: list[Finding] = []
    for prior in plan.priors:
        old = olds.get(prior.finding_id)
        if old is None or prior.action == "drop":
            continue
        if prior.action == "send":
            carry, note = review.prior.get(prior.pid, ("persists", NOTE_UNJUDGED))
        elif prior.action == "unchanged":
            carry, note = "unchanged", None
        else:  # 已忽略：原样带
            carry, note = ("persists", NOTE_IGNORED) if prior.touched else ("unchanged", None)
        row = Finding(run_id=run.id, task_id=task.id, seq=0, locs=prior.locs, carry=carry, carry_note=note,
                      prev_finding_id=old.id, **{k: getattr(old, k) for k in _CARRIED})
        rows.append(row)
    kept = [r for r in rows if r.carry != "resolved"]
    for f in review.findings:
        # 新问题与某个仍存在的旧问题位置相同、标题相似时并为一条（沿用 11.4 第 7 条）
        same = next((r for r in kept if r.category == f.category and set(r.locs or []) & set(f.locs)
                     and _similar(r.title, f.title) >= TITLE_SIMILARITY), None)
        if same is not None:
            continue
        rows.append(Finding(
            run_id=run.id, task_id=task.id, seq=0, title=f.title, category=f.category, severity=f.severity,
            req_ids=f.req_ids, locs=f.locs, missing=f.missing, cite_status=f.cite_status, quote_model=f.quote_model,
            quote_snapshot_enc=crypto.encrypt_text(f.quote_snapshot) if f.quote_snapshot is not None else None,
            quote_orig_enc=crypto.encrypt_text(f.quote_original) if f.quote_original is not None else None,
            problem=f.problem, impact=f.impact, suggestion=f.suggestion, suggested_text=f.suggested_text,
            basis=f.basis, law_refs=f.law_refs, law_unverified=f.law_unverified, source=f.source, carry="new"))
    live = _ordered(db, task.id, [r for r in rows if r.carry != "resolved"])
    done = _ordered(db, task.id, [r for r in rows if r.carry == "resolved"])
    for seq, row in enumerate(live + done, start=1):
        row.seq = seq
        db.add(row)

    issue = {r for row in live for r in (row.req_ids or [])}
    cleared = {r for row in done for r in (row.req_ids or [])} - issue
    results = []
    for rr in db.scalars(select(RequirementResult).where(RequirementResult.run_id == base.id).order_by(RequirementResult.id)):
        verdict, note, reason = rr.verdict, rr.note, rr.unable_reason
        if rr.req_id in issue and verdict != "issue":
            verdict, note, reason = "issue", None, None
        elif rr.req_id in cleared and verdict == "issue":
            verdict, note = "ok", _VERDICT_NOTE_RESOLVED
        db.add(RequirementResult(run_id=run.id, req_id=rr.req_id, req_source=rr.req_source, req_text=rr.req_text,
                                 dimension=rr.dimension, verdict=verdict, note=note, unable_reason=reason))
        results.append(RequirementResultView(rr.req_id, rr.req_source, rr.req_text, rr.dimension, verdict, note,
                                             unable_reason=reason))
    levels = [x for x in (base.sanctions_level, review.sanctions_level) if x]
    now = utcnow()
    run.doc_summary = review.doc_summary or base.doc_summary
    run.sanctions_level = max(levels, key=lambda v: _SANCTIONS_RANK.get(v, -1)) if levels else None
    run.sanctions_reasons = list(dict.fromkeys(list(base.sanctions_reasons or []) + list(review.sanctions_reasons)))
    run.tokens_in += review.tokens_in
    run.tokens_out += review.tokens_out
    run.chunk_count = chunk_count
    run.failed_chunks = failed
    summary = ValidatedReview(run.doc_summary, results, [], run.sanctions_level, run.sanctions_reasons)
    coverage = build_coverage(db, task, ctx, summary, chunk_count, failed)
    coverage["requirements"]["total"] = len(results)
    coverage["delta"] = {**plan.coverage(), "new": sum(r.carry == "new" for r in rows),
                         "persists": sum(r.carry in ("persists", "unchanged") for r in rows),
                         "resolved": len(done)}
    run.coverage = coverage
    unable = any(r.verdict == "unable" for r in results)
    run.status = "partial" if failed or unable else "completed"
    run.error_message = _partial_message(failed) if failed else None
    run.finished_at = now
    task.error_message = None
    task.last_activity_at = now
    try:
        db.commit()
    except IntegrityError:
        if _reload(db, run.id) is not None:
            raise
        return
    logger.info("run %s 增量轮结束 状态=%s 新增=%s 仍存在=%s 已解决=%s", run.id, run.status,
                coverage["delta"]["new"], coverage["delta"]["persists"], coverage["delta"]["resolved"])


def run_review(db: Session, run_id: uuid.UUID) -> None:
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
    task = db.get(Task, run.task_id) if run is not None else None
    if run is None or task is None:  # 已删除
        return
    if run.status not in ("queued", "running"):  # 重复投递；running 是 worker 异常退出后的重新入队
        return
    if task.policy != "ai":  # A-04：创建轮次的接口已拦截，这里是第二道防线
        _finish(db, run_id, status="failed", message=MSG_LOCAL_ONLY, task_status="reviewed")
        return
    db.execute(delete(Finding).where(Finding.run_id == run_id, Finding.seq < 0))  # worker 重入清除上次未完成的临时问题
    run.status, run.started_at, run.finished_at, run.error_message = "running", utcnow(), None, None
    try:
        model = load_model(db, run.model_id)
    except GatewayError:
        _finish(db, run_id, status="failed", message="所选模型已停用，请重新审核并换一个模型", task_status="reviewed")
        return
    run.model_name = model.name
    db.commit()
    logger.info("run %s 开始", run_id)

    ctx: ReviewContext | None = None
    plan = None
    if run.scope == "delta":  # 增量轮：按创建时的基准轮重算计划（本机、确定性）
        from app.review import delta
        main = delta.main_file(db, run)
        plan = delta.plan(db, task, main, stance=run.stance, base_run_id=run.base_run_id) if main else None
        if plan is None or not plan.available:
            _finish(db, run_id, status="failed", message=(plan.reason if plan else "主文件已移除") + "，请全文重审",
                    task_status="reviewed")
            return
    try:
        ctx = build_context(db, task, stance=run.stance, source=run.source, user_note=run.user_note,
                            main_doc_type=run.main_doc_type, file_ids=run.file_ids, delta=plan)
        if plan is not None and plan.empty:  # 没有要交给模型的内容：本机直接出结果，不调用模型
            _save(db, run_id, ctx, ValidatedReview("", [], [], None, []), chunk_count=1)
            return
        _, body = split_outbound(ctx.outbound_text)
        chunks = split_chunks(outbound_lines(body, ctx.boundary_locs),
                              effective_limit(model.chunk_chars))
        total = len(chunks)
        nodes = load_node_views(db, task.id, ctx.quote_tables)
        from app.standards.compare import start as start_templates
        start_templates(db,ctx,run,nodes)
        if not _set_progress(db, run_id, 0, total):
            return
        prepared = _prepare_chunks(db, ctx, chunks, nodes)
        # 全文能放入管理员配置的长度时按维度并发；长文件保留原分段路径，避免超出模型上下文。
        # 增量轮一次请求，不按维度并发（4.2：发送量小，并发省不了时间却要 4 倍调用）
        groups = group_requirements(ctx.requirements) if total == 1 and plan is None else [ctx.requirements]
        if len(groups) > 1:
            outcomes = _send_dimensions(db, run_id, prepared[0], groups, nodes)
        else:
            if total > 1 and not _precheck_all(db, run_id, prepared):
                return  # 某段复检命中：整轮 blocked，一段都没有发送
            outcomes = _send_chunks(db, run_id, prepared, nodes, total)
        if outcomes is None:  # 网关拦截（已置 blocked），或轮次 / 任务已删除
            return
    except Exception:
        _finish(db, run_id, status="failed", message=MSG_INTERNAL, task_status="reviewed")
        raise  # job 记为 failed，last_error 只有异常类名
    failed = _failed_chunks(outcomes)
    if len(failed) == total:
        coverage = build_coverage(db, task, ctx, None, total, failed)
        _finish(db, run_id, status="failed", message=outcomes[0].error or MSG_INTERNAL, task_status="reviewed",
                tokens_in=sum(o.tokens_in for o in outcomes), tokens_out=sum(o.tokens_out for o in outcomes),
                chunk_count=total, failed=failed if total > 1 else [], coverage=coverage)
        return
    review = outcomes[0].review if total == 1 else merge_reviews(ctx, outcomes)
    _save(db, run_id, ctx, review, chunk_count=total, failed=failed)


def _set_progress(db: Session, run_id: uuid.UUID, done: int, total: int) -> bool:
    """处理中的分段进度暂存在 coverage.progress（Q-78 ③），轮次结束时清除。轮次或任务已删除时返回 False。"""
    found = _reload(db, run_id)
    if found is None:
        return False
    run, _ = found
    if run.status != "running":
        db.rollback()
        return False
    run.chunk_count = total
    run.coverage = {"progress": {"done": done, "total": total}}
    db.commit()
    return True


def _prepare_chunks(db: Session, ctx: ReviewContext, chunks: list[Chunk],
                    nodes: dict[str, DocNodeView]) -> list[tuple[Chunk, ReviewContext]]:
    """逐段准备法条与范本（按段序，统计累加在共用的 ctx 上），返回每段的上下文快照，供并发发送使用。"""
    from app.laws.retrieve import prepare
    from app.standards.compare import prepare as prepare_template
    prepared = []
    for chunk in chunks:
        part = chunk if chunk.total > 1 else None
        if ctx.delta is not None and part is None:  # 增量轮：法条与范本只按变化的条款检索
            part = Chunk(chunk.index, chunk.total, chunk.lines, chunk.first_loc, chunk.last_loc,
                         frozenset(ctx.delta.locs))
        prepare(db, ctx, part, nodes)
        prepare_template(db, ctx, part)
        prepared.append((chunk, copy.copy(ctx)))  # 每段重新赋值 law_refs / template_section，浅拷贝即可
    db.commit()  # 范本准备可能锁住任务行与对照表，并发发送前释放
    return prepared


def _blocked(db: Session, run_id: uuid.UUID, exc: GatewayError) -> None:
    message = f"出口复检发现未脱敏的信息，已拦截，未发送（{describe_reasons(exc.reasons)}）"
    _finish(db, run_id, status="blocked", message=message, task_status="awaiting_confirm")


def _precheck_all(db: Session, run_id: uuid.UUID, prepared: list[tuple[Chunk, ReviewContext]]) -> bool:
    """分段并发发送前，先对每一段的完整消息做出口复检：任何一段命中，整轮不发（V31-U5F-01）。"""
    run = db.get(ReviewRun, run_id)
    for chunk, snapshot in prepared:
        try:
            gateway.precheck(db, model_id=run.model_id, purpose="review", messages=build_messages(snapshot, chunk),
                             user_id=run.created_by, task_id=snapshot.task.id, task_title=snapshot.task.title,
                             run_id=run_id)
        except GatewayError as exc:
            _update_receipt(db, run_id)
            _blocked(db, run_id, exc)
            return False
    return True


def _parallel(db: Session, run: ReviewRun, total: int) -> int:
    """审核额度按轮计，各组与分段不再因本轮剩余名额而串行。"""
    return min(CHUNK_PARALLEL, total)


def _send_dimensions(db: Session, run_id: uuid.UUID, prepared: tuple[Chunk, ReviewContext],
                     groups: list[list[Requirement]], nodes: dict[str, DocNodeView]) -> list[ChunkOutcome] | None:
    """各组使用同一全文、法条与范本，先复检全部组，再并发；合并后仍记为一个全文结果。"""
    chunk, ctx = prepared
    requests = []
    for index, requirements in enumerate(groups, 1):
        snapshot = copy.copy(ctx)
        snapshot.requirements = requirements
        # total=1 保证提示词与引用校验均按全文；index 仅区分进度回调。
        group_chunk = Chunk(index, 1, chunk.lines, chunk.first_loc, chunk.last_loc, chunk.locs)
        requests.append((group_chunk, snapshot))
    if not _precheck_all(db, run_id, requests):
        return None
    outcomes = _send_chunks(db, run_id, requests, nodes, len(requests), dimensions=True)
    if outcomes is None:
        return None
    if all(o.review is None for o in outcomes):
        return [ChunkOutcome(chunk, None, outcomes[0].error,
                             sum(o.tokens_in for o in outcomes), sum(o.tokens_out for o in outcomes))]
    # 失败组的要求由 merge_reviews 补 unable；成功组照常保留，不伪报“未做跨分段检查”。
    return [ChunkOutcome(chunk, merge_reviews(ctx, outcomes))]


def _send_chunks(db: Session, run_id: uuid.UUID, prepared: list[tuple[Chunk, ReviewContext]],
                 nodes: dict[str, DocNodeView], total: int, *, dimensions: bool = False) -> list[ChunkOutcome] | None:
    """每次经网关上限检查、出口复检、记录；最多四个维度组或 CHUNK_PARALLEL 个分段同时进行。
    进度在复检通过、即将发出时就写入（sent），收到回复后写 done，界面据此如实显示（V31-U5F-01）。"""
    bind = db.get_bind()
    run = db.get(ReviewRun, run_id)
    parallel = min(4, total) if dimensions else _parallel(db, run, total)
    lock = threading.Lock()
    stop = threading.Event()
    sent: set[int] = set()
    done = 0
    blocked: list[GatewayError] = []
    received: dict[int, int] = {}  # 各段本次尝试已流式收到的字数（V31-U5F-03）
    last_write = 0.0

    def write_progress() -> None:  # 调用方持有 lock：按顺序写，旧的计数不会覆盖新的
        nonlocal last_write
        last_write = time.monotonic()
        with Session(bind=bind) as s:
            row = s.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
            if row is not None and row.status == "running":
                row.chunk_count = 1 if dimensions else total
                # 既有界面把 progress.total 称为“段”；维度组均看全文，聚合为一份文件的真实进度。
                row.coverage = {"progress": {"done": int(done == total) if dimensions else done,
                                             "total": 1 if dimensions else total,
                                             "sent": int(bool(sent)) if dimensions else len(sent),
                                             "received": sum(received.values())}}
            s.commit()

    def mark_sent(index: int) -> None:
        with lock:
            if index not in sent:
                sent.add(index)
                write_progress()

    def mark_received(index: int, chars: int) -> None:
        with lock:
            received[index] = chars
            if time.monotonic() - last_write >= PROGRESS_WRITE_S:  # 节流：最多每秒写一次
                write_progress()

    def one(chunk: Chunk, snapshot: ReviewContext) -> ChunkOutcome | None:
        nonlocal done
        if stop.is_set():
            return None
        provisional_ids = []
        def reset_findings():
            with Session(bind=bind) as temp:
                temp.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
                if provisional_ids:
                    temp.execute(delete(Finding).where(Finding.run_id == run_id, Finding.id.in_(provisional_ids), Finding.seq < 0))
                    provisional_ids.clear()
                temp.commit()

        def save_finding(f):
            with Session(bind=bind) as temp:
                row = temp.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
                if row is None or row.status != "running":
                    return
                lowest = temp.scalar(select(func.min(Finding.seq)).where(Finding.run_id == run_id)) or 0
                item = Finding(run_id=run_id, task_id=snapshot.task.id, seq=min(lowest, 0) - 1,
                    title=f.title, category=f.category, severity=f.severity, req_ids=f.req_ids, locs=f.locs,
                    missing=f.missing, cite_status=f.cite_status, quote_model=f.quote_model,
                    quote_snapshot_enc=crypto.encrypt_text(f.quote_snapshot) if f.quote_snapshot is not None else None,
                    quote_orig_enc=crypto.encrypt_text(f.quote_original) if f.quote_original is not None else None,
                    problem=f.problem, impact=f.impact, suggestion=f.suggestion, suggested_text=f.suggested_text,
                    basis=f.basis, law_refs=f.law_refs, law_unverified=f.law_unverified, source=f.source)
                temp.add(item)
                temp.flush()
                provisional_ids.append(item.id)
                temp.commit()

        with Session(bind=bind, expire_on_commit=False) as s:
            row = s.get(ReviewRun, run_id)
            task = s.get(Task, snapshot.task.id)
            if row is None or task is None:
                return None
            snapshot.task = task  # 各线程只用自己会话中的对象
            part = chunk if chunk.total > 1 else None
            try:
                with usage.review_round(run_id):
                    review = review_once(s, snapshot, run=row, user_id=row.created_by, chunk=part, nodes=nodes,
                                         on_send=lambda: mark_sent(chunk.index),
                                         on_delta=lambda n: mark_received(chunk.index, n),
                                         on_finding=save_finding, on_reset=reset_findings)
                outcome = ChunkOutcome(chunk, review)
            except GatewayError as exc:
                reset_findings()
                if exc.code == "BLOCKED":
                    stop.set()
                    blocked.append(exc)
                    return None
                outcome = ChunkOutcome(chunk, None, str(exc))
            except OutputError as exc:
                reset_findings()
                # 只展示固定分类，不展示模型文字或可能含有输入值的校验异常。
                message = f"{MSG_BAD_OUTPUT}（已自动纠正重试 1 次；{exc.public_reason}）"
                outcome = ChunkOutcome(chunk, None, message, exc.tokens_in, exc.tokens_out)
            finally:
                _update_receipt(s, run_id)
            with lock:
                done += 1
                write_progress()
            return outcome

    with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="review-chunk") as pool:
        futures = [pool.submit(one, chunk, snapshot) for chunk, snapshot in prepared]
        results = [f.result() for f in futures]
    if blocked:
        _blocked(db, run_id, blocked[0])
        return None
    if any(r is None for r in results) or _reload(db, run_id) is None:
        return None  # 轮次或任务已被删除
    return results


def _update_receipt(db: Session, run_id: uuid.UUID) -> None:
    """从网关记录重算，包含格式重试；worker 重入时不重复计数，失败或拦截同样保留证据。"""
    # 模型调用期间用户可能已删除任务；重读并锁住仍存在的轮次，不更新会话中的旧对象。
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    if run is None or run.receipt is None:
        return
    rows = list(db.scalars(select(LlmCall).where(LlmCall.run_id == run_id, LlmCall.purpose == 'review')
                           .order_by(LlmCall.created_at, LlmCall.id)))
    sent = [r for r in rows if r.status == 'sent']
    blocked = sum(r.status == 'blocked' for r in rows)
    chars = sum(len(m['content']) for r in sent if r.request_text
                for m in json.loads(r.request_text)['messages'])
    run.receipt = {**run.receipt, 'calls': len(sent), 'chars': chars,
                   'ratios_sent': any('【本地财务比率】' in m['content'] for r in sent if r.request_text
                                      for m in json.loads(r.request_text)['messages'] if m['role'] == 'user'),
                   'rechecks': len(sent) + blocked, 'blocked': blocked,
                   'sent_at': sent[0].created_at.isoformat() if sent else None}
    db.commit()


@job_handler("review")
def handle_review(db: Session, payload: dict) -> None:
    run_review(db, uuid.UUID(str(payload["run_id"])))
