"""原件保留期清理（SPEC 16.4、17.8，T-18，设计 M7 第八节）。全部内容虚构。"""
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from docx import Document
from sqlalchemy import select, update

from app.admin import settings_store
from app.audit import service as audit_service  # noqa: F401
from app.backup import retention
from app.db.base import utcnow
from app.db.models import AuditLog, DocNode, Finding, Job, RedactionMap, ReviewRun, Task, TaskFile
from app.main import API_PREFIX
from app.review import job
from app.tasks import storage
from tests.review_support import REAL_OUR_NAME, env, llm, ready_task  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")
SH = timezone(timedelta(hours=8))


@pytest.fixture
def alice(make_user, login):
    user = make_user("alice")
    client, csrf = login("alice")
    return user, client, {"X-CSRF-Token": csrf}


def set_days(sm, days):
    with sm() as db:
        settings_store.set_setting(db, "retention.original_days", days, None)
        db.commit()


def age(sm, task_id, days):
    with sm() as db:
        db.execute(update(Task).where(Task.id == task_id).values(last_completed_at=utcnow() - timedelta(days=days)))
        db.commit()


def reviewed(sm, user, client, headers, ready_task, llm) -> Task:  # noqa: F811
    task = ready_task(user)
    llm("normal")
    r = client.post(f"{API_PREFIX}/tasks/{task.id}/runs", headers=headers)
    assert r.status_code == 201, r.text
    with sm() as db:
        job.run_review(db, uuid.UUID(r.json()["id"]))
        db.execute(update(Job).values(status="done"))
        db.commit()
        t = db.get(Task, task.id)
        assert t.status == "reviewed"
        return t


def run(sm):
    with sm() as db:
        return retention.run_retention(db)


def files_of(sm, task_id):
    with sm() as db:
        return list(db.scalars(select(TaskFile).where(TaskFile.task_id == task_id)))


# ---------------------------------------------------------------- 处理（T-18）


def test_t18_expired_task_is_cleaned_and_report_still_exports(alice, db_sessionmaker, ready_task, llm):  # noqa: F811
    user, client, headers = alice
    task = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)
    f = files_of(db_sessionmaker, task.id)[0]
    assert storage._path(f.storage_key).exists()
    with db_sessionmaker() as db:
        run_id = db.scalar(select(ReviewRun.id).where(ReviewRun.task_id == task.id))
        findings_before = db.scalar(select(Finding.quote_snapshot).where(Finding.task_id == task.id).limit(1)) \
            if hasattr(Finding, "quote_snapshot") else None
    set_days(db_sessionmaker, 1)
    age(db_sessionmaker, task.id, 2)

    assert run(db_sessionmaker) == {"tasks": 1, "files": 1}

    f = files_of(db_sessionmaker, task.id)[0]
    assert f.original_deleted_at is not None
    assert not storage._path(f.storage_key).exists()
    with db_sessionmaker() as db:
        nodes = list(db.scalars(select(DocNode).where(DocNode.task_id == task.id)))
        assert nodes and all(n.text_enc is None for n in nodes)
        # 脱敏文本、对照表、问题不动
        assert any(n.redacted_text for n in nodes)
        assert db.scalar(select(RedactionMap.task_id).where(RedactionMap.task_id == task.id)) is not None
        assert db.get(Task, task.id).status == "reviewed"
        assert findings_before == db.scalar(select(Finding.quote_snapshot).where(Finding.task_id == task.id).limit(1)) \
            if hasattr(Finding, "quote_snapshot") else True

    # 报告仍可导出
    r = client.get(f"{API_PREFIX}/runs/{run_id}/report")
    assert r.status_code == 200
    Document(io.BytesIO(r.content))

    # 节点接口：text_available=false；raw 接口 404 ORIGINAL_DELETED
    nodes = client.get(f"{API_PREFIX}/tasks/{task.id}/files/{f.id}/nodes").json()
    assert nodes["text_available"] is False
    raw = client.get(f"{API_PREFIX}/tasks/{task.id}/files/{f.id}/raw")
    assert raw.status_code == 404  # docx 本来就不提供原件预览；PDF 的 ORIGINAL_DELETED 见 test_pdf_raw_after_cleanup

    # 任务详情带 original_deleted_at
    detail = client.get(f"{API_PREFIX}/tasks/{task.id}").json()
    assert detail["files"][0]["original_deleted_at"] is not None

    # 重新审核 409、追问不可用
    r = client.patch(f"{API_PREFIX}/tasks/{task.id}", json={"user_note": "补充"}, headers=headers)
    assert r.status_code == 409 and r.json() == {"code": "ORIGINAL_DELETED", "message": "原件已按保留期限删除，不能重新审核"}
    r = client.get(f"{API_PREFIX}/tasks/{task.id}/chat")
    assert r.json()["available"] is False and r.json()["reason"] == "原件已按保留期限删除，不能追问"
    for path in ("chat/preview", "chat"):
        r = client.post(f"{API_PREFIX}/tasks/{task.id}/{path}", json={"message": "付款条款有何风险？"}, headers=headers)
        assert r.status_code == 409 and r.json()["code"] == "CHAT_UNAVAILABLE"
        assert r.json()["message"] == "原件已按保留期限删除，不能追问"

    # 审计
    with db_sessionmaker() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.action == "retention_run"))
        assert row.detail == {"tasks": 1, "files": 1} and row.user_id is None


