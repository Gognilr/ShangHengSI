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
        kw.setdefault("parse_concurrency", 2)
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
    with _Running(db_sessionmaker, parse_concurrency=3):
        assert _wait(lambda: set(_statuses(db_sessionmaker).values()) == {"done"})
    assert sorted(seen) == list(range(12))
    assert set(_statuses(db_sessionmaker)) == set(ids)


def test_law_import_runs_in_parse_group(handlers, db_sessionmaker):
    seen = []
    handlers['law_import'] = lambda db, payload: seen.append(threading.current_thread().name)
    jid = _enqueue(db_sessionmaker, 'law_import', {'import_id': 'example'})
    with _Running(db_sessionmaker, parse_concurrency=1):
        assert _wait(lambda: _statuses(db_sessionmaker)[jid] == 'done')
    assert seen == ['worker-parse-0']


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
    with _Running(db_sessionmaker, parse_concurrency=1):
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
    with _Running(db_sessionmaker, parse_concurrency=1, heartbeat_seconds=0.05):
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
        job = jobs.claim(db, "dead-worker", ("parse", "review", "backup", "retention"))
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
        kwargs={"parse_concurrency": 2, "idle_seconds": 30, "periodic_tick_seconds": 30},
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
    with _Running(db_sessionmaker, parse_concurrency=1):
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


def test_module_entry_delegates_to_app_worker(monkeypatch):
    """python -m app.worker：入口必须调用 app.worker.main，处理函数才注册在同一个 HANDLERS 中。"""
    import runpy
    import warnings

    called = []
    monkeypatch.setattr(worker, "main", lambda: called.append(True))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # app.worker 已导入时 runpy 的提示
        runpy.run_module("app.worker", run_name="__main__")
    assert called == [True]


def test_parse_and_review_handlers_registered_after_import():
    import app.review.job  # noqa: F401
    import app.tasks.pipeline  # noqa: F401

    assert {"parse", "review"} <= set(worker.HANDLERS)


def test_unregistered_handler_does_not_leave_task_parsing(handlers, db_sessionmaker, make_user):
    """M2 验收发现的问题（Q-53）：未注册处理函数时，任务不能一直停在“解析中”。"""
    from app.db.models import Task

    owner = make_user("alice")
    with db_sessionmaker() as db:
        task = Task(owner_id=owner.id, title="样本.docx", stance="sales", source="outgoing", policy="ai",
                    status="parsing")
        db.add(task)
        db.commit()
        tid = task.id
    jid = _enqueue(db_sessionmaker, "parse", {"task_id": str(tid)})
    with _Running(db_sessionmaker):
        assert _wait(lambda: _statuses(db_sessionmaker)[jid] == "failed")
    with db_sessionmaker() as db:
        assert db.get(Task, tid).status == "parse_failed"


def test_review_does_not_wait_for_busy_parse_group(handlers, db_sessionmaker):
    started = threading.Barrier(3)
    release = threading.Event()
    reviewed = threading.Event()

    def parse(db, payload):
        if not release.is_set():
            started.wait(5)
        release.wait(10)

    handlers.update(parse=parse, review=lambda db, payload: reviewed.set())
    parses = [_enqueue(db_sessionmaker, "parse") for _ in range(3)]
    try:
        with _Running(db_sessionmaker, review_concurrency=1, parse_concurrency=2):
            started.wait(5)
            review = _enqueue(db_sessionmaker, "review")
            assert reviewed.wait(3)
            assert _wait(lambda: _statuses(db_sessionmaker)[review] == "done")
            assert sorted(_statuses(db_sessionmaker)[jid] for jid in parses) == ["queued", "running", "running"]
            release.set()
    finally:
        release.set()


def test_default_worker_thread_groups(monkeypatch):
    threads = []
    stop = threading.Event()
    stop.set()

    class Thread:
        def __init__(self, **kw):
            threads.append(kw)

        def start(self):
            pass

        def join(self, timeout):
            pass

    monkeypatch.setattr(worker.threading, "Thread", Thread)
    worker.run_worker(None, stop)
    assert [t["args"][-1] for t in threads].count(("review",)) == 8
    assert [t["args"][-1] for t in threads].count(("parse", "law_import")) == 2
    assert [t["args"][-1] for t in threads].count(("backup", "retention", "law_embed", "law_sync", "template_embed")) == 1
    assert len({t["args"][1] for t in threads}) == 11


