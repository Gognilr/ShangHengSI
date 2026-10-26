"""任务接口（M2-02）：新建、列表、详情、删除，以及对真实接口重跑 A-06。需要 TEST_DATABASE_URL。"""
import hashlib
import io
import os
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.base import utcnow
from app.db.models import (
    AuditLog,
    DocNode,
    Finding,
    Job,
    LlmCall,
    Party,
    RedactionMap,
    ReviewRun,
    Task,
    TaskFile,
    User,
)
from app.main import API_PREFIX
from app.security import crypto
from app.tasks import storage
from tests.review_support import env, ready_task  # noqa: F401

CONTENT_TYPES = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
SECRET = "虚构甲公司专属内容ABC123"
PDF = b"%PDF-1.7\n" + SECRET.encode() + b"\n"


def docx_bytes(text: str = SECRET) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("word/document.xml", f"<w:document>{text}</w:document>".encode())
    return buf.getvalue()


@pytest.fixture
def files_dir(tmp_path, monkeypatch):
    """隔离的主密钥和原件目录。"""
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    d = tmp_path / "files"
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    monkeypatch.setenv("FILES_DIR", str(d))
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    yield d
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


def stored_files(d) -> list[str]:
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_v2c_create_activity_and_view_does_not_extend(files_dir, alice, db_sessionmaker):
    c, csrf = alice
    before = utcnow()
    r = upload(c, csrf, [("虚构.docx", docx_bytes())])
    assert r.status_code == 201
    tid = uuid.UUID(r.json()["id"])
    with db_sessionmaker() as db:
        activity = db.get(Task, tid).last_activity_at
        assert before <= activity <= utcnow()
    assert c.get(f"{API_PREFIX}/tasks/{tid}").status_code == 200
    assert c.get(f"{API_PREFIX}/tasks").status_code == 200
    with db_sessionmaker() as db:
        assert db.get(Task, tid).last_activity_at == activity


def test_v2c_patch_activity_success_and_rejection(make_user, login, db_sessionmaker, ready_task):
    user = make_user("activity")
    c, csrf = login("activity")
    task = ready_task(user)
    before = utcnow() - timedelta(days=5)
    with db_sessionmaker() as db:
        db.get(Task, task.id).last_activity_at = before
        db.commit()
    endpoint = f"{API_PREFIX}/tasks/{task.id}"
    assert c.patch(endpoint, json={"user_note": "请检查付款"}, headers={"X-CSRF-Token": csrf}).status_code == 200
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).last_activity_at > before
        db.get(Task, task.id).status = "reviewing"
        db.get(Task, task.id).last_activity_at = before
        db.commit()
    assert c.patch(endpoint, json={"user_note": "请检查付款"}, headers={"X-CSRF-Token": csrf}).status_code == 409
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).last_activity_at == before


def test_v2c03_expiry_list_detail_permanent_and_original_deleted(files_dir, make_user, login, db_sessionmaker):
    from app.admin.settings_store import set_setting
    user = make_user("expiry")
    c, _ = login("expiry")
    tid, _ = make_full_task(db_sessionmaker, user, files_dir)
    last = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with db_sessionmaker() as db:
        db.get(Task, tid).last_activity_at = last
        db.commit()
    d = c.get(f"{API_PREFIX}/tasks/{tid}").json()
    assert datetime.fromisoformat(d["original_expires_at"]) == datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert datetime.fromisoformat(d["task_expires_at"]) == datetime(2027, 1, 1, tzinfo=timezone.utc)
    row = c.get(f"{API_PREFIX}/tasks").json()["items"][0]
    assert row["task_expires_at"] == d["task_expires_at"] and row["original_expires_at"] == d["original_expires_at"]
    with db_sessionmaker() as db:
        set_setting(db, "retention.result_days", 0, None)
        for f in db.scalars(select(TaskFile).where(TaskFile.task_id == tid)):
            f.original_deleted_at = utcnow()
        db.commit()
    d = c.get(f"{API_PREFIX}/tasks/{tid}").json()
    assert d["task_expires_at"] is d["original_expires_at"] is None


def test_v2c04_duplicate_file_defaults_override_owner_and_latest(files_dir, make_user, login, db_sessionmaker):
    alice, bob = make_user("dup_a"), make_user("dup_b")
    c, csrf = login("dup_a")
    data = docx_bytes("重复文件虚构内容")
    first = upload(c, csrf, [("第一份.docx", data)])
    assert first.status_code == 201
    before_files = stored_files(files_dir)
    second = upload(c, csrf, [("改名.docx", data)])  # 表单不传 allow_duplicate，验证默认 false
    assert second.status_code == 409 and second.json()["code"] == "DUPLICATE_FILE"
    assert second.json()["task_id"] == first.json()["id"] and second.json()["title"] == "第一份.docx"
    assert second.json()["created_at"]
    assert stored_files(files_dir) == before_files
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "task_create")) == 1
    allowed = upload(c, csrf, [("继续新建.docx", data)], allow_duplicate="true")
    assert allowed.status_code == 201
    assert upload(c,csrf,[("又一份.docx",data)]).json()["task_id"] == allowed.json()["id"]
    cb, hb = login("dup_b")
    assert upload(cb,hb,[("其他用户.docx",data)]).status_code == 201
    for tid in (first.json()["id"],allowed.json()["id"]):
        assert c.delete(f"{API_PREFIX}/tasks/{tid}",headers={"X-CSRF-Token":csrf}).status_code == 204
    assert upload(c,csrf,[("删除后重传.docx",data)]).status_code == 201


