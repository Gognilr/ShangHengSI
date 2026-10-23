"""原件保留期清理（SPEC 16.4、17.8，设计 docs/design/M7.md 第八节，Q-109）。

- 定时：每分钟检查，本地时间（Asia/Shanghai）已过 03:30 且今天还没有创建过 retention job 时入队一次；retention.original_days=0 时不入队。
- 处理：last_completed_at 早于截止时间、状态为 reviewed / completed_local 且仍有未删除原件的任务，逐个加锁后删除原件文件、
  清空全部节点原文（text_enc）、写 original_deleted_at。单个任务失败只记日志（任务 id 与错误类名）并继续下一个。
- 不删除也不修改脱敏文本、对照表、轮次、问题（含引用快照）、本地校验结果、对话。
"""
import logging
from datetime import datetime, time as dtime, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.audit import service as audit
from app.backup.service import local_tz
from app.db import jobs
from app.db.base import utcnow
from app.db.models import DocNode, Job, Task, TaskFile
from app.tasks import storage
from app.worker import job_handler, register_periodic

logger = logging.getLogger("app.backup")

RUN_AFTER = dtime(3, 30)
ELIGIBLE_STATUS = ("reviewed", "completed_local")


def _days(db: Session) -> int:
    try:
        return max(int(get_setting(db, "retention.original_days") or 0), 0)
    except (TypeError, ValueError):
        return 0


def check_retention(db: Session, now: datetime | None = None) -> bool:
    """到点、保留期已设置且今天尚未入队时入队，返回是否入队。"""
    local = (now or utcnow()).astimezone(local_tz())
    if local.time() < RUN_AFTER or _days(db) <= 0:
        return False
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if db.scalar(select(exists().where(Job.type == "retention", Job.created_at >= day_start))):
        return False
    job = jobs.enqueue(db, "retention", {})
    db.commit()
    logger.info("已加入原件保留期清理 job %s", job.id)
    return True


def _pending(task_id) -> object:
    return exists().where(TaskFile.task_id == task_id, TaskFile.original_deleted_at.is_(None))


def run_retention(db: Session, now: datetime | None = None) -> dict:
    """执行一次清理，返回 {"tasks": n, "files": n}。"""
    now = now or utcnow()
    days = _days(db)
    if days <= 0:
        return {"tasks": 0, "files": 0}
    cutoff = now - timedelta(days=days)
    task_ids = list(db.scalars(
        select(Task.id).where(Task.last_completed_at < cutoff, Task.status.in_(ELIGIBLE_STATUS),
                              _pending(Task.id)).order_by(Task.last_completed_at)))
    done_tasks = done_files = 0
    for tid in task_ids:
        try:
            # 锁任务行后重新检查条件：期间可能被重新审核、删除
            task = db.execute(select(Task).where(Task.id == tid).with_for_update()
                              .execution_options(populate_existing=True)).scalar_one_or_none()
            if (task is None or task.status not in ELIGIBLE_STATUS or task.last_completed_at is None
                    or task.last_completed_at >= cutoff):
                db.rollback()
                continue
            files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == tid,
                                                           TaskFile.original_deleted_at.is_(None))))
            for f in files:
                storage.delete_original(f.storage_key)
                f.original_deleted_at = now
            db.execute(update(DocNode).where(DocNode.task_id == tid).values(text_enc=None))
            task.user_note_enc = None
            db.commit()
            done_tasks += 1
            done_files += len(files)
        except Exception as exc:  # 单个任务失败不影响其他任务；日志不含内容
            db.rollback()
            logger.error("原件保留期清理失败 task=%s 错误=%s", tid, type(exc).__name__)
    audit.record(db, "retention_run", target_type=None, detail={"tasks": done_tasks, "files": done_files})
    db.commit()
    logger.info("原件保留期清理完成 任务=%s 文件=%s", done_tasks, done_files)
    return {"tasks": done_tasks, "files": done_files}


@job_handler("retention")
def handle_retention(db: Session, payload: dict) -> None:
    run_retention(db)


register_periodic("retention_schedule", check_retention, 60)
