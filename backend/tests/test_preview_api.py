"""预览接口（M3-06）：节点接口、原件接口，以及对真实接口重跑 A-06。需要 TEST_DATABASE_URL。"""
import os
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.base import utcnow
from app.db.models import AuditLog, DocNode, Task, TaskFile
from app.main import API_PREFIX
from app.security import crypto
from app.tasks import storage

PDF = b"%PDF-1.7\n" + "虚构内容".encode() + bytes(range(256)) + b"\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(255, 0, -1))
SECRET = "虚构甲公司付款条款"


@pytest.fixture
def files_dir(tmp_path, monkeypatch):
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    monkeypatch.setenv("FILES_DIR", str(tmp_path / "files"))
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    yield tmp_path / "files"
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


def make_task(sm, owner, files_dir, *, status="awaiting_confirm", deleted=False, name="虚构 合同.pdf", ext="pdf",
              data=PDF):
    now = utcnow()
    with sm() as db:
        t = Task(owner_id=owner.id, title=name, stance="sales", source="outgoing", policy="ai", status=status,
                 created_at=now, updated_at=now)
        db.add(t)
        db.flush()
        key, sha = storage.save_original(data)
        f = TaskFile(task_id=t.id, seq=1, is_main=True, original_name=name, ext=ext, size_bytes=len(data),
                     sha256=sha, storage_key=key, created_at=now,
                     original_deleted_at=now if deleted else None)
        db.add(f)
        db.flush()
        if deleted:
            storage.delete_original(key)
        # 乱序插入，验证按 ord 排序；第二条在保留期清理后 text_enc 为空
        db.add(DocNode(task_id=t.id, file_id=f.id, loc="F1-PG1-B2", ord=2, kind="ocr_block", part="body",
                       page=1, bbox=[10.5, 20, 30, 40], ocr_conf=0.93,
                       text_enc=None if deleted else crypto.encrypt_text("第二段")))
        db.add(DocNode(task_id=t.id, file_id=f.id, loc="F1-PG1-B1", ord=1, kind="paragraph", part="body",
                       page=1, clause_no="1.1", text_enc=None if deleted else crypto.encrypt_text(SECRET)))
        db.commit()
        return t.id, f.id


def url(tid, fid, tail):
    return f"{API_PREFIX}/tasks/{tid}/files/{fid}/{tail}"


def test_nodes_sorted_decrypted_with_all_fields(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir)
    c, _ = login("alice")
    r = c.get(url(tid, fid, "nodes"))
    assert r.status_code == 200
    body = r.json()
    assert body["text_available"] is True
    assert [n["loc"] for n in body["items"]] == ["F1-PG1-B1", "F1-PG1-B2"]
    first, second = body["items"]
    assert first["text"] == SECRET and first["clause_no"] == "1.1" and first["bbox"] is None
    assert second["text"] == "第二段" and second["bbox"] == [10.5, 20.0, 30.0, 40.0]
    assert (second["page"], second["kind"], second["part"], second["ocr_conf"]) == (1, "ocr_block", "body", pytest.approx(0.93, abs=1e-3))
    assert set(first) == {"loc", "kind", "part", "text", "clause_no", "page", "bbox", "sheet", "cell_ref",
                          "ocr_conf", "parent_loc"}


@pytest.mark.parametrize("status", ["parsing", "parse_failed"])
def test_nodes_empty_while_parsing_or_failed(files_dir, make_user, login, db_sessionmaker, status):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir, status=status)
    c, _ = login("alice")
    r = c.get(url(tid, fid, "nodes"))
    assert r.status_code == 200 and r.json()["items"] == []


def test_nodes_text_unavailable_after_retention_cleanup(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir, status="reviewed", deleted=True)
    c, _ = login("alice")
    body = c.get(url(tid, fid, "nodes")).json()
    assert body["text_available"] is False
    assert [n["text"] for n in body["items"]] == [None, None]
    assert body["items"][0]["loc"] == "F1-PG1-B1"  # 位置信息仍在