def test_v2c04_duplicate_compares_only_main_file(files_dir, alice):
    c, csrf = alice
    main, attachment = docx_bytes("主文件"), docx_bytes("附件")
    assert upload(c,csrf,[("主.docx",main),("附.docx",attachment)]).status_code == 201
    assert upload(c,csrf,[("新主.docx",attachment)]).status_code == 201


def form(**kw):
    data = {"stance": "sales", "source": "outgoing", "policy": "ai"}
    data.update(kw)
    return {k: str(v) for k, v in data.items() if v is not None}


def upload(client, csrf, files, **kw):
    """files: [(文件名, 内容)]"""
    parts = [("files", (name, data, "application/octet-stream")) for name, data in files]
    return client.post(f"{API_PREFIX}/tasks", data=form(**kw), files=parts, headers={"X-CSRF-Token": csrf})


@pytest.fixture
def alice(make_user, login):
    make_user("alice")
    return login("alice")


# ---------------------------------------------------------------- 新建


def test_create_task_success(files_dir, alice, db_sessionmaker, make_user):
    c, csrf = alice
    r = upload(c, csrf, [("合同.docx", docx_bytes()), ("附件.pdf", PDF)], main_index=1, stance="purchase",
               source="incoming", user_note="  请重点看付款条款  ")
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        assert (task.status, task.stance, task.source, task.policy) == ("parsing", "purchase", "incoming", "ai")
        assert task.title == "附件.pdf" and crypto.decrypt_text(task.user_note_enc) == "请重点看付款条款"
        files = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)).all()
        assert [(f.seq, f.is_main, f.original_name, f.ext) for f in files] == [
            (1, False, "合同.docx", "docx"),
            (2, True, "附件.pdf", "pdf"),
        ]
        assert files[1].size_bytes == len(PDF) and len(files[1].sha256) == 64
        assert files[0].doc_type is None and files[0].doc_type_source is None
        # 入队 parse
        job = db.scalars(select(Job)).one()
        assert (job.type, job.status, job.payload) == ("parse", "queued", {"task_id": tid})
        # 记住本次选择
        user = db.scalars(select(User).where(User.username == "alice")).one()
        assert (user.last_stance, user.last_source) == ("purchase", "incoming")
        # 审计：只含文件数和策略，不含文件名
        log = db.scalars(select(AuditLog).where(AuditLog.action == "task_create")).one()
        assert log.target_id == tid and log.detail == {"files": 2, "policy": "ai"}
        assert "合同" not in str(log.detail) and log.user_id == user.id
    # 落盘
    assert len(stored_files(files_dir)) == 2


def test_policy_defaults_to_ai_and_local_only_accepted(files_dir, alice, db_sessionmaker):
    c, csrf = alice
    r = c.post(
        f"{API_PREFIX}/tasks",
        data={"stance": "other", "source": "outgoing"},
        files=[("files", ("a.pdf", PDF, "application/pdf"))],
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201
    r2 = upload(c, csrf, [("b.pdf", PDF)], policy="local_only", allow_duplicate="true")
    assert r2.status_code == 201
    with db_sessionmaker() as db:
        policies = {t.title: t.policy for t in db.scalars(select(Task))}
    assert policies == {"a.pdf": "ai", "b.pdf": "local_only"}


def test_stored_original_is_ciphertext_named_by_uuid(files_dir, alice, db_sessionmaker):
    c, csrf = alice
    data = docx_bytes()
    r = upload(c, csrf, [("绝密合同.docx", data)])
    assert r.status_code == 201
    names = stored_files(files_dir)
    assert len(names) == 1 and len(names[0]) == 32 and uuid.UUID(names[0]).hex == names[0]
    assert "绝密" not in names[0] and not names[0].endswith(".docx")
    blob = (files_dir / names[0]).read_bytes()
    assert SECRET.encode() not in blob and data not in blob  # 不含明文片段
    assert blob[0] == 1 and len(blob) == 1 + 12 + len(data) + 16  # 版本 + 随机数 + 密文 + 标签
    with db_sessionmaker() as db:
        f = db.scalars(select(TaskFile)).one()
        assert f.storage_key == names[0]
    assert storage.load_original(f.storage_key) == data
    assert f.sha256 == hashlib.sha256(data).hexdigest()


def test_filename_path_stripped_and_jpeg_stored_as_jpg(files_dir, alice, db_sessionmaker):
    c, csrf = alice
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 8
    r = upload(c, csrf, [("C:\\Users\\张三\\桌面\\照片.JPEG", jpg)])
    assert r.status_code == 201
    with db_sessionmaker() as db:
        f = db.scalars(select(TaskFile)).one()
        assert (f.original_name, f.ext) == ("照片.JPEG", "jpg")


@pytest.mark.parametrize(
    "files,extra,code",
    [
        ([], {}, "NO_FILES"),
        ([(f"f{i}.pdf", PDF) for i in range(11)], {}, "TOO_MANY_FILES"),
        ([("big.pdf", PDF + b"x" * (1024 * 1024))], {}, "FILE_TOO_LARGE"),
        ([("a.dot", PDF)], {}, "UNSUPPORTED_FILE_TYPE"),  # R2：doc 已支持，改用仍不支持的 dot
        ([("a.docm", docx_bytes())], {}, "UNSUPPORTED_FILE_TYPE"),
        ([("a.pdf", b"hello")], {}, "FILE_CONTENT_MISMATCH"),
        ([("a.docx", b"hello")], {}, "FILE_CONTENT_MISMATCH"),
        ([("ok.pdf", PDF), ("a.png", b"\xff\xd8\xff\xe0")], {}, "FILE_CONTENT_MISMATCH"),
        ([("a.pdf", PDF)], {"main_index": 1}, "VALIDATION_ERROR"),
        ([("a.pdf", PDF)], {"main_index": -1}, "VALIDATION_ERROR"),
        ([("a.pdf", PDF)], {"stance": "boss"}, "VALIDATION_ERROR"),
        ([("a.pdf", PDF)], {"source": "x"}, "VALIDATION_ERROR"),
        ([("a.pdf", PDF)], {"policy": "cloud"}, "VALIDATION_ERROR"),
        ([("a.pdf", PDF)], {"user_note": "字" * 501}, "VALIDATION_ERROR"),
        ([("a.pdf", PDF)], {"main_index": "abc"}, "VALIDATION_ERROR"),
    ],
)
def test_upload_errors_return_422_and_leave_nothing(files_dir, alice, db_sessionmaker, files, extra, code):
    c, csrf = alice
    r = upload(c, csrf, files, **extra)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == code
    assert SECRET not in r.text
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(TaskFile)) == 0
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert stored_files(files_dir) == []


