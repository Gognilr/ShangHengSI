"""T-47：会话接口、隔离、分批解析与删除；使用虚构文件，不调用真实模型。"""
import io
import uuid

import pytest
from docx import Document
from sqlalchemy import func, select

from app.db.base import utcnow
from app.db.models import AuditLog, DocNode, Job, LocalFinding, Task, TaskFile
from app.main import API_PREFIX
from app.tasks import pipeline, storage
from tests.test_tasks_api import files_dir  # noqa: F401

BASE = API_PREFIX + "/assistant/sessions"


@pytest.fixture(autouse=True)
def mock_ner(monkeypatch):
    from app.redaction import ner
    monkeypatch.setattr(ner, "recognize", lambda text: [])


def document(text="普通说明文字"):
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def session_client(make_user, login):
    user = make_user("assistant_owner")
    client, csrf = login(user.username)
    return client, {"X-CSRF-Token": csrf}, user


def create(client, headers):
    response = client.post(BASE, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, headers, tid, names=("虚构资料.docx",)):
    return client.post(f"{BASE}/{tid}/files", headers=headers,
                       files=[("files", (name, document(), "application/octet-stream")) for name in names])


def parse(sm, tid):
    with sm() as db:
        job = db.scalar(select(Job).where(Job.payload["task_id"].astext == tid).order_by(Job.id.desc()))
        pipeline.handle_parse(db, job.payload)


def test_t47_4_create_defaults_and_audit(files_dir, session_client, db_sessionmaker):
    c, h, owner = session_client
    data = create(c, h)
    assert (data["title"], data["status"], data["stance"], data["source"], data["policy"]) == (
        "新会话", "active", "other", "incoming", "ai")
    assert data["files"] == [] and data["code_count"] == 0
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(data["id"]))
        assert task.kind == "assistant" and task.owner_id == owner.id
        logs = db.scalars(select(AuditLog).where(AuditLog.action == "assistant_session_create")).all()
        assert len(logs) == 1 and logs[0].target_id == data["id"]


@pytest.mark.parametrize("role", ["user", "admin"])
def test_t47_1_other_owner_hidden(files_dir, session_client, make_user, login, role):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    make_user("outsider", role=role)
    other, csrf = login("outsider")
    headers = {"X-CSRF-Token": csrf}
    url = f"{BASE}/{tid}"
    assert other.get(BASE).json()["items"] == []
    for response in [other.get(url), other.patch(url, json={"title": "改名"}, headers=headers),
                     other.delete(url, headers=headers), upload(other, headers, tid),
                     other.get(url + f"/files/{uuid.uuid4()}/nodes"),
                     other.get(url + f"/files/{uuid.uuid4()}/raw"),
                     other.get(url + "/messages"), other.get(url + "/messages/1/export?format=docx")]:
        assert response.status_code == 404, response.text


def test_t47_2_3_review_isolation(files_dir, session_client, db_sessionmaker):
    c, h, owner = session_client
    tid = create(c, h)["id"]
    assert c.get(API_PREFIX + "/tasks").json()["items"] == []
    url = API_PREFIX + f"/tasks/{tid}"
    for response in [c.get(url), c.patch(url, headers=h, json={"stance": "sales"}), c.delete(url, headers=h),
                     c.get(url + "/runs"), c.post(url + "/runs", headers=h), c.get(url + "/chat"),
                     c.get(url + "/report")]:
        assert response.status_code == 404, response.text
    with db_sessionmaker() as db:
        task = Task(owner_id=owner.id, title="审核", stance="other", source="incoming", policy="ai", status="parsing")
        db.add(task)
        db.commit()
        rid = task.id
    for response in [c.get(f"{BASE}/{rid}"), c.patch(f"{BASE}/{rid}", json={"title": "改名"}, headers=h),
                     c.delete(f"{BASE}/{rid}", headers=h), upload(c, h, str(rid))]:
        assert response.status_code == 404


