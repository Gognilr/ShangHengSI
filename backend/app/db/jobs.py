"""后台任务队列（SPEC 7.5、18.19，设计 docs/design/M2.md 第二节）。

基于 jobs 表，取任务用 FOR UPDATE SKIP LOCKED，多个 worker 线程/进程不会取到同一条。
本模块的函数不记录 payload 内容；last_error 只写异常类名或错误码，不写消息内容。
"""
import os
import platform
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import Job

# 心跳超过此时间未更新的 running 任务视为 worker 异常退出（SPEC 7.5）
STALE_AFTER = timedelta(minutes=15)


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


def claim(db: Session, worker_id: str) -> Job | None:
    """取一条到期的 queued 任务：置 running、attempts+1、locked_by、heartbeat_at，并提交。"""
    job = db.execute(
        select(Job)
        .where(Job.status == "queued", Job.run_after <= func.now())
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
    """置 failed。error_code 只能是异常类名或错误码，不得带消息内容。"""
    db.execute(
        update(Job).where(Job.id == job_id).values(status="failed", last_error=error_code, finished_at=func.now())
    )
    db.commit()


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
