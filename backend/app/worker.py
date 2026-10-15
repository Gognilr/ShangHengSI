"""后台 worker 入口：python -m app.worker。

- 并发数取 WORKER_CONCURRENCY，每个并发一个线程，各自独立的数据库会话，从 jobs 表取任务并按 type 分派给处理函数。
- 处理函数抛出异常：job 记为 failed（last_error 只写异常类名）；没有注册处理函数的类型记为 failed/NO_HANDLER。
  处理函数自己负责在抛出前把 task / run 置为失败状态。
- 处理期间有独立线程定期更新心跳；心跳超时的任务由定时工作重新入队（最多尝试 max_attempts 次）。
- 每分钟执行一次定时工作：重新入队心跳超时的任务、删除已过期会话（open_questions Q-31）。
  其他里程碑通过 register_periodic 增加自己的定时工作（M7 的备份和保留期清理）。
- 日志只写 job id、类型、task id、耗时、错误码，不写文件名和任何文本。
"""
import logging
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import ContextManager

from sqlalchemy import delete, func
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings, setup_logging
from app.db import jobs
from app.db.models import Session as UserSession
from app.security.crypto import check_master_key

logger = logging.getLogger("app.worker")

IDLE_SECONDS = 2  # 队列为空时的轮询间隔
HEARTBEAT_SECONDS = 60  # 处理期间的心跳间隔
PERIODIC_TICK_SECONDS = 5  # 检查定时工作是否到期的间隔
JOIN_TIMEOUT_SECONDS = 30  # 收到停止信号后，等待正在处理的任务的最长时间

# 按 job.type 分派；处理函数签名 (db, payload) -> None
HANDLERS: dict[str, Callable[[Session, dict], None]] = {}

_stop = threading.Event()


def job_handler(type: str):
    """装饰器：注册某个 job 类型的处理函数。"""

    def register(func: Callable[[Session, dict], None]) -> Callable[[Session, dict], None]:
        if type in HANDLERS:
            raise ValueError(f"job 类型重复注册: {type}")
        HANDLERS[type] = func
        return func

    return register


_ocr_lock = threading.Lock()


def ocr_slot() -> ContextManager:
    """全局 OCR 锁：同一时间最多 1 个 OCR 任务（SPEC 21，M3 使用）。"""
    return _ocr_lock


# ---------------------------------------------------------------- 定时工作


@dataclass
class _Periodic:
    name: str
    func: Callable[[Session], None]
    every_seconds: float
    last_run: float | None = field(default=None)  # time.monotonic()；None 表示尚未执行，启动后立即执行一次


_PERIODIC: dict[str, _Periodic] = {}


def register_periodic(name: str, func: Callable[[Session], None], every_seconds: float) -> None:
    """注册定时工作。func 接收一个数据库会话（调用方负责提交），异常只记日志，不影响其他工作。"""
    _PERIODIC[name] = _Periodic(name, func, every_seconds)


def purge_expired_sessions(db: Session) -> None:
    result = db.execute(delete(UserSession).where(UserSession.expires_at <= func.now()))
    db.commit()
    if result.rowcount:
        logger.info("已清理过期会话 %s 个", result.rowcount)


def _requeue_stale(db: Session) -> None:
    n = jobs.requeue_stale(db)
    if n:
        logger.warning("心跳超时的任务 %s 个，已按次数重新入队或置为失败", n)


register_periodic("requeue_stale", _requeue_stale, 60)
register_periodic("purge_expired_sessions", purge_expired_sessions, 60)


def run_periodic(session_factory: sessionmaker[Session], *, force: bool = False) -> list[str]:
    """执行已到期的定时工作，返回本次执行过的名称。force=True 忽略间隔（测试用）。"""
    ran = []
    now = time.monotonic()
    for p in list(_PERIODIC.values()):
        if not force and p.last_run is not None and now - p.last_run < p.every_seconds:
            continue
        p.last_run = now
        ran.append(p.name)
        try:
            with session_factory() as db:
                p.func(db)
        except Exception as exc:  # noqa: BLE001  定时工作失败不能拖垮 worker
            logger.error("定时工作 %s 失败: %s", p.name, type(exc).__name__)
    return ran


# ---------------------------------------------------------------- 处理任务


