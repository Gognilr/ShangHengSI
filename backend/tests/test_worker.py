"""worker 框架（M2-01）：分派、失败处理、心跳、定时工作、停止信号。需要 TEST_DATABASE_URL（OCR 锁测试除外）。"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import worker
from app.db import jobs
from app.db.models import Job
from app.db.models import Session as UserSession


def _wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _statuses(sm) -> dict[int, str]:
    with sm() as db:
        return {j.id: j.status for j in db.scalars(select(Job))}


class _Running:
    """在后台线程中运行 worker，退出时停止并等待。"""

    def __init__(self, sm, **kw):
        self.stop = threading.Event()
        kw.setdefault("concurrency", 2)
        kw.setdefault("idle_seconds", 0.02)
        kw.setdefault("periodic_tick_seconds", 0.05)
        self.thread = threading.Thread(target=worker.run_worker, args=(sm, self.stop), kwargs=kw)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(10)
        assert not self.thread.is_alive()


@pytest.fixture
def handlers(monkeypatch):
    """隔离的处理函数表和定时工作表，测试结束自动恢复。"""
    table: dict = {}
    monkeypatch.setattr(worker, "HANDLERS", table)
    monkeypatch.setattr(worker, "_PERIODIC", {})
    return table


def _enqueue(sm, type="parse", payload=None) -> int:
    with sm() as db:
        job = jobs.enqueue(db, type, payload or {"task_id": "t1"})
        db.commit()
        return job.id


# ---------------------------------------------------------------- 分派


def test_job_handler_registers_and_rejects_duplicate(handlers):
    @worker.job_handler("parse")
    def h(db, payload):
        pass

    assert handlers["parse"] is h
    with pytest.raises(ValueError):
        worker.job_handler("parse")(h)


def test_dispatch_runs_each_job_exactly_once_concurrently(handlers, db_sessionmaker):
    seen: list[int] = []
    lock = threading.Lock()

    def handle(db, payload):
        with lock:
            seen.append(payload["n"])
        time.sleep(0.01)

    handlers["parse"] = handle
    ids = [_enqueue(db_sessionmaker, payload={"n": n}) for n in range(12)]
    with _Running(db_sessionmaker, concurrency=3):
        assert _wait(lambda: set(_statuses(db_sessionmaker).values()) == {"done"})
    assert sorted(seen) == list(range(12))
    assert set(_statuses(db_sessionmaker)) == set(ids)


def test_handler_exception_marks_failed_without_message(handlers, db_sessionmaker, caplog):
    secret = "合同金额 USD 123456 客户甲公司"

    def boom(db, payload):
        raise ValueError(secret)

    handlers["parse"] = boom
    jid = _enqueue(db_sessionmaker)
    with caplog.at_level(logging.DEBUG, logger="app.worker"):
        with _Running(db_sessionmaker):
            assert _wait(lambda: _statuses(db_sessionmaker)[jid] == "failed")
    with db_sessionmaker() as db:
        job = db.get(Job, jid)
    assert job.last_error == "ValueError" and job.attempts == 1 and job.finished_at is not None
    assert secret not in caplog.text and "123456" not in caplog.text
    assert "ValueError" in caplog.text


def test_handler_failure_does_not_stop_worker(handlers, db_sessionmaker):
    def handle(db, payload):
        if payload["bad"]:
            raise RuntimeError("x")

    handlers["parse"] = handle
    bad = _enqueue(db_sessionmaker, payload={"bad": True})
    good = _enqueue(db_sessionmaker, payload={"bad": False})
    with _Running(db_sessionmaker, concurrency=1):
        assert _wait(lambda: _statuses(db_sessionmaker)[good] == "done")
    assert _statuses(db_sessionmaker)[bad] == "failed"


def test_unregistered_type_fails_with_no_handler(handlers, db_sessionmaker):
    jid = _enqueue(db_sessionmaker, "backup")
    with _Running(db_sessionmaker):
        assert _wait(lambda: _statuses(db_sessionmaker)[jid] == "failed")
    with db_sessionmaker() as db:
        assert db.get(Job, jid).last_error == "NO_HANDLER"


def test_handler_uncommitted_changes_rolled_back_on_failure(handlers, db_sessionmaker):
    """处理函数异常时，它在自己会话里未提交的改动不会落库。"""

    def handle(db, payload):
        db.add(Job(type="review", payload={"leak": True}))
        db.flush()
        raise RuntimeError

    handlers["parse"] = handle
    jid = _enqueue(db_sessionmaker)
    with _Running(db_sessionmaker):
        assert _wait(lambda: _statuses(db_sessionmaker)[jid] == "failed")
    assert len(_statuses(db_sessionmaker)) == 1


# ---------------------------------------------------------------- 心跳与重新入队


def test_heartbeat_updates_while_handler_runs(handlers, db_sessionmaker):
    started = threading.Event()
    release = threading.Event()

    def handle(db, payload):
        started.set()
        release.wait(10)

    handlers["parse"] = handle
    jid = _enqueue(db_sessionmaker)
    with _Running(db_sessionmaker, concurrency=1, heartbeat_seconds=0.05):
        assert started.wait(5)
        with db_sessionmaker() as db:
            first = db.get(Job, jid).heartbeat_at
        assert _wait(lambda: _hb(db_sessionmaker, jid) > first)
        release.set()
        assert _wait(lambda: _statuses(db_sessionmaker)[jid] == "done")


def _hb(sm, jid):
    with sm() as db:
        return db.get(Job, jid).heartbeat_at


def test_stale_job_is_requeued_and_rerun_by_worker(handlers, db_sessionmaker):
    """worker 异常退出留下的 running 任务：定时工作把它重新入队，之后被正常处理。"""
    ran = threading.Event()
    handlers["parse"] = lambda db, payload: ran.set()
    jid = _enqueue(db_sessionmaker)
    with db_sessionmaker() as db:
        job = jobs.claim(db, "dead-worker")
        job.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.commit()
    worker._PERIODIC.clear()
    worker.register_periodic("requeue_stale", worker._requeue_stale, 0)
    with _Running(db_sessionmaker):
        assert ran.wait(10)
        assert _wait(lambda: _statuses(db_sessionmaker)[jid] == "done")
    with db_sessionmaker() as db:
        assert db.get(Job, jid).attempts == 2


# ---------------------------------------------------------------- 定时工作


def test_purge_expired_sessions(handlers, db_sessionmaker, make_user):
    user = make_user("alice")
    now = datetime.now(timezone.utc)
    with db_sessionmaker() as db:
        for h, delta in (("a" * 64, -timedelta(minutes=1)), ("b" * 64, timedelta(hours=1))):
            db.add(UserSession(token_hash=h, user_id=user.id, csrf_token="c" * 64, expires_at=now + delta))
        db.commit()
    worker.register_periodic("purge_expired_sessions", worker.purge_expired_sessions, 60)
    assert worker.run_periodic(db_sessionmaker, force=True) == ["purge_expired_sessions"]
    with db_sessionmaker() as db:
        assert [s.token_hash for s in db.scalars(select(UserSession))] == ["b" * 64]


def test_default_periodic_jobs_registered():
    assert {"requeue_stale", "purge_expired_sessions"} <= set(worker._PERIODIC)


def test_periodic_interval_and_failure_isolation(handlers, db_sessionmaker, caplog):
    calls = {"ok": 0}

    def bad(db):
        raise RuntimeError("内部信息")

    def ok(db):
        calls["ok"] += 1

    worker.register_periodic("bad", bad, 3600)
    worker.register_periodic("ok", ok, 3600)
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        assert worker.run_periodic(db_sessionmaker) == ["bad", "ok"]  # 首次立即执行；失败不影响其他
        assert worker.run_periodic(db_sessionmaker) == []  # 未到间隔
        assert worker.run_periodic(db_sessionmaker, force=True) == ["bad", "ok"]
    assert calls["ok"] == 2
    assert "内部信息" not in caplog.text and "RuntimeError" in caplog.text


# ---------------------------------------------------------------- 停止与 OCR 锁


def test_worker_stops_promptly_on_stop_event(handlers, db_sessionmaker):
    stop = threading.Event()
    t = threading.Thread(
        target=worker.run_worker,
        args=(db_sessionmaker, stop),
        kwargs={"concurrency": 2, "idle_seconds": 30, "periodic_tick_seconds": 30},
    )
    t.start()
    time.sleep(0.2)
    begin = time.monotonic()
    stop.set()
    t.join(5)
    assert not t.is_alive() and time.monotonic() - begin < 3


def test_stop_waits_for_running_job_to_finish(handlers, db_sessionmaker):
    started = threading.Event()

    def handle(db, payload):
        started.set()
        time.sleep(0.3)

    handlers["parse"] = handle
    jid = _enqueue(db_sessionmaker)
    with _Running(db_sessionmaker, concurrency=1):
        assert started.wait(5)
    assert _statuses(db_sessionmaker)[jid] == "done"


def test_ocr_slot_is_exclusive():
    active = 0
    peak = 0
    lock = threading.Lock()

    def use():
        nonlocal active, peak
        with worker.ocr_slot():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1

    threads = [threading.Thread(target=use) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1
