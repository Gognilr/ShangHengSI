"""V2-C-02 分层保留清理：逐任务加锁，单次最多处理 500 个不同任务。

原件、结果、未完成任务、外发正文、审计依次清理，最后记录汇总审计。
单任务失败只记录标识和异常类名，不输出文件名、路径或内容。
"""
import logging
from datetime import datetime, time as dtime, timedelta

from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.audit import service as audit
from app.backup.service import local_tz
from app.db import jobs
from app.db.base import utcnow
from app.db.models import AuditLog, DocNode, Job, LlmCall, Task, TaskFile
from app.tasks import storage
from app.tasks.service import delete_task
from app.worker import job_handler, register_periodic

logger = logging.getLogger("app.backup")

RUN_AFTER = dtime(3, 30)
ELIGIBLE_STATUS = ("reviewed", "completed_local")
LAYERS = ("original", "result", "outbound", "audit", "unfinished")
TASK_LIMIT = 500


def _days(db: Session, layer: str = "original") -> int:
    try:
        return max(int(get_setting(db, f"retention.{layer}_days") or 0), 0)
    except (TypeError, ValueError):
        return 0


def check_retention(db: Session, now: datetime | None = None) -> bool:
    """到点、保留期已设置且今天尚未入队时入队，返回是否入队。"""
    local = (now or utcnow()).astimezone(local_tz())
    if local.time() < RUN_AFTER or not any(_days(db, layer) for layer in LAYERS):
        return False
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if db.scalar(select(exists().where(Job.type == "retention", Job.created_at >= day_start))):
        return False
    job = jobs.enqueue(db, "retention", {})
    db.commit()
    logger.info("已加入分层保留期清理 job %s", job.id)
    return True


def _pending(task_id) -> object:
    return exists().where(TaskFile.task_id == task_id, TaskFile.original_deleted_at.is_(None))


def _layer_conditions(layer: str) -> list:
    """V2Q-74：重新审核后未提交（待确认且有旧轮次）归原件层、结果层；未完成层只含从未提交审核的任务。"""
    if layer == "unfinished":
        return [Task.status.in_(("parse_failed", "awaiting_confirm")), Task.latest_run_id.is_(None)]
    return [or_(Task.status.in_(ELIGIBLE_STATUS),
                and_(Task.status == "awaiting_confirm", Task.latest_run_id.is_not(None)))]


def run_retention(db: Session, now: datetime | None = None) -> dict:
    """执行分层清理，返回各类处理数量。"""
    now = now or utcnow()
    days = {layer: _days(db, layer) for layer in LAYERS}
    counts = dict(tasks=0, files=0, results=0, unfinished=0, outbound=0, audit=0)
    if not any(days.values()):
        return counts
    processed = set()
    for layer in ("original", "result", "unfinished"):
        if not days[layer]:
            continue
        cutoff = now - timedelta(days=days[layer])
        conditions = [*_layer_conditions(layer), Task.last_activity_at < cutoff]
        if layer == "original":
            conditions.append(_pending(Task.id))
        # 同一任务跨层只占一个名额；每类最多取 500 条候选，剩余留到下次。
        ids = list(db.scalars(select(Task.id).where(*conditions)
                             .order_by(Task.last_activity_at, Task.id).limit(TASK_LIMIT)))
        db.rollback()
        for tid in ids:
            if tid not in processed and len(processed) >= TASK_LIMIT:
                continue
            try:
                task = db.scalar(select(Task).where(Task.id == tid, *conditions)
                                 .with_for_update(skip_locked=True).execution_options(populate_existing=True))
                if task is None:
                    db.rollback()
                    continue
                processed.add(tid)
                if layer == "original":
                    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == tid,
                                                                  TaskFile.original_deleted_at.is_(None))))
                    for file in files:
                        storage.delete_original(file.storage_key)
                        file.original_deleted_at = now
                    db.execute(update(DocNode).where(DocNode.task_id == tid).values(text_enc=None))
                    task.user_note_enc = None
                    if task.status == "awaiting_confirm":
                        # 原件已删，不能再提交；回到重新审核前的已审核状态，旧结果照常可看。
                        task.status = "reviewed"
                    db.commit()
                    counts["tasks"] += 1
                    counts["files"] += len(files)
                else:
                    # 与用户删除共用原件、子表、排队任务和外发文件名清理，系统无操作人。
                    delete_task(db, task, None, None)
                    counts["results" if layer == "result" else "unfinished"] += 1
            except Exception as exc:
                db.rollback()
                logger.error("保留期清理失败 task=%s layer=%s 错误=%s", tid, layer, type(exc).__name__)
    if days["outbound"]:
        result = db.execute(update(LlmCall).where(
            LlmCall.created_at < now - timedelta(days=days["outbound"]),
            or_(LlmCall.request_text.is_not(None), LlmCall.response_text.is_not(None)),
        ).values(request_text=None, response_text=None))
        counts["outbound"] = result.rowcount
    if days["audit"]:
        result = db.execute(delete(AuditLog).where(AuditLog.created_at < now - timedelta(days=days["audit"])))
        counts["audit"] = result.rowcount
    audit.record(db, "retention_run", target_type=None, detail=counts)
    db.commit()
    logger.info("分层保留期清理完成 数量=%s", counts)
    return counts


@job_handler("retention")
def handle_retention(db: Session, payload: dict) -> None:
    run_retention(db)


register_periodic("retention_schedule", check_retention, 60)