@contextmanager
def _heartbeating(session_factory: sessionmaker[Session], job_id: int, interval: float) -> Iterator[None]:
    done = threading.Event()

    def beat() -> None:
        while not done.wait(interval):
            try:
                with session_factory() as db:
                    jobs.heartbeat(db, job_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("job %s 心跳失败: %s", job_id, type(exc).__name__)

    t = threading.Thread(target=beat, name=f"heartbeat-{job_id}", daemon=True)
    t.start()
    try:
        yield
    finally:
        done.set()
        t.join()


def process_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    job_type: str,
    payload: dict,
    *,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> None:
    """执行一条已取到的任务，并把结果（done / failed）写回 jobs 表。"""
    task_id = payload.get("task_id") if isinstance(payload, dict) else None
    started = time.monotonic()
    handler = HANDLERS.get(job_type)
    error_code: str | None = None
    if handler is None:
        error_code = "NO_HANDLER"
    else:
        try:
            with _heartbeating(session_factory, job_id, heartbeat_seconds):
                with session_factory() as db:
                    handler(db, payload)
        except Exception as exc:  # noqa: BLE001  只记录类名，不记录消息内容
            error_code = type(exc).__name__
    with session_factory() as db:
        if error_code is None:
            jobs.complete(db, job_id)
        else:
            jobs.fail(db, job_id, error_code)
    ms = int((time.monotonic() - started) * 1000)
    if error_code is None:
        logger.info("job %s 完成 type=%s task=%s 耗时=%sms", job_id, job_type, task_id, ms)
    else:
        logger.error("job %s 失败 type=%s task=%s 耗时=%sms 错误=%s", job_id, job_type, task_id, ms, error_code)


def _worker_loop(
    session_factory: sessionmaker[Session],
    worker_id: str,
    stop: threading.Event,
    idle_seconds: float,
    heartbeat_seconds: float,
) -> None:
    while not stop.is_set():
        try:
            with session_factory() as db:
                job = jobs.claim(db, worker_id)
                claimed = (job.id, job.type, job.payload) if job else None
        except Exception as exc:  # noqa: BLE001  数据库暂时不可用时等待后重试
            logger.error("取任务失败: %s", type(exc).__name__)
            stop.wait(idle_seconds)
            continue
        if claimed is None:
            stop.wait(idle_seconds)
            continue
        try:
            process_job(session_factory, *claimed, heartbeat_seconds=heartbeat_seconds)
        except Exception as exc:  # noqa: BLE001  写回结果失败：保持 running，由心跳超时机制重新入队
            logger.error("job %s 写回结果失败: %s", claimed[0], type(exc).__name__)


def run_worker(
    session_factory: sessionmaker[Session],
    stop: threading.Event,
    *,
    concurrency: int,
    idle_seconds: float = IDLE_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    periodic_tick_seconds: float = PERIODIC_TICK_SECONDS,
    join_timeout: float = JOIN_TIMEOUT_SECONDS,
) -> None:
    """启动并发线程，主线程负责定时工作；stop 置位后等待正在处理的任务结束再返回。"""
    threads = [
        threading.Thread(
            target=_worker_loop,
            args=(session_factory, jobs.make_worker_id(i), stop, idle_seconds, heartbeat_seconds),
            name=f"worker-{i}",
            daemon=True,
        )
        for i in range(concurrency)
    ]
    for t in threads:
        t.start()
    while not stop.is_set():
        run_periodic(session_factory)
        stop.wait(periodic_tick_seconds)
    deadline = time.monotonic() + join_timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))


# ---------------------------------------------------------------- 入口


def _handle_signal(signum: int, _frame: object) -> None:
    logger.info("收到停止信号 %s，准备退出", signum)
    _stop.set()


def main() -> None:
    setup_logging()
    # 主密钥缺失或不是 32 字节时拒绝启动（SPEC 17.3、open_questions Q-08）
    check_master_key()
    from app.db.session import get_sessionmaker

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    settings = get_settings()
    logger.info("worker 启动，版本 %s，并发 %s", settings.app_version, settings.worker_concurrency)
    run_worker(get_sessionmaker(), _stop, concurrency=max(1, settings.worker_concurrency))
    logger.info("worker 已退出")


if __name__ == "__main__":
    main()