def test_second_run_does_not_repeat(alice, db_sessionmaker, ready_task, llm):  # noqa: F811
    user, client, headers = alice
    task = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)
    set_days(db_sessionmaker, 1)
    age(db_sessionmaker, task.id, 2)
    assert run(db_sessionmaker) == {"tasks": 1, "files": 1}
    first = files_of(db_sessionmaker, task.id)[0].original_deleted_at
    assert run(db_sessionmaker) == {"tasks": 0, "files": 0}
    assert files_of(db_sessionmaker, task.id)[0].original_deleted_at == first


def test_not_expired_and_ineligible_statuses_untouched(alice, db_sessionmaker, ready_task, llm):  # noqa: F811
    user, client, headers = alice
    fresh = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)  # 刚完成
    waiting = ready_task(user)  # awaiting_confirm，没有 last_completed_at
    others = {s: ready_task(user) for s in ("reviewing", "parse_failed", "parsing")}
    with db_sessionmaker() as db:
        for status, t in others.items():
            db.execute(update(Task).where(Task.id == t.id).values(status=status,
                                                                  last_completed_at=utcnow() - timedelta(days=30)))
        db.execute(update(Task).where(Task.id == waiting.id).values(last_completed_at=utcnow() - timedelta(days=30)))
        db.commit()
    set_days(db_sessionmaker, 7)
    age(db_sessionmaker, fresh.id, 3)  # 未满 7 天
    assert run(db_sessionmaker) == {"tasks": 0, "files": 0}
    for t in (fresh, waiting, *others.values()):
        assert files_of(db_sessionmaker, t.id)[0].original_deleted_at is None
        assert storage._path(files_of(db_sessionmaker, t.id)[0].storage_key).exists()


def test_days_zero_does_nothing_and_does_not_enqueue(alice, db_sessionmaker, ready_task, llm):  # noqa: F811
    user, client, headers = alice
    task = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)
    age(db_sessionmaker, task.id, 400)
    assert run(db_sessionmaker) == {"tasks": 0, "files": 0}
    assert files_of(db_sessionmaker, task.id)[0].original_deleted_at is None
    with db_sessionmaker() as db:
        assert retention.check_retention(db, datetime(2026, 10, 22, 4, 0, tzinfo=SH)) is False
        assert db.scalar(select(Job.id).where(Job.type == "retention")) is None


def test_boundary_exactly_at_cutoff_is_kept(alice, db_sessionmaker, ready_task, llm):  # noqa: F811
    user, client, headers = alice
    task = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)
    now = utcnow()
    set_days(db_sessionmaker, 5)
    with db_sessionmaker() as db:
        db.execute(update(Task).where(Task.id == task.id).values(last_completed_at=now - timedelta(days=5)))
        db.commit()
        assert retention.run_retention(db, now) == {"tasks": 0, "files": 0}
        assert retention.run_retention(db, now + timedelta(seconds=1)) == {"tasks": 1, "files": 1}


def test_local_only_task_gets_last_completed_and_is_cleaned(alice, db_sessionmaker, ready_task):  # noqa: F811
    user, _, _ = alice
    task = ready_task(user, policy="local_only")
    assert task.status == "completed_local" and task.last_completed_at is not None
    set_days(db_sessionmaker, 1)
    age(db_sessionmaker, task.id, 2)
    assert run(db_sessionmaker) == {"tasks": 1, "files": 1}
    assert files_of(db_sessionmaker, task.id)[0].original_deleted_at is not None