def test_t47_5_add_two_parse_and_preview(files_dir, session_client, db_sessionmaker):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    r = upload(c, h, tid, ("第一份.docx", "第二份.docx"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "parsing" and data["title"] == "第一份.docx"
    with db_sessionmaker() as db:
        payload = db.scalar(select(Job.payload).where(Job.type == "parse"))
        assert set(payload["file_ids"]) == {f["id"] for f in data["files"]}
    parse(db_sessionmaker, tid)
    data = c.get(f"{BASE}/{tid}").json()
    assert data["status"] == "awaiting_confirm" and not data["can_send"]
    for f in data["files"]:
        assert f["confirmed_at"] is None
        nodes = c.get(f"{BASE}/{tid}/files/{f['id']}/nodes").json()
        assert nodes["text_available"] and nodes["items"][0]["text"] == "普通说明文字"
    assert c.get(API_PREFIX + f"/tasks/{tid}/redaction").status_code == 200
    assert c.get(API_PREFIX + f"/tasks/{tid}/parties").status_code == 200


def test_t47_6_only_new_nodes_no_checks(files_dir, session_client, db_sessionmaker):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    assert upload(c, h, tid).status_code == 200
    parse(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.status = "active"
        old = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
        old.confirmed_at = utcnow()
        old_ids = list(db.scalars(select(DocNode.id).where(DocNode.file_id == old.id)))
        fid = old.id
        db.commit()
    assert upload(c, h, tid, ("新增.docx",)).status_code == 200
    parse(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        assert list(db.scalars(select(DocNode.id).where(DocNode.file_id == fid))) == old_ids
        assert db.scalar(select(func.count()).select_from(LocalFinding).where(LocalFinding.task_id == uuid.UUID(tid))) == 0


def test_t47_7_count_and_busy(files_dir, session_client, db_sessionmaker):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    assert upload(c, h, tid, tuple(f"资料{i}.docx" for i in range(5))).status_code == 200
    assert upload(c, h, tid).json()["code"] == "SESSION_BUSY"
    with db_sessionmaker() as db:
        db.get(Task, uuid.UUID(tid)).status = "active"
        db.commit()
    assert upload(c, h, tid, tuple(f"资料{i}.docx" for i in range(5))).status_code == 200
    with db_sessionmaker() as db:
        db.get(Task, uuid.UUID(tid)).status = "active"
        db.commit()
    r = upload(c, h, tid)
    assert r.status_code == 422 and r.json()["code"] == "TOO_MANY_FILES"


def test_t47_8_remove_confirmed_or_unconfirmed(files_dir, session_client, db_sessionmaker):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    data = upload(c, h, tid, ("保留.docx", "移除.docx")).json()
    parse(db_sessionmaker, tid)
    keep, remove = data["files"]
    with db_sessionmaker() as db:
        db.get(TaskFile, uuid.UUID(keep["id"])).confirmed_at = utcnow()
        key = db.get(TaskFile, uuid.UUID(remove["id"])).storage_key
        db.commit()
    r = c.delete(f"{BASE}/{tid}/files/{keep['id']}", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "FILE_CONFIRMED"
    r = c.delete(f"{BASE}/{tid}/files/{remove['id']}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "active"
    with pytest.raises(FileNotFoundError):
        storage.load_original(key)


def test_t47_9_failed_batch_preserves_old_file_and_message(files_dir, session_client, db_sessionmaker):
    from app.db.models import AssistantMessage
    c, h, _ = session_client
    tid = create(c, h)["id"]
    upload(c, h, tid)
    parse(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.status = "active"
        old = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
        old.confirmed_at = utcnow()
        db.add(AssistantMessage(task_id=task.id, role="user", status="done", content_redacted="已有消息"))
        old_id = old.id
        db.commit()
    r = c.post(f"{BASE}/{tid}/files", files=[("files", ("虚构损坏.pdf", b"%PDF-broken", "application/pdf"))], headers=h)
    assert r.status_code == 200
    fid = r.json()["files"][-1]["id"]
    parse(db_sessionmaker, tid)
    data = c.get(f"{BASE}/{tid}").json()
    assert data["status"] == "parse_failed" and "虚构损坏.pdf" in data["error_message"]
    assert c.delete(f"{BASE}/{tid}/files/{fid}", headers=h).json()["status"] == "active"
    with db_sessionmaker() as db:
        assert db.get(TaskFile, old_id) is not None
        assert db.scalar(select(AssistantMessage.content_redacted)) == "已有消息"


def test_t47_10_delete_cascade(files_dir, session_client, db_sessionmaker):
    from app.db.models import AssistantMessage, RedactionMap
    c, h, _ = session_client
    tid = create(c, h)["id"]
    upload(c, h, tid)
    parse(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        db.add(AssistantMessage(task_id=uuid.UUID(tid), role="user", status="done", content_redacted="已有消息"))
        key = db.scalar(select(TaskFile.storage_key))
        db.commit()
    assert c.delete(f"{BASE}/{tid}", headers=h).status_code == 204
    with db_sessionmaker() as db:
        for model in (TaskFile, AssistantMessage, RedactionMap, DocNode):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")) == 0
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "assistant_session_delete")) == 1
    with pytest.raises(FileNotFoundError):
        storage.load_original(key)


def test_t47_12_logs_and_patch(files_dir, session_client, caplog):
    c, h, _ = session_client
    with caplog.at_level("DEBUG"):
        tid = create(c, h)["id"]
        r = c.patch(f"{BASE}/{tid}", json={"title": "虚构秘密会话标题", "stance": "sales"}, headers=h)
        assert r.status_code == 200 and r.json()["stance"] == "sales"
        assert c.patch(f"{BASE}/{tid}", json={"title": "字" * 61}, headers=h).status_code == 422
        upload(c, h, tid, ("虚构秘密文件名.docx",))
        c.get(f"{BASE}/{tid}")
        c.get(BASE)
        c.delete(f"{BASE}/{tid}", headers=h)
    assert "虚构秘密会话标题" not in caplog.text
    assert "虚构秘密文件名" not in caplog.text


def test_session_csrf_invalid_kind_query_and_patch_lock(files_dir, session_client, db_sessionmaker, api):
    c, h, _ = session_client
    assert api.get(BASE).status_code == 401
    assert c.post(BASE).status_code == 403
    tid = create(c, h)["id"]
    assert c.get(API_PREFIX + f"/tasks/{tid}?kind=assistant").status_code == 404
    assert c.patch(f"{BASE}/{tid}", headers=h, json={"title": "   "}).status_code == 422
    upload(c, h, tid)
    assert c.patch(f"{BASE}/{tid}", headers=h, json={"stance": "purchase"}).status_code == 409
    parse(db_sessionmaker, tid)
    fid = c.get(f"{BASE}/{tid}").json()["files"][0]["id"]
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.status = "active"
        db.get(TaskFile, uuid.UUID(fid)).confirmed_at = utcnow()
        db.commit()
    assert c.patch(f"{BASE}/{tid}", headers=h, json={"source": "outgoing"}).status_code == 409
    assert c.patch(f"{BASE}/{tid}", headers=h, json={"title": "新的标题"}).status_code == 200


def test_list_search_pagination_activity_and_readonly(files_dir, session_client, db_sessionmaker):
    from app.db.models import AssistantMessage
    c, h, owner = session_client
    with db_sessionmaker() as db:
        for i in range(31):
            db.add(Task(kind="assistant", owner_id=owner.id, title=f"检索虚构{i}", stance="other", source="incoming",
                        policy="ai", status="active"))
        db.commit()
    first = c.get(BASE).json()
    second = c.get(BASE + "?page=2").json()
    assert first["total"] == 31 and first["page_size"] == 30 and len(first["items"]) == 30
    assert len(second["items"]) == 1
    assert c.get(BASE + "?q=虚构30").json()["total"] == 1
    tid = create(c, h)["id"]
    upload(c, h, tid)
    parse(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.status = "active"
        f = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
        f.original_deleted_at = utcnow()
        task.last_activity_at = utcnow()
        last = task.last_activity_at
        db.add(AssistantMessage(task_id=task.id, role="assistant", status="streaming"))
        db.commit()
    detail = c.get(f"{BASE}/{tid}").json()
    assert detail["original_deleted"] and not detail["can_send"]
    assert detail["send_reason"] == "原件已按保留期限删除，本会话只能查看"
    assert upload(c, h, tid).json()["code"] == "SESSION_READONLY"
    with db_sessionmaker() as db:
        assert db.get(Task, uuid.UUID(tid)).last_activity_at == last


def test_file_owner_preview_and_upload_validation(files_dir, session_client, make_user, login):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    assert c.post(f"{BASE}/{tid}/files", headers=h).json()["code"] == "NO_FILES"
    r = c.post(f"{BASE}/{tid}/files", headers=h, files=[("files", ("错误.pdf", b"plain", "application/pdf"))])
    assert r.status_code == 422 and r.json()["code"] == "FILE_CONTENT_MISMATCH"
    fid = upload(c, h, tid).json()["files"][0]["id"]
    make_user("other_session")
    other, csrf = login("other_session")
    for suffix in ("nodes", "raw"):
        assert other.get(f"{BASE}/{tid}/files/{fid}/{suffix}").status_code == 404
    assert other.delete(f"{BASE}/{tid}/files/{fid}", headers={"X-CSRF-Token": csrf}).status_code == 404


def test_upload_failure_removes_partial_originals(files_dir, session_client, monkeypatch, db_sessionmaker):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    original = storage.save_original
    count = 0

    def fail_second(data):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("测试存储失败")
        return original(data)

    monkeypatch.setattr(storage, "save_original", fail_second)
    with pytest.raises(OSError):
        upload(c, h, tid, ("第一份.docx", "第二份.docx"))
    assert not list(files_dir.iterdir())
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(TaskFile)) == 0
        assert db.get(Task, uuid.UUID(tid)).status == "active"


def test_assistant_ocr_budget_counts_old_empty_scanned_pages(files_dir, session_client, monkeypatch, db_sessionmaker):
    from types import SimpleNamespace
    from app.parsing.model import ParsedFile, ParsedNode
    c, h, _ = session_client
    tid = create(c, h)["id"]
    with db_sessionmaker() as db:
        key, digest = storage.save_original(b"%PDF-old")
        db.add(TaskFile(task_id=uuid.UUID(tid), seq=1, is_main=True, original_name="旧扫描.pdf", ext="pdf",
                        size_bytes=8, sha256=digest, storage_key=key, page_count=100, confirmed_at=utcnow()))
        db.commit()
    # 旧 PDF 含 100 页，其中空白页不一定有 OCR 节点，仍必须全部计数。
    monkeypatch.setattr(pipeline.pdf, "inspect_pdf", lambda data: SimpleNamespace(scanned_pages=list(range(1, 101))))
    seen = []

    def parse_bytes(ext, data, seq, used):
        seen.append(used)
        pipeline._check_ocr_budget(used, 1)
        return ParsedFile([ParsedNode(f"F{seq}-P1", 1, "paragraph", "body", "文字")], 1, False, [], 1), "test"

    monkeypatch.setattr(pipeline, "_parse_bytes", parse_bytes)
    assert upload(c, h, tid).status_code == 200
    parse(db_sessionmaker, tid)
    assert seen == [100]
    detail = c.get(f"{BASE}/{tid}").json()
    assert detail["status"] == "parse_failed" and "超过 100 页" in detail["error_message"]


def test_t48_5_6_database_pins_and_remove_preserve_history(files_dir, session_client, db_sessionmaker):
    from app.redaction.engine import load_redaction_map, load_real_values
    c, h, _ = session_client
    tid = create(c, h)["id"]

    def add(name, text):
        r = c.post(f"{BASE}/{tid}/files", headers=h,
                   files=[("files", (name, document(text), "application/octet-stream"))])
        assert r.status_code == 200, r.text
        parse(db_sessionmaker, tid)
        return r.json()["files"][-1]["id"]

    fid = add("第一份.docx", "买方：虚构星河贸易有限公司")
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        first_map = load_redaction_map(db, task.id)
        assert first_map == {"[[ORG_1]]": "虚构星河贸易有限公司"}
        old = db.scalar(select(DocNode).where(DocNode.file_id == uuid.UUID(fid)))
        node_id, redacted = old.id, old.redacted_text
        db.get(TaskFile, uuid.UUID(fid)).confirmed_at = utcnow()
        task.status = "active"
        db.commit()
    added = add("第二份.docx", "买方：虚构山川科技有限公司；虚构星河贸易有限公司")
    with db_sessionmaker() as db:
        mapping = load_redaction_map(db, uuid.UUID(tid))
        assert mapping["[[ORG_1]]"] == first_map["[[ORG_1]]"]
        assert mapping["[[ORG_2]]"] == "虚构山川科技有限公司"
        assert db.get(DocNode, node_id).redacted_text == redacted
    r = c.delete(f"{BASE}/{tid}/files/{added}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "active"
    with db_sessionmaker() as db:
        mapping = load_redaction_map(db, uuid.UUID(tid))
        assert mapping["[[ORG_2]]"] == "虚构山川科技有限公司"
        assert "虚构山川科技有限公司" in load_real_values(db, uuid.UUID(tid))


def test_assistant_parties_allow_zero_and_review_still_requires_one(files_dir, session_client, db_sessionmaker):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    upload(c, h, tid)
    parse(db_sessionmaker, tid)
    response = c.put(API_PREFIX + f"/tasks/{tid}/parties", headers=h, json={"items": []})
    assert response.status_code == 200 and response.json()["items"] == []
