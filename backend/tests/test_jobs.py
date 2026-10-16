"""jobs 队列（M2-01）：取任务、并发安全、心跳超时、排队位置。需要 TEST_DATABASE_URL。"""
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.db import jobs
from app.db.models import Job, ReviewRun, Task


def _add(sm, type="parse", payload=None, **kw) -> int:
    with sm() as db:
        job = jobs.enqueue(db, type, payload or {"task_id": "t"})
        for k, v in kw.items():
            setattr(job, k, v)
        db.commit()
        return job.id


def _get(sm, job_id) -> Job:
    with sm() as db:
        return db.get(Job, job_id)


def test_enqueue_defaults_and_not_committed(db_sessionmaker):
    with db_sessionmaker() as db:
        job = jobs.enqueue(db, "parse", {"task_id": "x"})
        assert job.id is not None
        db.rollback()  # enqueue 不提交
    with db_sessionmaker() as db:
        assert db.scalars(select(Job)).all() == []
    jid = _add(db_sessionmaker)
    row = _get(db_sessionmaker, jid)
    assert (row.status, row.attempts, row.max_attempts) == ("queued", 0, 3)


def test_claim_marks_running_in_id_order(db_sessionmaker):
    a = _add(db_sessionmaker)
    b = _add(db_sessionmaker)
    with db_sessionmaker() as db:
        job = jobs.claim(db, "w1")
        assert job.id == a and job.status == "running" and job.attempts == 1
        assert job.locked_by == "w1" and job.heartbeat_at is not None
    assert _get(db_sessionmaker, b).status == "queued"


def test_claim_skips_future_run_after_and_empty(db_sessionmaker):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    _add(db_sessionmaker, run_after=future)
    with db_sessionmaker() as db:
        assert jobs.claim(db, "w1") is None


def test_two_connections_never_claim_same_job(db_sessionmaker):
    """会话 A 持有第一条的行锁（未提交）时，会话 B 的 claim 跳过它取第二条（SKIP LOCKED）。"""
    first = _add(db_sessionmaker)
    second = _add(db_sessionmaker)
    with db_sessionmaker() as a, db_sessionmaker() as b:
        locked = a.execute(select(Job).where(Job.id == first).with_for_update()).scalar_one()
        assert locked.id == first
        got = jobs.claim(b, "w2")
        assert got is not None and got.id == second
        assert jobs.claim(b, "w2") is None  # 只剩被 A 锁住的那条，不会等待也不会重复取
        a.rollback()
    assert _get(db_sessionmaker, first).status == "queued"


def test_concurrent_threads_claim_distinct_jobs(db_sessionmaker):
    ids = {_add(db_sessionmaker) for _ in range(20)}
    claimed: list[int] = []
    lock = threading.Lock()

    def run(name):
        while True:
            with db_sessionmaker() as db:
                job = jobs.claim(db, name)
            if job is None:
                return
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids)  # 每条恰好被取一次


def test_heartbeat_complete_fail(db_sessionmaker):
    a = _add(db_sessionmaker)
    b = _add(db_sessionmaker)
    with db_sessionmaker() as db:
        jobs.claim(db, "w")
        jobs.claim(db, "w")
        db.execute(text("UPDATE jobs SET heartbeat_at = now() - interval '1 hour'"))
        db.commit()
        jobs.heartbeat(db, a)
        jobs.complete(db, a)
        jobs.fail(db, b, "ValueError")
    ra, rb = _get(db_sessionmaker, a), _get(db_sessionmaker, b)
    assert ra.status == "done" and ra.finished_at is not None
    assert ra.heartbeat_at > datetime.now(timezone.utc) - timedelta(minutes=1)
    assert rb.status == "failed" and rb.last_error == "ValueError" and rb.finished_at is not None


def _age(sm, job_id, minutes):
    with sm() as db:
        db.execute(
            text("UPDATE jobs SET heartbeat_at = now() - make_interval(mins => :m) WHERE id = :i"),
            {"m": minutes, "i": job_id},
        )
        db.commit()


def test_requeue_stale_requeues_then_fails_after_max_attempts(db_sessionmaker):
    jid = _add(db_sessionmaker)
    fresh = _add(db_sessionmaker)
    for attempt in (1, 2, 3):
        with db_sessionmaker() as db:
            job = jobs.claim(db, "w")
            assert job.id == jid and job.attempts == attempt
        with db_sessionmaker() as db:
            if attempt == 1:
                jobs.claim(db, "w")  # fresh 也在运行，但心跳新鲜，不应被动
        _age(db_sessionmaker, jid, 16)
        with db_sessionmaker() as db:
            assert jobs.requeue_stale(db) == 1
        row = _get(db_sessionmaker, jid)
        if attempt < 3:
            assert row.status == "queued" and row.locked_by is None
        else:
            assert row.status == "failed" and row.last_error == "HEARTBEAT_TIMEOUT"
            assert row.finished_at is not None
    assert _get(db_sessionmaker, fresh).status == "running"


