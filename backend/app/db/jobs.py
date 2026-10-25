"""后台任务队列（SPEC 7.5、18.19，设计 docs/design/M2.md 第二节）。

基于 jobs 表，取任务用 FOR UPDATE SKIP LOCKED，多个 worker 线程/进程不会取到同一条。
本模块的函数不记录 payload 内容；last_error 只写异常类名或错误码，不写消息内容。
"""
import os
import platform
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import BackupRecord, Job, ReviewRun, Task

# 心跳超过此时间未更新的 running 任务视为 worker 异常退出（SPEC 7.5）
STALE_AFTER = timedelta(minutes=15)

# job 在处理函数之外最终失败时写给用户的文字；处理函数内部失败也用同样的文字
PARSE_INTERNAL_MSG = "解析时发生内部错误，请重新上传；仍然失败请联系管理员"
REVIEW_INTERNAL_MSG = "审核时发生内部错误，请重新审核；仍然失败请联系管理员"
BACKUP_INTERRUPTED_MSG = "备份中断"


def make_worker_id(index: int) -> str:
    """worker 实例标识，写入 jobs.locked_by（varchar(64)）。"""
    return f"{platform.node()[:30]}-{os.getpid()}-{index}"[:64]


def enqueue(db: Session, type: str, payload: dict, *, run_after: datetime | None = None) -> Job:
    """加入队列。只 add 并 flush（拿到 id），不提交，由调用方与业务数据在同一事务中提交。"""
    job = Job(type=type, payload=payload)
    if run_after is not None:
        job.run_after = run_after
    db.add(job)
    db.flush()
    return job


def claim(db: Session, worker_id: str, types: tuple[str, ...]) -> Job | None:
    """按类型取一条到期的 queued 任务：置 running、更新锁与心跳，并提交。"""
    job = db.execute(
        select(Job)
        .where(Job.status == "queued", Job.run_after <= func.now(), Job.type.in_(types))
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        db.rollback()
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_by = worker_id
    job.heartbeat_at = func.now()
    db.commit()
    db.refresh(job)
    return job


def heartbeat(db: Session, job_id: int) -> None:
    db.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(heartbeat_at=func.now()))
    db.commit()


def complete(db: Session, job_id: int) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(status="done", finished_at=func.now()))
    db.commit()


def fail(db: Session, job_id: int, error_code: str) -> None:
    """置 failed，并按 finalize_failed 收尾业务状态。error_code 只能是异常类名或错误码，不得带消息内容。"""
    job = db.execute(
        update(Job).where(Job.id == job_id)
        .values(status="failed", last_error=error_code, finished_at=func.now())
        .returning(Job.type, Job.payload)
    ).one_or_none()
    if job is not None:
        finalize_failed(db, job.type, job.payload)
    db.commit()


def _uuid(payload: object, key: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(payload[key]))  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return None


def finalize_failed(db: Session, job_type: str, payload: object) -> None:
    """job 最终置 failed 后收尾业务状态，不提交（open_questions Q-53）。

    处理函数之外的失败（未注册处理函数、心跳超时用完次数）不会经过处理函数的失败分支，
    任务会一直停在“解析中”“审核中”。这里按状态守卫收尾，结果与处理函数内部失败一致；
    处理函数已经收尾过的（状态已不是进行中）不再改动。
    """
    if job_type == "parse":
        task_id = _uuid(payload, "task_id")
        task = db.get(Task, task_id, with_for_update=True) if task_id else None
        if task is not None and task.status == "parsing":
            task.status = "parse_failed"
            task.error_message = PARSE_INTERNAL_MSG
    elif job_type == "review":
        run_id = _uuid(payload, "run_id")
        run = db.get(ReviewRun, run_id, with_for_update=True) if run_id else None
        if run is not None and run.status in ("queued", "running"):
            run.status, run.error_message, run.finished_at = "failed", REVIEW_INTERNAL_MSG, utcnow()
            task = db.get(Task, run.task_id, with_for_update=True)
            if task is not None and task.status == "reviewing":
                task.status = "reviewed"
    elif job_type == "backup":
        # 同一时间只有一个备份（advisory lock），job 最终失败时仍为 running 的记录就是它留下的（M7 设计 3.4）
        db.execute(update(BackupRecord).where(BackupRecord.status == "running")
                   .values(status="failed", error_message=BACKUP_INTERRUPTED_MSG, finished_at=utcnow()))


def requeue_stale(db: Session, *, stale_after: timedelta = STALE_AFTER) -> int:
    """心跳超时的 running 任务：未用完次数的置回 queued，否则置 failed。返回处理的条数。"""
    cutoff = func.now() - stale_after
    stale = (
        db.execute(
            select(Job)
            .where(Job.status == "running", func.coalesce(Job.heartbeat_at, Job.created_at) < cutoff)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for job in stale:
        job.locked_by = None
        if job.attempts < job.max_attempts:
            job.status = "queued"
        else:
            job.status = "failed"
            job.last_error = "HEARTBEAT_TIMEOUT"
            job.finished_at = func.now()
            finalize_failed(db, job.type, job.payload)
    db.commit()
    return len(stale)


def queue_position(db: Session, job_id: int) -> int | None:
    """同类型、queued、id 更小的数量 + 1；任务不在排队时返回 None。"""
    job = db.get(Job, job_id)
    if job is None or job.status != "queued":
        return None
    ahead = db.execute(
        select(func.count()).select_from(Job).where(Job.type == job.type, Job.status == "queued", Job.id < job.id)
    ).scalar_one()
    return ahead + 1