def test_raw_bytes_and_headers(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    name = "虚构 合同.pdf"
    tid, fid = make_task(db_sessionmaker, alice, files_dir, name=name)
    c, _ = login("alice")
    r = c.get(url(tid, fid, "raw"))
    assert r.status_code == 200
    assert r.content == PDF
    h = r.headers
    assert h["content-type"] == "application/pdf"
    assert h["content-disposition"] == f"inline; filename*=UTF-8''{quote(name, safe='')}"
    assert h["cache-control"] == "no-store"
    assert h["x-content-type-options"] == "nosniff"
    assert h["content-security-policy"] == "default-src 'none'; sandbox"


@pytest.mark.parametrize("ext,data,media", [("png", PNG, "image/png"), ("jpg", b"\xff\xd8\xff" + b"\x00" * 20, "image/jpeg")])
def test_raw_images(files_dir, make_user, login, db_sessionmaker, ext, data, media):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir, name=f"图.{ext}", ext=ext, data=data)
    c, _ = login("alice")
    r = c.get(url(tid, fid, "raw"))
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == media


@pytest.mark.parametrize("ext", ["docx", "xlsx"])
def test_raw_not_available_for_other_formats(files_dir, make_user, login, db_sessionmaker, ext):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir, name=f"a.{ext}", ext=ext, data=b"PK\x03\x04x")
    c, _ = login("alice")
    r = c.get(url(tid, fid, "raw"))
    assert r.status_code == 404 and r.json()["code"] == "RAW_NOT_AVAILABLE"


def test_raw_original_deleted(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir, status="reviewed", deleted=True)
    c, _ = login("alice")
    r = c.get(url(tid, fid, "raw"))
    assert r.status_code == 404
    assert r.json()["code"] == "ORIGINAL_DELETED"
    assert r.json()["message"] == "原件已按保留期限删除"


def test_raw_missing_file_on_disk_is_original_deleted(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir)
    with db_sessionmaker() as db:
        storage.delete_original(db.get(TaskFile, fid).storage_key)
    c, _ = login("alice")
    r = c.get(url(tid, fid, "raw"))
    assert r.status_code == 404 and r.json()["code"] == "ORIGINAL_DELETED"


def test_raw_writes_no_audit_log(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir)
    c, _ = login("alice")
    with db_sessionmaker() as db:
        before = len(db.scalars(select(AuditLog)).all())
    c.get(url(tid, fid, "raw"))
    c.get(url(tid, fid, "nodes"))
    with db_sessionmaker() as db:
        assert len(db.scalars(select(AuditLog)).all()) == before


def test_file_of_another_task_and_bad_ids_404(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    t1, f1 = make_task(db_sessionmaker, alice, files_dir)
    t2, f2 = make_task(db_sessionmaker, alice, files_dir)
    c, _ = login("alice")
    for tail in ("nodes", "raw"):
        assert c.get(url(t1, f2, tail)).status_code == 404  # 文件不属于该任务
        assert c.get(url(t1, uuid.uuid4(), tail)).status_code == 404
        assert c.get(url(t1, "不是uuid", tail)).status_code == 404


def test_a06_others_and_admin_get_404(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    make_user("bob")
    make_user("carl", role="admin")
    tid, fid = make_task(db_sessionmaker, alice, files_dir)
    ghost = uuid.uuid4()
    for who in ("bob", "carl"):
        c, _ = login(who)
        for tail in ("nodes", "raw"):
            r = c.get(url(tid, fid, tail))
            g = c.get(url(ghost, fid, tail))
            assert r.status_code == 404, (who, tail)
            assert r.json() == g.json()  # 与“任务不存在”完全相同
            assert SECRET.encode() not in r.content
    c, _ = login("alice")
    assert c.get(url(tid, fid, "nodes")).status_code == 200


def test_unauthenticated_gets_401(files_dir, make_user, db_sessionmaker, api):
    alice = make_user("alice")
    tid, fid = make_task(db_sessionmaker, alice, files_dir)
    assert api.get(url(tid, fid, "nodes")).status_code == 401
    assert api.get(url(tid, fid, "raw")).status_code == 401
