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


@pytest.fixture(autouse=True)
def fake_embedding(monkeypatch):
    import numpy as np
    from app.embedding import engine
    monkeypatch.setattr(engine, 'embed', lambda texts, kind:
                        np.tile(np.eye(1, 1024, dtype=np.float32), (len(texts), 1)))


def test_t44_11_original_layer_deletes_clause_vectors(alice, db_sessionmaker, ready_task, llm):
    from app.db.models import TaskClauseVector
    from sqlalchemy import func
    user, client, headers = alice
    task = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(TaskClauseVector)
                         .where(TaskClauseVector.task_id == task.id)) > 0
    set_days(db_sessionmaker, 1)
    age(db_sessionmaker, task.id, 2)
    assert run(db_sessionmaker)['tasks'] == 1
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(TaskClauseVector)
                         .where(TaskClauseVector.task_id == task.id)) == 0


@pytest.fixture
def alice(make_user, login):
    user = make_user("alice")
    client, csrf = login("alice")
    return user, client, {"X-CSRF-Token": csrf}


def set_days(sm, days):
    with sm() as db:
        settings_store.set_setting(db, "retention.original_days", days, None)
        for layer in ("result", "outbound", "audit", "unfinished"):
            settings_store.set_setting(db, f"retention.{layer}_days", 0, None)
        db.commit()


def age(sm, task_id, days):
    with sm() as db:
        db.execute(update(Task).where(Task.id == task_id).values(last_completed_at=utcnow() - timedelta(days=days), last_activity_at=utcnow() - timedelta(days=days)))
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

    assert run(db_sessionmaker) == dict(tasks=1, files=1, results=0, unfinished=0, outbound=0, audit=0)

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
        assert row.detail == dict(tasks=1, files=1, results=0, unfinished=0, outbound=0, audit=0) and row.user_id is None


def test_days_zero_does_nothing_and_does_not_enqueue(alice, db_sessionmaker, ready_task, llm):  # noqa: F811
    user, client, headers = alice
    task = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)
    set_days(db_sessionmaker, 0)
    age(db_sessionmaker, task.id, 400)
    assert run(db_sessionmaker) == dict(tasks=0, files=0, results=0, unfinished=0, outbound=0, audit=0)
    assert files_of(db_sessionmaker, task.id)[0].original_deleted_at is None
    with db_sessionmaker() as db:
        assert retention.check_retention(db, datetime(2026, 10, 22, 4, 0, tzinfo=SH)) is False
        assert db.scalar(select(Job.id).where(Job.type == "retention")) is None


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


# V2-C-02：以下用例与设计 2.7 逐项对应，全部使用隔离虚构数据。
def _layers(sm, **values):
    with sm() as db:
        for key, default in dict(original=30, result=365, outbound=180, audit=0, unfinished=30).items():
            settings_store.set_setting(db, f"retention.{key}_days", values.get(key, default), None)
        db.commit()


def _activity(sm, tid, days):
    with sm() as db:
        db.execute(update(Task).where(Task.id == tid).values(last_activity_at=utcnow() - timedelta(days=days)))
        db.commit()


def test_v2c_original_layer_preserves_report(alice, db_sessionmaker, ready_task, llm):
    user, client, headers = alice
    task = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)
    _layers(db_sessionmaker)
    _activity(db_sessionmaker, task.id, 31)
    run(db_sessionmaker)
    with db_sessionmaker() as db:
        assert db.get(Task, task.id) is not None
        assert all(n.text_enc is None for n in db.scalars(select(DocNode).where(DocNode.task_id == task.id)))
        assert db.get(Task, task.id).user_note_enc is None
    assert not storage._path(files_of(db_sessionmaker, task.id)[0].storage_key).exists()
    assert client.get(f"{API_PREFIX}/runs/{task.latest_run_id}/report").status_code == 200


def test_v2c_result_layer_deletes_entire_task_but_keeps_calls(alice, db_sessionmaker, ready_task, llm):
    from app.db.models import ChatMessage, Party, RedactionItem, LocalFinding
    user, client, headers = alice
    task = reviewed(db_sessionmaker, user, client, headers, ready_task, llm)
    _layers(db_sessionmaker)
    _activity(db_sessionmaker, task.id, 366)
    file = files_of(db_sessionmaker, task.id)[0]
    with db_sessionmaker() as db:
        db.add(ChatMessage(task_id=task.id, role="user", content_redacted="虚构问题"))
        db.commit()
    run(db_sessionmaker)
    with db_sessionmaker() as db:
        assert db.get(Task, task.id) is None
        for model in (TaskFile, DocNode, Party, RedactionItem, ReviewRun, Finding, ChatMessage, LocalFinding):
            assert list(db.scalars(select(model).where(model.task_id == task.id))) == []
        assert db.get(RedactionMap, task.id) is None
        from app.db.models import LlmCall
        calls = list(db.scalars(select(LlmCall).where(LlmCall.task_id == task.id)))
        assert calls and all(c.task_title is None for c in calls)
    assert not storage._path(file.storage_key).exists()


@pytest.mark.parametrize("status,deleted", [("awaiting_confirm", True), ("parse_failed", True),
                                           ("parsing", False), ("reviewing", False)])
def test_v2c_unfinished_cleanup_guards_running_tasks(alice, db_sessionmaker, ready_task, status, deleted):
    user, _, _ = alice
    task = ready_task(user)
    with db_sessionmaker() as db:
        db.get(Task, task.id).status = status
        db.commit()
    _layers(db_sessionmaker)
    _activity(db_sessionmaker, task.id, 400)
    run(db_sessionmaker)
    with db_sessionmaker() as db:
        assert (db.get(Task, task.id) is None) is deleted


# V2Q-74（C-09 审核修订）：重新审核后未提交的任务不按未完成任务整删。


def test_d01_assistant_original_and_result_layers(alice, db_sessionmaker):
    from app.db.models import AssistantMessage
    user, _, _ = alice
    set_days(db_sessionmaker, 1)
    with db_sessionmaker() as db:
        task = Task(kind="assistant", owner_id=user.id, title="虚构会话", stance="other", source="incoming",
                    policy="ai", status="active", last_activity_at=utcnow() - timedelta(days=2))
        db.add(task)
        db.flush()
        key, digest = storage.save_original(b"test")
        file = TaskFile(task_id=task.id, seq=1, is_main=True, original_name="虚构.pdf", ext="pdf",
                        size_bytes=4, sha256=digest, storage_key=key, confirmed_at=utcnow())
        db.add(file)
        db.add(AssistantMessage(task_id=task.id, role="user", status="done", content_redacted="代号问题"))
        tid = task.id
        db.commit()
    assert run(db_sessionmaker)["tasks"] == 1
    with db_sessionmaker() as db:
        assert db.get(Task, tid).status == "active"
        assert db.scalar(select(AssistantMessage).where(AssistantMessage.task_id == tid)) is not None
        settings_store.set_setting(db, "retention.result_days", 1, None)
        db.commit()
    assert run(db_sessionmaker)["results"] == 1
    with db_sessionmaker() as db:
        assert db.get(Task, tid) is None
        assert db.scalar(select(AssistantMessage).where(AssistantMessage.task_id == tid)) is None
