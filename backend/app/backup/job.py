"""备份的触发：backup 处理函数、立即备份入队、每分钟的定时检查（SPEC 16.2，设计 docs/design/M7.md 3.4）。

- 立即备份：enqueue_backup() 在已有排队或运行中的备份时返回 None（接口据此返回 409 BACKUP_RUNNING）。
- 定时：本地时间（Asia/Shanghai）已过 backup.time，且今天还没有创建过 trigger=schedule 的 backup job 时入队一次；
  worker 在备份时间之后才启动的，当天补做一次（Q-104）。
"""
import logging
import re
import uuid
from datetime import datetime, time as dtime

from sqlalchemy import exists, or_, select, text
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.backup.service import local_tz, run_backup
from app.db import jobs
from app.db.base import utcnow
from app.db.models import BackupRecord, Job
from app.worker import job_handler, register_periodic

logger = logging.getLogger("app.backup")

ENQUEUE_LOCK_KEY = 0x41495256_0002  # 串行化“检查是否已有备份 + 入队”
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
TRIGGERS = ("manual", "schedule")


def parse_backup_time(value: object) -> dtime | None:
    m = _TIME_RE.match(value) if isinstance(value, str) else None
    return dtime(int(m.group(1)), int(m.group(2))) if m else None


def backup_busy(db: Session) -> bool:
    """有排队或运行中的 backup job，或有 running 的备份记录。"""
    return bool(db.scalar(select(or_(
        exists().where(Job.type == "backup", Job.status.in_(("queued", "running"))),
        exists().where(BackupRecord.status == "running"),
    ))))


def enqueue_backup(db: Session, *, trigger: str, user_id: uuid.UUID | None) -> Job | None:
    """加入一个 backup job（不提交）。已有备份排队或进行中时返回 None。"""
    if trigger not in TRIGGERS:
        raise ValueError("trigger")
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ENQUEUE_LOCK_KEY})
    if backup_busy(db):
        return None
    return jobs.enqueue(db, "backup", {"trigger": trigger, "user_id": str(user_id) if user_id else None})


@job_handler("backup")
def handle_backup(db: Session, payload: dict) -> None:
    trigger = payload.get("trigger")
    if trigger not in TRIGGERS:
        raise ValueError("payload.trigger")
    raw_user = payload.get("user_id")
    user_id = uuid.UUID(raw_user) if raw_user else None
    run_backup(db, trigger=trigger, user_id=user_id)


def check_schedule(db: Session, now: datetime | None = None) -> bool:
    """到点且今天尚未入队定时备份时入队，返回是否入队。"""
    local = (now or utcnow()).astimezone(local_tz())
    at = parse_backup_time(get_setting(db, "backup.time"))
    if at is None:
        logger.error("备份时间设置无效，跳过定时备份")
        return False
    if local.time() < at:
        return False
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    done_today = db.scalar(select(exists().where(
        Job.type == "backup", Job.payload["trigger"].astext == "schedule", Job.created_at >= day_start)))
    if done_today:
        return False
    job = enqueue_backup(db, trigger="schedule", user_id=None)
    if job is None:  # 立即备份正在进行：下一分钟再检查
        db.rollback()
        return False
    db.commit()
    logger.info("已加入定时备份 job %s", job.id)
    return True


register_periodic("backup_schedule", check_schedule, 60)