def test_eight_reviews_with_gateway_sessions_and_heartbeats(handlers, db_sessionmaker, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from app.db import session

    monkeypatch.setattr(session, "get_settings", lambda: SimpleNamespace(
        database_url=db_sessionmaker.kw["bind"].url.render_as_string(hide_password=False)))
    session.get_engine.cache_clear()
    engine = session.get_engine(pool_size=12, max_overflow=12)
    sm = sessionmaker(bind=engine, expire_on_commit=False)
    simultaneous = threading.Barrier(8)
    seen = []

    def review(db, payload):
        db.execute(text("SELECT 1"))
        simultaneous.wait(10)
        # 模拟网关在独立会话写外发记录，延迟期间任务会话与心跳仍然存在。
        with sm() as gateway_db:
            gateway_db.execute(text("SELECT 1"))
            time.sleep(2)
        seen.append(payload["n"])

    handlers["review"] = review
    ids = [_enqueue(sm, "review", {"n": i}) for i in range(8)]
    try:
        with _Running(sm, review_concurrency=8, parse_concurrency=2, heartbeat_seconds=0.05):
            assert _wait(lambda: all(_statuses(sm)[jid] == "done" for jid in ids), timeout=15)
        assert sorted(seen) == list(range(8))
        assert engine.pool.size() == 12
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()
        session.get_engine.cache_clear()


def test_stop_deadline_applies_to_all_groups(handlers, db_sessionmaker, monkeypatch):
    release = threading.Event()
    started = threading.Barrier(4)

    def handle(db, payload):
        started.wait(5)
        release.wait(10)

    handlers.update({kind: handle for kind in ("review", "parse", "backup")})
    ids = [_enqueue(db_sessionmaker, kind) for kind in handlers]
    stop = threading.Event()
    monkeypatch.setattr(worker, "_stop", stop)
    thread = threading.Thread(target=worker.run_worker, args=(db_sessionmaker, stop), kwargs={
        "review_concurrency": 1, "parse_concurrency": 1, "idle_seconds": 0.02,
        "periodic_tick_seconds": 0.02, "join_timeout": 0.1,
    })
    thread.start()
    try:
        started.wait(5)
        begin = time.monotonic()
        worker._handle_signal(15, None)
        thread.join(2)
        assert not thread.is_alive() and time.monotonic() - begin < 1
        assert all(_statuses(db_sessionmaker)[jid] == "running" for jid in ids)
    finally:
        stop.set()
        release.set()
        thread.join(5)
        assert _wait(lambda: all(_statuses(db_sessionmaker)[jid] == "done" for jid in ids))


def test_startup_ignores_legacy_variable_and_warns_once(monkeypatch, caplog):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from app.embedding import engine as embedding_engine
    from app.laws import job as law_job
    from app.config import load_settings
    from app.db import session
    from app.redaction import ner

    monkeypatch.setenv("WORKER_CONCURRENCY", "invalid-ignored-secret")
    monkeypatch.setenv("WORKER_REVIEW_CONCURRENCY", "8")
    monkeypatch.setenv("WORKER_PARSE_CONCURRENCY", "2")
    monkeypatch.setattr(worker, "get_settings", load_settings)
    monkeypatch.setattr(worker, "check_master_key", lambda: None)
    monkeypatch.setattr(ner, "check", lambda **kw: None)
    startup = []
    monkeypatch.setattr(embedding_engine, 'check', lambda **kw: startup.append(kw))
    monkeypatch.setattr(law_job, 'enqueue_embed', lambda db: startup.append('enqueue'))
    monkeypatch.setattr(worker.signal, "signal", lambda *args: None)
    pool_options = []
    monkeypatch.setattr(session, "get_engine", lambda **kw: pool_options.append(kw) or "engine")
    monkeypatch.setattr(worker, "sessionmaker", lambda **kw:
                        lambda: nullcontext(SimpleNamespace(commit=lambda: startup.append('commit'))))
    calls = []
    monkeypatch.setattr(worker, "run_worker", lambda *args, **kw: calls.append((args, kw)))
    with caplog.at_level(logging.WARNING, logger="app.worker"):
        worker.main()
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["已改为 WORKER_REVIEW_CONCURRENCY / WORKER_PARSE_CONCURRENCY"]
    assert "invalid-ignored-secret" not in caplog.text
    assert pool_options == [{"pool_size": 12, "max_overflow": 12}]
    assert calls[0][1] == {"review_concurrency": 8, "parse_concurrency": 2}
    assert startup == [{'load': True}, 'enqueue', 'commit']


def test_law_embed_runs_in_maintenance_group(handlers, db_sessionmaker):
    seen = []
    handlers['law_embed'] = lambda db, payload: seen.append(threading.current_thread().name)
    jid = _enqueue(db_sessionmaker, 'law_embed', {})
    with _Running(db_sessionmaker):
        assert _wait(lambda: _statuses(db_sessionmaker)[jid] == 'done')
    assert len(seen) == 1 and seen[0].startswith('worker-maintenance-')