def test_requeue_stale_ignores_recent_heartbeat(db_sessionmaker):
    jid = _add(db_sessionmaker)
    with db_sessionmaker() as db:
        jobs.claim(db, "w")
    _age(db_sessionmaker, jid, 14)
    with db_sessionmaker() as db:
        assert jobs.requeue_stale(db) == 0
    assert _get(db_sessionmaker, jid).status == "running"


def test_queue_position(db_sessionmaker):
    p1 = _add(db_sessionmaker, "parse")
    r1 = _add(db_sessionmaker, "review")
    p2 = _add(db_sessionmaker, "parse")
    r2 = _add(db_sessionmaker, "review")
    with db_sessionmaker() as db:
        assert jobs.queue_position(db, p1) == 1
        assert jobs.queue_position(db, p2) == 2  # 只数同类型
        assert jobs.queue_position(db, r2) == 2
        assert jobs.queue_position(db, 99999) is None
        job = jobs.claim(db, "w")  # 取走 p1
        assert job.id == p1
        assert jobs.queue_position(db, p1) is None  # 不在排队
        assert jobs.queue_position(db, p2) == 1
        assert jobs.queue_position(db, r1) == 1


# ---------------------------------------------------------------- 最终失败时收尾业务状态（Q-53）


def _task(sm, owner, status="parsing"):
    with sm() as db:
        task = Task(owner_id=owner.id, title="样本.docx", stance="sales", source="outgoing", policy="ai", status=status)
        db.add(task)
        db.commit()
        return task.id


def _run(sm, owner, task_id, status="queued"):
    with sm() as db:
        run = ReviewRun(task_id=task_id, run_no=1, status=status, stance="sales", source="outgoing",
                        main_doc_type="contract", created_by=owner.id)
        db.add(run)
        db.commit()
        return run.id


def test_fail_finalizes_parsing_task(db_sessionmaker, make_user):
    tid = _task(db_sessionmaker, make_user("alice"))
    jid = _add(db_sessionmaker, "parse", {"task_id": str(tid)})
    with db_sessionmaker() as db:
        jobs.fail(db, jid, "NO_HANDLER")
    with db_sessionmaker() as db:
        task = db.get(Task, tid)
        assert (task.status, task.error_message) == ("parse_failed", jobs.PARSE_INTERNAL_MSG)


def test_fail_finalizes_review_run_and_task(db_sessionmaker, make_user):
    owner = make_user("alice")
    tid = _task(db_sessionmaker, owner, status="reviewing")
    rid = _run(db_sessionmaker, owner, tid, status="running")
    jid = _add(db_sessionmaker, "review", {"run_id": str(rid)})
    with db_sessionmaker() as db:
        jobs.fail(db, jid, "NO_HANDLER")
    with db_sessionmaker() as db:
        run, task = db.get(ReviewRun, rid), db.get(Task, tid)
        assert (run.status, run.error_message) == ("failed", jobs.REVIEW_INTERNAL_MSG)
        assert run.finished_at is not None
        assert task.status == "reviewed"


def test_fail_keeps_state_already_set_by_handler(db_sessionmaker, make_user):
    """处理函数已经收尾（如解析失败写了具体原因、轮次 blocked）时不覆盖。"""
    owner = make_user("alice")
    tid = _task(db_sessionmaker, owner, status="parse_failed")
    with db_sessionmaker() as db:
        db.get(Task, tid).error_message = "样本.docx：具体原因"
        db.commit()
    tid2 = _task(db_sessionmaker, owner, status="awaiting_confirm")
    rid = _run(db_sessionmaker, owner, tid2, status="blocked")
    for type, payload in (("parse", {"task_id": str(tid)}), ("review", {"run_id": str(rid)})):
        jid = _add(db_sessionmaker, type, payload)
        with db_sessionmaker() as db:
            jobs.fail(db, jid, "RuntimeError")
    with db_sessionmaker() as db:
        assert db.get(Task, tid).error_message == "样本.docx：具体原因"
        assert db.get(ReviewRun, rid).status == "blocked"
        assert db.get(Task, tid2).status == "awaiting_confirm"


def test_fail_tolerates_missing_or_bad_payload(db_sessionmaker):
    """任务已删除、payload 不合法时只置 job failed。"""
    ids = [_add(db_sessionmaker, "parse", {"task_id": "00000000-0000-0000-0000-000000000000"}),
           _add(db_sessionmaker, "parse", {"task_id": "not-a-uuid"}),
           _add(db_sessionmaker, "review", {})]
    for jid in ids:
        with db_sessionmaker() as db:
            jobs.fail(db, jid, "NO_HANDLER")
        assert _get(db_sessionmaker, jid).status == "failed"


def test_requeue_stale_finalizes_after_max_attempts(db_sessionmaker, make_user):
    tid = _task(db_sessionmaker, make_user("alice"))
    jid = _add(db_sessionmaker, "parse", {"task_id": str(tid)})
    for attempt in (1, 2, 3):
        with db_sessionmaker() as db:
            jobs.claim(db, "w")
        _age(db_sessionmaker, jid, 16)
        with db_sessionmaker() as db:
            jobs.requeue_stale(db)
        with db_sessionmaker() as db:
            expected = "parsing" if attempt < 3 else "parse_failed"
            assert db.get(Task, tid).status == expected