def test_note_of_exactly_500_chars_accepted(files_dir, alice):
    c, csrf = alice
    assert upload(c, csrf, [("a.pdf", PDF)], user_note="字" * 500).status_code == 201


def test_missing_required_fields_rejected(files_dir, alice):
    c, csrf = alice
    r = c.post(f"{API_PREFIX}/tasks", data={}, files=[("files", ("a.pdf", PDF, "x"))], headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"


def test_macro_code_via_api(files_dir, alice):
    c, csrf = alice
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("word/document.xml", b"<w:document/>")
        zf.writestr("word/vbaProject.bin", b"\x00")
    r = upload(c, csrf, [("a.docx", buf.getvalue())])
    assert (r.status_code, r.json()["code"]) == (422, "MACRO_NOT_ALLOWED")


def test_archive_too_large_code_via_api(files_dir, alice, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "30")
    get_settings.cache_clear()
    c, csrf = alice
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("word/document.xml", b"<w:document/>")
        with zf.open("word/media/big.bin", "w", force_zip64=True) as w:
            for _ in range(201):
                w.write(b"\x00" * 1024 * 1024)
    r = upload(c, csrf, [("a.docx", buf.getvalue())])
    assert (r.status_code, r.json()["code"]) == (422, "ARCHIVE_TOO_LARGE")


def test_failure_while_saving_cleans_up_written_files(files_dir, alice, db_sessionmaker, monkeypatch):
    c, csrf = alice
    real = storage.save_original
    calls = {"n": 0}

    def flaky(data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("磁盘已满")
        return real(data)

    monkeypatch.setattr(storage, "save_original", flaky)
    with pytest.raises(OSError):  # 测试客户端会把未处理异常重新抛出；真实服务返回 500
        upload(c, csrf, [("a.pdf", PDF), ("b.pdf", PDF)])
    assert stored_files(files_dir) == []
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_create_requires_csrf_and_login(files_dir, alice, api):
    c, _ = alice
    r = c.post(f"{API_PREFIX}/tasks", data=form(), files=[("files", ("a.pdf", PDF, "x"))])
    assert r.status_code == 403 and r.json()["code"] == "CSRF_FAILED"
    r = api.post(f"{API_PREFIX}/tasks", data=form(), files=[("files", ("a.pdf", PDF, "x"))])
    assert r.status_code == 401


def test_upload_error_does_not_leak_into_logs(files_dir, alice, caplog):
    c, csrf = alice
    with caplog.at_level("DEBUG"):
        upload(c, csrf, [("a.pdf", PDF)], main_index=5)
        upload(c, csrf, [("a.pdf", PDF)])
    assert SECRET not in caplog.text


# ---------------------------------------------------------------- 列表


def add_task(sm, owner, title, *, created_at=None, status="reviewed", stance="sales", high=0, doc_type=None,
             extra_names=(), files_dir=None) -> uuid.UUID:
    now = created_at or utcnow()
    with sm() as db:
        t = Task(owner_id=owner.id, title=title, stance=stance, source="outgoing", policy="ai", status=status,
                 high_count=high, created_at=now, updated_at=now, last_activity_at=now)
        db.add(t)
        db.flush()
        names = [title, *extra_names]
        for i, n in enumerate(names, start=1):
            db.add(TaskFile(task_id=t.id, seq=i, is_main=(i == 1), original_name=n, ext="docx", size_bytes=1,
                            sha256="0" * 64, storage_key=uuid.uuid4().hex, created_at=now,
                            doc_type=doc_type if i == 1 else None))
        db.commit()
        return t.id


def test_list_only_own_tasks_newest_first(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    bob = make_user("bob")
    base = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    a1 = add_task(db_sessionmaker, alice, "第一份.docx", created_at=base, high=2, doc_type="contract")
    a2 = add_task(db_sessionmaker, alice, "第二份.docx", created_at=base + timedelta(hours=1), stance="purchase")
    add_task(db_sessionmaker, bob, "别人的.docx", created_at=base + timedelta(hours=2))
    c, _ = login("alice")
    r = c.get(f"{API_PREFIX}/tasks")
    assert r.status_code == 200
    body = r.json()
    assert (body["total"], body["page"], body["page_size"]) == (2, 1, 20)
    assert [i["id"] for i in body["items"]] == [str(a2), str(a1)]
    assert body["items"][1] == {
        "id": str(a1), "title": "第一份.docx", "doc_type": "contract", "stance": "sales",
        "status": "reviewed", "high_count": 2, "created_at": "2026-05-01T08:00:00Z",
        "original_expires_at": "2026-05-31T08:00:00Z", "task_expires_at": "2027-05-01T08:00:00Z",
    }
    assert body["items"][0]["doc_type"] is None and body["items"][0]["stance"] == "purchase"
    # 管理员也只看到自己的
    make_user("carl", role="admin")
    cc, _ = login("carl")
    assert cc.get(f"{API_PREFIX}/tasks").json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_list_pagination(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i in range(25):
        add_task(db_sessionmaker, alice, f"文件{i:02d}.docx", created_at=base + timedelta(minutes=i))
    c, _ = login("alice")
    p1 = c.get(f"{API_PREFIX}/tasks").json()
    p2 = c.get(f"{API_PREFIX}/tasks", params={"page": 2}).json()
    p3 = c.get(f"{API_PREFIX}/tasks", params={"page": 3}).json()
    assert (p1["total"], len(p1["items"]), len(p2["items"]), p3["items"]) == (25, 20, 5, [])
    assert p1["items"][0]["title"] == "文件24.docx" and p2["items"][-1]["title"] == "文件00.docx"
    assert len({i["id"] for i in p1["items"]} | {i["id"] for i in p2["items"]}) == 25
    assert c.get(f"{API_PREFIX}/tasks", params={"page": 0}).status_code == 422


def test_list_search_by_filename(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    bob = make_user("bob")
    t1 = add_task(db_sessionmaker, alice, "Sales Contract.docx")
    t2 = add_task(db_sessionmaker, alice, "报价单.xlsx", extra_names=["附件-CONTRACT-v2.pdf"])
    add_task(db_sessionmaker, alice, "100%_折扣.docx")
    add_task(db_sessionmaker, alice, "1000x折扣.docx")
    add_task(db_sessionmaker, bob, "contract-bob.docx")
    c, _ = login("alice")

    def ids(q):
        return {i["id"] for i in c.get(f"{API_PREFIX}/tasks", params={"q": q}).json()["items"]}

    assert ids("contract") == {str(t1), str(t2)}  # 不区分大小写；附件名也能搜到；不含他人
    assert ids("报价") == {str(t2)}
    assert len(ids("100%")) == 1  # % 按字面匹配，不当作通配符
    assert len(ids("%_折")) == 1  # _ 同理
    assert ids("不存在的名字") == set()
    assert len(ids("   ")) == 4  # 空白等于不筛选（只含本人 4 个）


def test_list_date_filter_uses_shanghai_day_boundaries(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    # UTC 16:00 == 上海次日 00:00
    t_before = add_task(db_sessionmaker, alice, "三月一日深夜.docx",
                        created_at=datetime(2026, 3, 1, 15, 59, 59, tzinfo=timezone.utc))
    t_start = add_task(db_sessionmaker, alice, "三月二日零点.docx",
                       created_at=datetime(2026, 3, 1, 16, 0, 0, tzinfo=timezone.utc))
    t_end = add_task(db_sessionmaker, alice, "三月二日深夜.docx",
                     created_at=datetime(2026, 3, 2, 15, 59, 59, tzinfo=timezone.utc))
    t_next = add_task(db_sessionmaker, alice, "三月三日零点.docx",
                      created_at=datetime(2026, 3, 2, 16, 0, 0, tzinfo=timezone.utc))
    c, _ = login("alice")

    def ids(**params):
        r = c.get(f"{API_PREFIX}/tasks", params=params)
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()["items"]}

    assert ids(**{"from": "2026-03-02", "to": "2026-03-02"}) == {str(t_start), str(t_end)}
    assert ids(**{"from": "2026-03-02"}) == {str(t_start), str(t_end), str(t_next)}
    assert ids(to="2026-03-02") == {str(t_before), str(t_start), str(t_end)}
    assert ids(**{"from": "2026-03-03", "to": "2026-03-01"}) == set()
    assert c.get(f"{API_PREFIX}/tasks", params={"from": "不是日期"}).status_code == 422


# ---------------------------------------------------------------- 详情


def test_detail_contents(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid = add_task(db_sessionmaker, alice, "主合同.docx", extra_names=["附件.docx"], status="awaiting_confirm",
                   doc_type="contract")
    now = utcnow()
    with db_sessionmaker() as db:
        task = db.get(Task, tid)
        task.user_note_enc = crypto.encrypt_text("补充说明")
        f1, f2 = db.scalars(select(TaskFile).where(TaskFile.task_id == tid).order_by(TaskFile.seq)).all()
        f1.doc_type_source = "auto"
        f1.has_revisions = True
        f1.warnings = ["检测到修订痕迹，按接受修订后的文本审核"]
        n = 0

        def node(f, loc, kind, part="body"):
            nonlocal n
            n += 1
            db.add(DocNode(task_id=tid, file_id=f.id, loc=loc, ord=n, kind=kind, part=part))

        node(f1, "F1-P1", "heading")
        node(f1, "F1-P2", "paragraph")
        node(f1, "F1-T1-R1-C1", "table_cell")
        node(f1, "F1-T1-R1-C2", "table_cell")
        node(f1, "F1-T1-R2-C1", "table_cell")
        node(f1, "F1-T2-R1-C1", "table_cell")
        node(f1, "F1-T2-R1-C1-T1-R1-C1", "table_cell")  # 嵌套表格算另一张表
        node(f1, "F1-H1", "paragraph", part="header")  # 页眉不计入
        node(f2, "F2-P1", "paragraph")
        db.commit()
    c, _ = login("alice")
    r = c.get(f"{API_PREFIX}/tasks/{tid}")
    assert r.status_code == 200
    d = r.json()
    assert (d["id"], d["title"], d["status"], d["stance"], d["source"], d["policy"], d["user_note"]) == (
        str(tid), "主合同.docx", "awaiting_confirm", "sales", "outgoing", "ai", "补充说明")
    assert d["error_message"] is None and d["latest_run"] is None and d["queue_position"] is None
    assert d["created_at"].endswith("Z")
    first, second = d["files"]
    assert first["seq"] == 1 and first["is_main"] and first["original_name"] == "主合同.docx"
    assert (first["ext"], first["size_bytes"], first["doc_type"], first["doc_type_source"]) == ("docx", 1, "contract", "auto")
    assert (first["paragraph_count"], first["table_count"], first["page_count"]) == (2, 3, None)
    assert first["has_revisions"] is True and first["warnings"] == ["检测到修订痕迹，按接受修订后的文本审核"]
    assert (second["seq"], second["is_main"], second["paragraph_count"], second["table_count"]) == (2, False, 1, 0)
    assert second["warnings"] == [] and second["doc_type"] is None
    assert "storage_key" not in first and "sha256" not in first  # 不暴露存储信息


def test_detail_latest_run_and_queue_position(files_dir, make_user, login, db_sessionmaker):
    from app.db import jobs

    alice = make_user("alice")
    tid = add_task(db_sessionmaker, alice, "合同.docx", status="reviewing")
    other = add_task(db_sessionmaker, alice, "另一份.docx", status="reviewing")
    now = utcnow()
    run_ids = {}
    with db_sessionmaker() as db:
        for t in (other, tid):  # other 的 review 任务排在前面
            run = ReviewRun(task_id=t, run_no=1, status="queued", stance="sales", source="outgoing",
                            main_doc_type="contract", created_by=alice.id, queued_at=now)
            db.add(run)
            db.flush()
            run_ids[t] = run.id
            db.get(Task, t).latest_run_id = run.id
            jobs.enqueue(db, "review", {"task_id": str(t), "run_id": str(run.id)})
        db.commit()
    c, _ = login("alice")
    d = c.get(f"{API_PREFIX}/tasks/{tid}").json()
    assert d["latest_run"] == {"id": str(run_ids[tid]), "run_no": 1, "status": "queued"}
    assert d["queue_position"] == 2
    # 开始处理后不再排队
    with db_sessionmaker() as db:
        jobs.claim(db, "w", ("review",))
        jobs.claim(db, "w", ("review",))
    assert c.get(f"{API_PREFIX}/tasks/{tid}").json()["queue_position"] is None


# ---------------------------------------------------------------- 删除


def make_full_task(sm, owner, files_dir, title="待删.docx"):
    """一个带原件文件、节点、签约方、对照表、轮次、问题、待处理 job 和 llm_calls 的任务。"""
    now = utcnow()
    with sm() as db:
        t = Task(owner_id=owner.id, title=title, stance="sales", source="outgoing", policy="ai", status="reviewed",
                 created_at=now, updated_at=now)
        db.add(t)
        db.flush()
        keys = []
        for seq in (1, 2):
            key, sha = storage.save_original(docx_bytes(f"文件{seq}"))
            keys.append(key)
            db.add(TaskFile(task_id=t.id, seq=seq, is_main=seq == 1, original_name=f"f{seq}.docx", ext="docx",
                            size_bytes=1, sha256=sha, storage_key=key, created_at=now))
        db.flush()
        fid = db.scalars(select(TaskFile.id).where(TaskFile.task_id == t.id)).first()
        db.add(DocNode(task_id=t.id, file_id=fid, loc="F1-P1", ord=1, kind="paragraph", part="body"))
        db.add(Party(task_id=t.id, code="客户_A", role_label="买方"))
        db.add(RedactionMap(task_id=t.id, map_enc=b"x" * 40))
        run = ReviewRun(task_id=t.id, run_no=1, status="completed", stance="sales", source="outgoing",
                        main_doc_type="contract", created_by=owner.id, queued_at=now)
        db.add(run)
        db.flush()
        db.add(Finding(run_id=run.id, task_id=t.id, seq=1, title="问题", category="legal", severity="high",
                       cite_status="verified", problem="说明"))
        db.add(LlmCall(user_id=owner.id, task_id=t.id, task_title="脱敏标题", run_id=run.id, purpose="review",
                       model_name="mock", status="sent", request_text="脱敏后的外发文本"))
        db.add(Job(type="parse", payload={"task_id": str(t.id)}))
        db.add(Job(type="parse", payload={"task_id": str(uuid.uuid4())}))  # 别的任务的 job 不受影响
        db.commit()
        return t.id, keys


def count(sm, model, *where):
    with sm() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def test_delete_removes_everything_but_keeps_llm_calls(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid, keys = make_full_task(db_sessionmaker, alice, files_dir)
    other_tid, other_keys = make_full_task(db_sessionmaker, alice, files_dir, title="保留.docx")
    assert len(stored_files(files_dir)) == 4
    c, csrf = login("alice")
    r = c.delete(f"{API_PREFIX}/tasks/{tid}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204 and r.content == b""
    # 原件文件、节点、轮次、问题、签约方、对照表全部消失
    assert sorted(stored_files(files_dir)) == sorted(other_keys)
    assert count(db_sessionmaker, Task, Task.id == tid) == 0
    for model in (TaskFile, DocNode, Party, RedactionMap, ReviewRun, Finding):
        assert count(db_sessionmaker, model, model.task_id == tid) == 0, model
    # llm_calls 保留
    assert count(db_sessionmaker, LlmCall, LlmCall.task_id == tid) == 1
    with db_sessionmaker() as db:
        assert db.scalar(select(LlmCall.task_title).where(LlmCall.task_id == tid)) is None
    # 排队中的 job 被清掉，别的任务的不受影响
    with db_sessionmaker() as db:
        payloads = [j.payload["task_id"] for j in db.scalars(select(Job))]
    assert str(tid) not in payloads and str(other_tid) in payloads and len(payloads) == 3
    # 审计
    with db_sessionmaker() as db:
        log = db.scalars(select(AuditLog).where(AuditLog.action == "task_delete")).one()
        assert (log.target_id, log.detail, log.user_id) == (str(tid), {"files": 2}, alice.id)
    # 其他任务完好
    assert count(db_sessionmaker, TaskFile, TaskFile.task_id == other_tid) == 2
    # 再删一次：已不存在
    assert c.delete(f"{API_PREFIX}/tasks/{tid}", headers={"X-CSRF-Token": csrf}).status_code == 404
    assert c.get(f"{API_PREFIX}/tasks/{tid}").status_code == 404


@pytest.mark.parametrize("status", ["parsing", "parse_failed", "awaiting_confirm", "reviewing", "reviewed", "completed_local"])
def test_delete_allowed_in_any_status(files_dir, make_user, login, db_sessionmaker, status):
    alice = make_user("alice")
    tid = add_task(db_sessionmaker, alice, "x.docx", status=status)
    c, csrf = login("alice")
    assert c.delete(f"{API_PREFIX}/tasks/{tid}", headers={"X-CSRF-Token": csrf}).status_code == 204


def test_delete_when_original_file_already_missing(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid, keys = make_full_task(db_sessionmaker, alice, files_dir)
    (files_dir / keys[0]).unlink()  # 例如保留期清理已删除
    c, csrf = login("alice")
    assert c.delete(f"{API_PREFIX}/tasks/{tid}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert stored_files(files_dir) == []


def test_delete_requires_csrf(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    tid = add_task(db_sessionmaker, alice, "x.docx")
    c, _ = login("alice")
    r = c.delete(f"{API_PREFIX}/tasks/{tid}")
    assert r.status_code == 403 and r.json()["code"] == "CSRF_FAILED"
    assert count(db_sessionmaker, Task, Task.id == tid) == 1


def test_delete_via_storage_key_traversal_rejected():
    for bad in ("../x", "a/b", "", "ZZ" * 16, "0" * 31):
        with pytest.raises(ValueError):
            storage.delete_original(bad)
        with pytest.raises(ValueError):
            storage.load_original(bad)


# ---------------------------------------------------------------- A-06 重跑：真实接口


def test_a06_others_and_admin_get_404_on_real_endpoints(files_dir, make_user, login, db_sessionmaker):
    alice = make_user("alice")
    make_user("bob")
    make_user("carl", role="admin")
    tid, keys = make_full_task(db_sessionmaker, alice, files_dir)
    missing = uuid.uuid4()
    for who in ("bob", "carl"):
        c, csrf = login(who)
        h = {"X-CSRF-Token": csrf}
        for method, tail in (("GET", ""), ("DELETE", "")):
            r = c.request(method, f"{API_PREFIX}/tasks/{tid}{tail}", headers=h)
            ghost = c.request(method, f"{API_PREFIX}/tasks/{missing}{tail}", headers=h)
            assert r.status_code == 404, (who, method)
            assert r.json() == ghost.json()  # 与“不存在”完全相同
        assert c.request("GET", f"{API_PREFIX}/tasks/不是uuid", headers=h).status_code == 404
        # 列表里看不到
        assert c.get(f"{API_PREFIX}/tasks").json()["total"] == 0
        # 按文件名搜索也搜不到
        assert c.get(f"{API_PREFIX}/tasks", params={"q": "待删"}).json()["total"] == 0
    # 越权删除没有产生任何副作用
    assert count(db_sessionmaker, Task, Task.id == tid) == 1
    assert sorted(stored_files(files_dir)) == sorted(keys)
    assert count(db_sessionmaker, AuditLog, AuditLog.action == "task_delete") == 0
    # 本人仍然可以访问
    c, _ = login("alice")
    assert c.get(f"{API_PREFIX}/tasks/{tid}").status_code == 200


def test_unauthenticated_gets_401(files_dir, make_user, db_sessionmaker, api):
    alice = make_user("alice")
    tid = add_task(db_sessionmaker, alice, "x.docx")
    assert api.get(f"{API_PREFIX}/tasks").status_code == 401
    assert api.get(f"{API_PREFIX}/tasks/{tid}").status_code == 401


# ---------------------------------------------------------------- 重新审核（M5-04）

from tests.review_support import REAL_BUYER_NAME, REAL_OUR_NAME, env, llm, ready_task  # noqa: E402,F401


@pytest.fixture
def rerun_user(make_user, login):
    user = make_user("alice")
    client, csrf = login("alice")
    return user, client, {"X-CSRF-Token": csrf}


def patch_task(client, headers, task_id, body):
    return client.patch(f"{API_PREFIX}/tasks/{task_id}", json=body, headers=headers)


def parties_of(client, task_id) -> dict:
    return {p["name"]: p for p in client.get(f"{API_PREFIX}/tasks/{task_id}/parties").json()["items"]}


def set_our_party(client, headers, task_id, name):
    items = list(parties_of(client, task_id).values())
    our_id = next(p["id"] for p in items if p["name"] == name)
    body = {"items": [{"id": p["id"], "is_our_party": p["id"] == our_id, "country": p["country"],
                       "org_type": p["org_type"]} for p in items]}
    r = client.put(f"{API_PREFIX}/tasks/{task_id}/parties", json=body, headers=headers)
    assert r.status_code == 200, r.text


def review_once(db_sessionmaker, client, headers, task_id) -> str:
    """确认发送并代替 worker 执行，任务进入 reviewed。"""
    from app.review import job

    r = client.post(f"{API_PREFIX}/tasks/{task_id}/runs", headers=headers)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    with db_sessionmaker() as db:
        job.run_review(db, uuid.UUID(rid))
        db.commit()
    return rid


def test_rerun_recomputes_roles_and_keeps_old_runs(db_sessionmaker, rerun_user, ready_task, llm):
    user, c, h = rerun_user
    task = ready_task(user, user_note="旧的补充说明")
    set_our_party(c, h, task.id, REAL_BUYER_NAME)  # 我方实际是买方，立场却选成了销售
    assert parties_of(c, task.id)[REAL_OUR_NAME]["code"] == "卖方_A"
    llm("normal")
    first = review_once(db_sessionmaker, c, h, task.id)
    with db_sessionmaker() as db:
        db.get(Task, task.id).high_count = 3
        db.commit()
    audits = count(db_sessionmaker, AuditLog)

    r = patch_task(c, h, task.id, {"stance": "purchase", "source": "incoming", "user_note": "  请重点看交货期  "})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["status"], d["stance"], d["source"], d["user_note"]) == (
        "awaiting_confirm", "purchase", "incoming", "请重点看交货期")
    assert d["latest_run"]["id"] == first and d["latest_run"]["run_no"] == 1
    assert d == c.get(f"{API_PREFIX}/tasks/{task.id}").json()
    # 我方选择保留，对方的代号按新立场生成：卖方 → 供应商
    ps = parties_of(c, task.id)
    assert ps[REAL_BUYER_NAME]["is_our_party"] and ps[REAL_BUYER_NAME]["code"] == "我方"
    assert ps[REAL_OUR_NAME]["code"] == "供应商_A"
    outbound = c.get(f"{API_PREFIX}/tasks/{task.id}/redaction/outbound").json()["text"]
    assert "供应商_A" in outbound and "卖方_A" not in outbound
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert t.high_count == 3 and t.latest_run_id == uuid.UUID(first)
    assert count(db_sessionmaker, AuditLog) == audits  # 不写审计

    second = review_once(db_sessionmaker, c, h, task.id)
    runs = c.get(f"{API_PREFIX}/tasks/{task.id}/runs").json()["items"]
    assert [(x["id"], x["run_no"]) for x in runs] == [(second, 2), (first, 1)]
    with db_sessionmaker() as db:
        new, old = db.get(ReviewRun, uuid.UUID(second)), db.get(ReviewRun, uuid.UUID(first))
        assert (new.stance, new.source, new.user_note) == ("purchase", "incoming", "请重点看交货期")
        assert (old.stance, old.source, old.user_note) == ("sales", "outgoing", "旧的补充说明")
    assert count(db_sessionmaker, Finding, Finding.run_id == uuid.UUID(first)) > 0


def test_rerun_keeps_user_terms_and_ignore_decisions(rerun_user, ready_task):
    import docx

    doc = docx.Document()
    for text in ("销售确认书", f"卖方：{REAL_OUR_NAME}", f"买方：{REAL_BUYER_NAME}",
                 "货物由 Northwind Fictive Holdings 承运。", "品名：虚构煅后焦。"):
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    user, c, h = rerun_user
    task = ready_task(user, [("确认书.docx", buf.getvalue())])
    base = f"{API_PREFIX}/tasks/{task.id}"
    (suspect,) = c.get(f"{base}/redaction").json()["suspects"]
    r = c.post(f"{base}/redaction/suspects/{suspect['id']}", json={"decision": "ignore"}, headers=h)
    assert r.status_code == 200, r.text
    nodes = c.get(f"{base}/files/{c.get(base).json()['files'][0]['id']}/nodes").json()["items"]
    node = next(n for n in nodes if "虚构煅后焦" in (n["text"] or ""))
    start = node["text"].index("虚构煅后焦")
    r = c.post(f"{base}/redaction/manual", headers=h, json={"loc": node["loc"], "char_start": start,
                                                            "char_end": start + 5, "category": "CUSTOM"})
    assert r.status_code == 200, r.text

    assert patch_task(c, h, task.id, {"stance": "other"}).status_code == 200
    red = c.get(f"{base}/redaction").json()
    assert [(v["text"], v["decision"]) for v in red["suspects"]] == [("Northwind Fictive Holdings", "ignore")]
    outbound = c.get(f"{base}/redaction/outbound").json()["text"]
    assert "虚构煅后焦" not in outbound and "[敏感词_1]" in outbound


def test_rerun_clears_note_in_awaiting_confirm(rerun_user, ready_task):
    user, c, h = rerun_user
    task = ready_task(user, user_note="旧说明")
    r = patch_task(c, h, task.id, {"user_note": ""})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["user_note"], d["status"], d["stance"], d["source"]) == (None, "awaiting_confirm", "sales", "outgoing")


def test_rerun_validation_errors(db_sessionmaker, rerun_user, ready_task):
    user, c, h = rerun_user
    task = ready_task(user)
    for body in ({}, {"stance": None}, {"source": None}, {"stance": "buy"}, {"source": "both"},
                 {"user_note": "x" * 501}, {"stance": 1}):
        r = patch_task(c, h, task.id, body)
        assert (r.status_code, r.json()["code"]) == (422, "VALIDATION_ERROR"), body
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).user_note_enc is None
    assert patch_task(c, h, task.id, {"user_note": "x" * 500}).status_code == 200
    assert c.patch(f"{API_PREFIX}/tasks/{task.id}", json={"stance": "purchase"}).status_code == 403  # 缺 CSRF
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).stance == "sales"


@pytest.mark.parametrize("state", ["parsing", "parse_failed", "reviewing", "completed_local"])
def test_rerun_state_conflict(db_sessionmaker, rerun_user, ready_task, state):
    user, c, h = rerun_user
    task = ready_task(user)
    with db_sessionmaker() as db:
        db.get(Task, task.id).status = state
        db.commit()
    r = patch_task(c, h, task.id, {"stance": "purchase"})
    assert (r.status_code, r.json()["code"]) == (409, "TASK_STATE_CONFLICT")
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).stance == "sales"


def test_rerun_local_only(db_sessionmaker, rerun_user, ready_task):
    user, c, h = rerun_user
    task = ready_task(user, policy="local_only")
    r = patch_task(c, h, task.id, {"stance": "purchase"})
    assert (r.status_code, r.json()["code"]) == (409, "LOCAL_ONLY")
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).stance == "sales"


def test_rerun_a06_others_and_admin_get_404(db_sessionmaker, rerun_user, ready_task, make_user, login):
    user, _, _ = rerun_user
    task = ready_task(user)
    make_user("bob")
    make_user("carl", role="admin")
    for who in ("bob", "carl"):
        c, csrf = login(who)
        h = {"X-CSRF-Token": csrf}
        r = patch_task(c, h, task.id, {"stance": "purchase"})
        ghost = patch_task(c, h, uuid.uuid4(), {"stance": "purchase"})
        assert r.status_code == 404 and r.json() == ghost.json(), who
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert (t.stance, t.status) == ("sales", "awaiting_confirm")


# ---------------------------------------------------------------- 旧格式（R2-02）

LEGACY = os.path.join(os.path.dirname(__file__), "fixtures", "legacy")


def legacy(name: str) -> bytes:
    with open(os.path.join(LEGACY, name), "rb") as f:
        return f.read()


def test_legacy_upload_stores_real_extension(files_dir, alice, db_sessionmaker):
    c, csrf = alice
    names = [("合同.doc", "contract_basic.doc"), ("合同.wps", "contract_basic.wps"), ("合同.rtf", "contract_basic.rtf"),
             ("报价.xls", "quotation.xls"), ("报价.ET", "quotation.et")]
    r = upload(c, csrf, [(n, legacy(f)) for n, f in names])
    assert r.status_code == 201, r.text
    with db_sessionmaker() as db:
        files = db.scalars(select(TaskFile).order_by(TaskFile.seq)).all()
        assert [(f.original_name, f.ext) for f in files] == [
            ("合同.doc", "doc"), ("合同.wps", "wps"), ("合同.rtf", "rtf"), ("报价.xls", "xls"), ("报价.ET", "et"),
        ]
        assert storage.load_original(files[0].storage_key) == legacy("contract_basic.doc")
        assert db.scalars(select(Job)).one().type == "parse"
    assert len(stored_files(files_dir)) == 5


@pytest.mark.parametrize(
    "name,fixture,code",
    [
        ("a.doc", "macro_word.doc", "MACRO_NOT_ALLOWED"),
        ("a.et", "macro_excel.xls", "MACRO_NOT_ALLOWED"),
        ("a.doc", "encrypted_ooxml.docx", "FILE_ENCRYPTED"),
        ("a.docx", "encrypted_ooxml.docx", "FILE_ENCRYPTED"),
        ("a.doc", "word_no_main.doc", "FILE_CONTENT_MISMATCH"),
        ("a.xls", "contract_basic.doc", "FILE_CONTENT_MISMATCH"),
        ("a.rtf", "contract_basic.doc", "FILE_CONTENT_MISMATCH"),
        ("a.doc", "../docx/contract_basic.docx", "FILE_CONTENT_MISMATCH"),
    ],
)
def test_legacy_upload_errors(files_dir, alice, db_sessionmaker, name, fixture, code):
    c, csrf = alice
    r = upload(c, csrf, [("ok.pdf", PDF), (name, legacy(fixture))])
    assert (r.status_code, r.json()["code"]) == (422, code)
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert stored_files(files_dir) == []


def test_v2q74_r4_rereview_pending_expiry_uses_original_and_result_layers(files_dir, make_user, login, db_sessionmaker):
    """V2Q-74 R4：重新审核后未提交的任务按原件层、结果层计算到期，不按未完成任务。"""
    user = make_user("expiry_rereview")
    c, _ = login("expiry_rereview")
    tid, _ = make_full_task(db_sessionmaker, user, files_dir)
    last = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with db_sessionmaker() as db:
        task = db.get(Task, tid)
        task.status = "awaiting_confirm"
        task.latest_run_id = db.scalar(select(ReviewRun.id).where(ReviewRun.task_id == tid))
        task.last_activity_at = last
        db.commit()
    d = c.get(f"{API_PREFIX}/tasks/{tid}").json()
    assert datetime.fromisoformat(d["original_expires_at"]) == datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert datetime.fromisoformat(d["task_expires_at"]) == datetime(2027, 1, 1, tzinfo=timezone.utc)
