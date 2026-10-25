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
import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.db import jobs
from app.db.base import utcnow
from app.db.models import Finding, RequirementResult, ReviewRun, Task
from app.llm.gateway import GatewayError, format_reasons
from app.review.chunking import Chunk, effective_limit, outbound_lines, split_chunks
from app.review.coverage import build_coverage
from app.review.prompt import ReviewContext, split_outbound
from app.review.service import build_context
from app.review.validate import (
    ChunkOutcome,
    DocNodeView,
    OutputError,
    ValidatedReview,
    load_node_views,
    merge_reviews,
    review_once,
)
from app.security import crypto
from app.worker import job_handler

logger = logging.getLogger("app.review.job")

MSG_LOCAL_ONLY = "该任务的外发策略为不外发，未调用模型"
MSG_BAD_OUTPUT = "模型返回的内容不符合要求，请稍后重新审核"
MSG_INTERNAL = jobs.REVIEW_INTERNAL_MSG


def _reload(db: Session, run_id: uuid.UUID) -> tuple[ReviewRun, Task] | None:
    """放弃未提交的改动后重读轮次和任务；任一已被删除时返回 None。"""
    db.rollback()
    run = db.get(ReviewRun, run_id, populate_existing=True)
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
    run.status, run.error_message, run.finished_at = status, message, utcnow()
    run.tokens_in += tokens_in
    run.tokens_out += tokens_out
    if chunk_count is not None:
        run.chunk_count = chunk_count
    if failed is not None:
        run.failed_chunks = failed
    run.coverage = None if status == "blocked" else coverage  # blocked 未发送；其他带结果的终态写入覆盖说明
    task.status = task_status
    task.last_activity_at = utcnow()
    db.commit()
    logger.info("run %s 结束 状态=%s", run_id, status)


def _failed_chunks(outcomes: list[ChunkOutcome]) -> list[dict]:
    return [{"index": o.chunk.index, "first_loc": o.chunk.first_loc, "last_loc": o.chunk.last_loc, "reason": o.error}
            for o in outcomes if o.review is None]


def _partial_message(failed: list[dict]) -> str:
    return f"第 {'、'.join(str(f['index']) for f in failed)} 段审核失败，结果不完整"


def _save(db: Session, run_id: uuid.UUID, ctx: ReviewContext, review: ValidatedReview, *, chunk_count: int = 1,
          failed: list[dict] | None = None) -> None:
    failed = failed or []
    found = _reload(db, run_id)
    if found is None:
        return
    run, task = found
    for rr in review.requirement_results:
        db.add(RequirementResult(run_id=run.id, req_id=rr.req_id, req_source=rr.req_source, req_text=rr.req_text,
                                 dimension=rr.dimension, verdict=rr.verdict, note=rr.note))
    for seq, f in enumerate(review.findings, start=1):
        db.add(Finding(
            run_id=run.id, task_id=task.id, seq=seq, title=f.title, category=f.category, severity=f.severity,
            req_ids=f.req_ids, locs=f.locs, missing=f.missing, cite_status=f.cite_status, quote_model=f.quote_model,
            quote_snapshot_enc=crypto.encrypt_text(f.quote_snapshot) if f.quote_snapshot is not None else None,
            problem=f.problem, impact=f.impact, suggestion=f.suggestion, suggested_text=f.suggested_text,
            basis=f.basis,
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
    task.status = "reviewed"
    task.high_count = high
    task.error_message = None
    task.last_completed_at = now
    task.last_activity_at = now
    try:
        db.commit()
    except IntegrityError:  # 保存期间任务被删除
        if _reload(db, run_id) is not None:
            raise
        return
    logger.info("run %s 结束 状态=%s 问题=%s 高风险=%s", run_id, run.status, len(review.findings), high)


def run_review(db: Session, run_id: uuid.UUID) -> None:
    run = db.get(ReviewRun, run_id)
    task = db.get(Task, run.task_id) if run is not None else None
    if run is None or task is None:  # 已删除
        return
    if run.status not in ("queued", "running"):  # 重复投递；running 是 worker 异常退出后的重新入队
        return
    if task.policy != "ai":  # A-04：创建轮次的接口已拦截，这里是第二道防线
        _finish(db, run_id, status="failed", message=MSG_LOCAL_ONLY, task_status="reviewed")
        return
    run.status, run.started_at, run.finished_at, run.error_message = "running", utcnow(), None, None
    run.model_name = str(get_setting(db, "model.name") or "").strip()[:100] or run.model_name
    db.commit()
    logger.info("run %s 开始", run_id)

    ctx: ReviewContext | None = None
    try:
        ctx = build_context(db, task, stance=run.stance, source=run.source, user_note=run.user_note,
                            main_doc_type=run.main_doc_type)
        _, body = split_outbound(ctx.outbound_text)
        chunks = split_chunks(outbound_lines(body, ctx.boundary_locs),
                              effective_limit(get_setting(db, "model.chunk_chars")))
        total = len(chunks)
        nodes = load_node_views(db, task.id, ctx.quote_tables)
        if not _set_progress(db, run_id, 0, total):
            return
        outcomes: list[ChunkOutcome] = []
        for chunk in chunks:
            outcome = _review_chunk(db, ctx, run_id, chunk, nodes)
            if outcome is None:  # 出口复检拦截：整轮 blocked，剩余分段不发送（_review_chunk 已处理）
                return
            outcomes.append(outcome)
            if not _set_progress(db, run_id, chunk.index, total):
                return  # 轮次或任务已被删除
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
    run.chunk_count = total
    run.coverage = {"progress": {"done": done, "total": total}}
    db.commit()
    return True


def _review_chunk(db: Session, ctx: ReviewContext, run_id: uuid.UUID, chunk: Chunk,
                  nodes: dict[str, DocNodeView]) -> ChunkOutcome | None:
    """审核一段（只有 1 段时消息与不分段相同）。网关拦截时把整轮置为 blocked 并返回 None；其他失败返回失败的结果。"""
    run = db.get(ReviewRun, run_id)
    try:
        review = review_once(db, ctx, run=run, user_id=run.created_by, chunk=chunk if chunk.total > 1 else None,
                             nodes=nodes)
    except GatewayError as exc:
        if exc.code == "BLOCKED":
            message = f"出口复检发现未脱敏的信息，已拦截，未发送（{format_reasons(exc.reasons)}）"
            _finish(db, run_id, status="blocked", message=message, task_status="awaiting_confirm")
            return None
        return ChunkOutcome(chunk, None, str(exc))
    except OutputError as exc:
        return ChunkOutcome(chunk, None, MSG_BAD_OUTPUT, exc.tokens_in, exc.tokens_out)
    return ChunkOutcome(chunk, review)


@job_handler("review")
def handle_review(db: Session, payload: dict) -> None:
    run_review(db, uuid.UUID(str(payload["run_id"])))
