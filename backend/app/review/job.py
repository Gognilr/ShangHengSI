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
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import gateway, usage
from app.llm.gateway import load_model
from app.db import jobs
from app.db.base import utcnow
from app.db.models import Finding, LlmCall, RequirementResult, ReviewRun, Task
from app.llm.gateway import GatewayError, format_reasons
from app.review.chunking import Chunk, effective_limit, outbound_lines, split_chunks
from app.review.coverage import build_coverage
from app.review.prompt import ReviewContext, build_messages, split_outbound
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


def _save(db: Session, run_id: uuid.UUID, ctx: ReviewContext, review: ValidatedReview, *, chunk_count: int = 1,
          failed: list[dict] | None = None) -> None:
    failed = failed or []
    found = _reload(db, run_id)
    if found is None:
        return
    run, task = found
    for rr in review.requirement_results:
        db.add(RequirementResult(run_id=run.id, req_id=rr.req_id, req_source=rr.req_source, req_text=rr.req_text,
                                 dimension=rr.dimension, verdict=rr.verdict, note=rr.note,
                                 unable_reason=normalize_unable_reason(rr.verdict, rr.unable_reason)))
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
    try:
        model = load_model(db, run.model_id)
    except GatewayError:
        _finish(db, run_id, status="failed", message="所选模型已停用，请重新审核并换一个模型", task_status="reviewed")
        return
    run.model_name = model.name
    db.commit()
    logger.info("run %s 开始", run_id)

    ctx: ReviewContext | None = None
    try:
        ctx = build_context(db, task, stance=run.stance, source=run.source, user_note=run.user_note,
                            main_doc_type=run.main_doc_type)
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
        prepare(db, ctx, part, nodes)
        prepare_template(db, ctx, part)
        prepared.append((chunk, copy.copy(ctx)))  # 每段重新赋值 law_refs / template_section，浅拷贝即可
    db.commit()  # 范本准备可能锁住任务行与对照表，并发发送前释放
    return prepared


def _blocked(db: Session, run_id: uuid.UUID, exc: GatewayError) -> None:
    message = f"出口复检发现未脱敏的信息，已拦截，未发送（{format_reasons(exc.reasons)}）"
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
    """接近调用上限时逐段发送，保证超限的分段准确地记为失败（并发会在记录前同时通过上限检查）。"""
    if total <= 1:
        return 1
    left = [v for v in usage.remaining_calls(db, run.created_by, run.model_id).values() if v is not None]
    return CHUNK_PARALLEL if all(v >= 2 * total for v in left) else 1  # 2 倍：每段可能有一次格式重试


def _send_chunks(db: Session, run_id: uuid.UUID, prepared: list[tuple[Chunk, ReviewContext]],
                 nodes: dict[str, DocNodeView], total: int) -> list[ChunkOutcome] | None:
    """各段经网关发送（每次发送前网关照常上限检查、出口复检、记录），最多 CHUNK_PARALLEL 段同时进行。
    进度在复检通过、即将发出时就写入（sent），收到回复后写 done，界面据此如实显示（V31-U5F-01）。"""
    bind = db.get_bind()
    run = db.get(ReviewRun, run_id)
    parallel = _parallel(db, run, total)
    lock = threading.Lock()
    stop = threading.Event()
    sent: set[int] = set()
    done = 0
    blocked: list[GatewayError] = []

    def write_progress() -> None:  # 调用方持有 lock：按顺序写，旧的计数不会覆盖新的
        with Session(bind=bind) as s:
            row = s.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
            if row is not None and row.status == "running":
                row.chunk_count = total
                row.coverage = {"progress": {"done": done, "total": total, "sent": len(sent)}}
            s.commit()

    def mark_sent(index: int) -> None:
        with lock:
            if index not in sent:
                sent.add(index)
                write_progress()

    def one(chunk: Chunk, snapshot: ReviewContext) -> ChunkOutcome | None:
        nonlocal done
        if stop.is_set():
            return None
        with Session(bind=bind, expire_on_commit=False) as s:
            row = s.get(ReviewRun, run_id)
            task = s.get(Task, snapshot.task.id)
            if row is None or task is None:
                return None
            snapshot.task = task  # 各线程只用自己会话中的对象
            part = chunk if chunk.total > 1 else None
            try:
                review = review_once(s, snapshot, run=row, user_id=row.created_by, chunk=part, nodes=nodes,
                                     on_send=lambda: mark_sent(chunk.index))
                outcome = ChunkOutcome(chunk, review)
            except GatewayError as exc:
                if exc.code == "BLOCKED":
                    stop.set()
                    blocked.append(exc)
                    return None
                outcome = ChunkOutcome(chunk, None, str(exc))
            except OutputError as exc:
                outcome = ChunkOutcome(chunk, None, MSG_BAD_OUTPUT, exc.tokens_in, exc.tokens_out)
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
