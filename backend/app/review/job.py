"""review 处理函数（SPEC 7.1 第 4 步、7.3、7.5，设计 docs/design/M2.md 第一、八节）。

worker 取到 review 任务后：run=running → 拼接上下文 → review_once（经网关：上限检查 → 出口复检 → 发送 → 记录）→ 写结果。
- 成功：全部要求都有非 unable 的结论时 run=completed，否则 partial；task=reviewed，high_count=本轮高风险问题数。
- 网关拦截（BLOCKED）：run=blocked，error_message 只写类别和位置；task 回到 awaiting_confirm，用户处理后可重新提交（T-07）。
- 其他网关错误、输出无法解析：run=failed；task=reviewed。其他异常：同样置 failed 后再抛出，job 记为 failed。
- 外发策略不是 ai 的任务一律不调用模型（A-04）；任务或轮次已被删除时直接结束。
- 模型返回的文字以代号形式入库，原文引用快照加密存储。日志只写轮次 id、状态、数量，不写任何文本。
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
from app.review.service import build_context
from app.review.validate import OutputError, ValidatedReview, review_once
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
            tokens_in: int = 0, tokens_out: int = 0) -> None:
    """把轮次置为 blocked / failed，并让任务进入 task_status。"""
    found = _reload(db, run_id)
    if found is None:
        return
    run, task = found
    run.status, run.error_message, run.finished_at = status, message, utcnow()
    run.tokens_in += tokens_in
    run.tokens_out += tokens_out
    task.status = task_status
    db.commit()
    logger.info("run %s 结束 状态=%s", run_id, status)


def _save(db: Session, run_id: uuid.UUID, review: ValidatedReview) -> None:
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
    run.status = "partial" if any(r.verdict == "unable" for r in review.requirement_results) else "completed"
    run.error_message = None
    run.finished_at = now
    task.status = "reviewed"
    task.high_count = high
    task.error_message = None
    task.last_completed_at = now
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

    try:
        ctx = build_context(db, task, stance=run.stance, source=run.source, user_note=run.user_note,
                            main_doc_type=run.main_doc_type)
        review = review_once(db, ctx, run=run, user_id=run.created_by)
    except GatewayError as exc:
        if exc.code == "BLOCKED":
            message = f"出口复检发现未脱敏的信息，已拦截，未发送（{format_reasons(exc.reasons)}）"
            _finish(db, run_id, status="blocked", message=message, task_status="awaiting_confirm")
        else:
            _finish(db, run_id, status="failed", message=str(exc), task_status="reviewed")
        return
    except OutputError as exc:
        _finish(db, run_id, status="failed", message=MSG_BAD_OUTPUT, task_status="reviewed",
                tokens_in=exc.tokens_in, tokens_out=exc.tokens_out)
        return
    except Exception:
        _finish(db, run_id, status="failed", message=MSG_INTERNAL, task_status="reviewed")
        raise  # job 记为 failed，last_error 只有异常类名
    _save(db, run_id, review)


@job_handler("review")
def handle_review(db: Session, payload: dict) -> None:
    run_review(db, uuid.UUID(str(payload["run_id"])))