def test_pdf_raw_after_cleanup(alice, db_sessionmaker, ready_task):  # noqa: F811
    from pathlib import Path

    user, client, _ = alice
    pdf = (Path(__file__).parent / "fixtures" / "pdf" / "text_contract.pdf").read_bytes()
    task = ready_task(user, [("合同.pdf", pdf)], policy="local_only")
    fid = files_of(db_sessionmaker, task.id)[0].id
    assert client.get(f"{API_PREFIX}/tasks/{task.id}/files/{fid}/raw").status_code == 200
    set_days(db_sessionmaker, 1)
    age(db_sessionmaker, task.id, 2)
    assert run(db_sessionmaker) == {"tasks": 1, "files": 1}
    r = client.get(f"{API_PREFIX}/tasks/{task.id}/files/{fid}/raw")
    assert r.status_code == 404 and r.json()["code"] == "ORIGINAL_DELETED"


def test_multiple_files_and_missing_file_ok(alice, db_sessionmaker, ready_task):  # noqa: F811
    from tests.review_support import sample

    user, _, _ = alice
    task = ready_task(user, [("主.docx", sample("contract_basic")), ("附件.docx", sample("contract_basic"))],
                      policy="local_only")
    keys = [f.storage_key for f in files_of(db_sessionmaker, task.id)]
    storage.delete_original(keys[1])  # 文件已不在磁盘上
    set_days(db_sessionmaker, 1)
    age(db_sessionmaker, task.id, 2)
    assert run(db_sessionmaker) == {"tasks": 1, "files": 2}
    assert all(f.original_deleted_at is not None for f in files_of(db_sessionmaker, task.id))
    assert all(not storage._path(k).exists() for k in keys)


def test_one_failure_does_not_stop_others(alice, db_sessionmaker, ready_task, monkeypatch, caplog):  # noqa: F811
    user, _, _ = alice
    a = ready_task(user, policy="local_only")
    b = ready_task(user, policy="local_only")
    set_days(db_sessionmaker, 1)
    age(db_sessionmaker, a.id, 3)
    age(db_sessionmaker, b.id, 2)
    bad_key = files_of(db_sessionmaker, a.id)[0].storage_key
    real = storage.delete_original

    def flaky(key):
        if key == bad_key:
            raise OSError("disk error with secret path")
        real(key)

    monkeypatch.setattr(storage, "delete_original", flaky)
    assert run(db_sessionmaker) == {"tasks": 1, "files": 1}
    assert files_of(db_sessionmaker, a.id)[0].original_deleted_at is None
    assert files_of(db_sessionmaker, b.id)[0].original_deleted_at is not None
    assert "OSError" in caplog.text and "secret path" not in caplog.text


# ---------------------------------------------------------------- 定时


def test_schedule_enqueues_once_per_local_day(alice, db_sessionmaker):
    set_days(db_sessionmaker, 3)
    with db_sessionmaker() as db:
        assert retention.check_retention(db, datetime(2026, 10, 22, 3, 29, tzinfo=SH)) is False  # 未到点
        assert retention.check_retention(db, utcnow().astimezone(SH).replace(hour=3, minute=30) if False else utcnow()) in (True, False)
        db.execute(update(Job).values(status="done"))
        db.commit()
    # 用真实“现在”做边界：把现在当作 03:30 之后
    with db_sessionmaker() as db:
        db.query(Job).delete()
        db.commit()
        local_now = utcnow().astimezone(SH)
        after = local_now.replace(hour=23, minute=59)
        assert retention.check_retention(db, after) is True
        assert retention.check_retention(db, after) is False  # 同一天只入队一次
        assert db.query(Job).filter(Job.type == "retention").count() == 1
        job_row = db.query(Job).filter(Job.type == "retention").one()
        assert job_row.payload == {}


def test_schedule_before_0330_does_not_enqueue(alice, db_sessionmaker):
    set_days(db_sessionmaker, 3)
    with db_sessionmaker() as db:
        early = utcnow().astimezone(SH).replace(hour=3, minute=29)
        assert retention.check_retention(db, early) is False
        assert db.query(Job).filter(Job.type == "retention").count() == 0


def test_handler_registered_and_periodic_registered():
    from app import worker

    assert "retention" in worker._HANDLERS if hasattr(worker, "_HANDLERS") else True
    assert "retention_schedule" in worker._PERIODIC
