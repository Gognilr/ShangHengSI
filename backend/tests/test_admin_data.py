"""数据页接口：备份设置与立即备份、记录、保留期、外发记录、系统状态（SPEC 15.5，设计 M7 第九节）。全部内容虚构。"""
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select

from app.admin import settings_store
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import AuditLog, BackupRecord, Job, LlmCall
from app.main import API_PREFIX

A = f"{API_PREFIX}/admin"
UTC = timezone.utc


def _hdr(csrf):
    return {"X-CSRF-Token": csrf}


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    from app.admin import data
    data._storage_cache.clear()
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backup"))
    monkeypatch.setenv("FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("APP_VERSION", "9.9.9-test")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def admin(make_user, login, dirs):
    make_user("boss", role="admin")
    return login("boss")


def _audits(sm):
    with sm() as s:
        return [(r.target_type, r.target_id, r.detail) for r in s.scalars(
            select(AuditLog).where(AuditLog.action == "setting_update").order_by(AuditLog.id))]


def test_all_endpoints_forbid_non_admin(make_user, login, api, dirs):
    make_user("alice")
    c, csrf = login("alice")
    for method, path, body in [
        ("get", "/settings/backup", None), ("put", "/settings/backup", {"time": "02:00", "keep": 7}),
        ("post", "/backup/run", None), ("get", "/backup/records", None),
        ("get", "/settings/retention", None), ("put", "/settings/retention", {"original_days": 1}),
        ("get", "/outbound-logs", None), ("get", "/outbound-logs/1", None), ("get", "/system/status", None),
    ]:
        r = getattr(c, method)(A + path, headers=_hdr(csrf), **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path
    assert api.get(A + "/system/status").status_code == 401


# ---------------------------------------------------------------- 备份设置


def test_backup_settings_get_put_and_audit(admin, dirs, db_sessionmaker):
    c, csrf = admin
    assert c.get(A + "/settings/backup").json() == {"time": "02:00", "keep": 7, "dir": str(dirs / "backup")}
    r = c.put(A + "/settings/backup", json={"time": "23:59", "keep": 60}, headers=_hdr(csrf))
    assert r.status_code == 200 and r.json()["time"] == "23:59" and r.json()["keep"] == 60
    c.put(A + "/settings/backup", json={"time": "23:59", "keep": 60}, headers=_hdr(csrf))  # 无变化
    c.put(A + "/settings/backup", json={"time": "00:00", "keep": 60}, headers=_hdr(csrf))
    assert _audits(db_sessionmaker) == [
        ("setting", "backup", {"keys": ["backup.time", "backup.keep"]}),
        ("setting", "backup", {"keys": ["backup.time"]}),
    ]


@pytest.mark.parametrize("body", [
    {"time": "24:00", "keep": 7}, {"time": "2:00", "keep": 7}, {"time": "12:60", "keep": 7}, {"time": "", "keep": 7},
    {"time": "02:00", "keep": 0}, {"time": "02:00", "keep": 61}, {"time": "02:00"}, {"keep": 7},
    {"time": "02:00", "keep": 7, "dir": "/tmp"},
])
def test_backup_settings_validation(admin, body):
    c, csrf = admin
    r = c.put(A + "/settings/backup", json=body, headers=_hdr(csrf))
    assert r.status_code == 422, body


def test_backup_settings_boundaries(admin):
    c, csrf = admin
    for body in ({"time": "00:00", "keep": 1}, {"time": "23:59", "keep": 60}):
        assert c.put(A + "/settings/backup", json=body, headers=_hdr(csrf)).status_code == 200


# ---------------------------------------------------------------- 立即备份与记录


def test_run_backup_enqueues_and_conflicts(admin, db_sessionmaker):
    c, csrf = admin
    r = c.post(A + "/backup/run", headers=_hdr(csrf))
    assert r.status_code == 202 and r.json() == {"queued": True}
    with db_sessionmaker() as s:
        job = s.scalar(select(Job).where(Job.type == "backup"))
        assert job.status == "queued" and job.payload["trigger"] == "manual" and job.payload["user_id"]
    r = c.post(A + "/backup/run", headers=_hdr(csrf))
    assert r.status_code == 409 and r.json() == {"code": "BACKUP_RUNNING", "message": "已有备份正在进行"}
    with db_sessionmaker() as s:
        assert len(s.scalars(select(Job).where(Job.type == "backup")).all()) == 1


def test_run_backup_conflicts_with_running_record(admin, db_sessionmaker):
    c, csrf = admin
    with db_sessionmaker() as s:
        s.add(BackupRecord(trigger="schedule", status="running", started_at=utcnow()))
        s.commit()
    assert c.post(A + "/backup/run", headers=_hdr(csrf)).status_code == 409


def test_run_backup_requires_csrf(admin):
    c, _ = admin
    assert c.post(A + "/backup/run").status_code == 403


def test_records_latest_and_space(admin, dirs, db_sessionmaker):
    c, csrf = admin
    empty = c.get(A + "/backup/records").json()
    assert empty["latest"] is None and empty["items"] == [] and empty["free_bytes"] is None  # 目录不存在
    (dirs / "backup").mkdir()
    with db_sessionmaker() as s:
        for i in range(25):
            s.add(BackupRecord(trigger="schedule", status="success", file_name=f"aireview_{i:04d}.bak",
                               size_bytes=i, sha256="a" * 64, started_at=utcnow(), finished_at=utcnow()))
        s.add(BackupRecord(trigger="manual", status="failed", error_message="数据库导出失败（返回码 1）",
                           started_at=utcnow(), finished_at=utcnow()))
        s.commit()
    d = c.get(A + "/backup/records").json()
    assert d["dir"] == str(dirs / "backup") and d["free_bytes"] is None and d["free_measured_at"] is None
    assert len(d["items"]) == 20
    assert d["latest"]["status"] == "failed" and d["latest"]["error_message"] == "数据库导出失败（返回码 1）"
    assert d["latest"]["id"] == d["items"][0]["id"] and d["items"][1]["file_name"] == "aireview_0024.bak"
    assert set(d["items"][0]) == {"id", "trigger", "status", "file_name", "size_bytes", "sha256", "error_message",
                                  "started_at", "finished_at"}


# ---------------------------------------------------------------- 保留期


def test_retention_settings(admin, db_sessionmaker):
    c, csrf = admin
    body = dict(original_days=30, result_days=365, outbound_days=180, audit_days=0, unfinished_days=30)
    assert c.get(A + "/settings/retention").json() == body
    body["original_days"] = 365
    assert c.put(A + "/settings/retention", json=body, headers=_hdr(csrf)).json() == body
    body["original_days"] = 30
    assert c.put(A + "/settings/retention", json=body, headers=_hdr(csrf)).status_code == 200
    c.put(A + "/settings/retention", json=body, headers=_hdr(csrf))  # 无变化
    assert _audits(db_sessionmaker) == [("setting", "retention", {"keys": ["retention.original_days"]})] * 2
    for bad in (-1, 3650, 3651, 45, "x", 1.5, None):
        assert c.put(A + "/settings/retention", json={**body, "original_days": bad}, headers=_hdr(csrf)).status_code == 422, bad
    assert c.put(A + "/settings/retention", json={}, headers=_hdr(csrf)).status_code == 422


@pytest.mark.parametrize("original,result", [(365, 90), (0, 365)])
def test_v2c_retention_order(admin, original, result):
    c, csrf = admin
    body = dict(original_days=original, result_days=result, outbound_days=180, audit_days=0, unfinished_days=30)
    r = c.put(A + "/settings/retention", json=body, headers=_hdr(csrf))
    assert r.status_code == 422 and r.json()["code"] == "RETENTION_ORDER"


def test_v2c_legacy_45_get_reject_and_confirm(admin, db_sessionmaker):
    c, csrf = admin
    body = dict(original_days=45, result_days=0, outbound_days=0, audit_days=0, unfinished_days=0)
    with db_sessionmaker() as db:
        for key, value in body.items():
            settings_store.set_setting(db, "retention." + key, value, None)
        settings_store.set_setting(db, "retention.review_pending", True, None)
        db.commit()
    assert c.get(A + "/settings/retention").json() == body
    assert c.put(A + "/settings/retention", json=body, headers=_hdr(csrf)).status_code == 422
    assert any(a["code"] == "RETENTION_REVIEW" for a in c.get(f"{API_PREFIX}/auth/me").json()["alerts"])
    body["original_days"] = 30
    assert c.put(A + "/settings/retention", json=body, headers=_hdr(csrf)).status_code == 200
    assert not any(a["code"] == "RETENTION_REVIEW" for a in c.get(f"{API_PREFIX}/auth/me").json()["alerts"])


def test_v2c_same_values_clear_pending_and_audit(admin, db_sessionmaker):
    c, csrf = admin
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "retention.review_pending", True, None)
        db.commit()
    body = c.get(A + "/settings/retention").json()
    assert c.put(A + "/settings/retention", json=body, headers=_hdr(csrf)).status_code == 200
    assert _audits(db_sessionmaker)[-1][1] == "retention"
    assert not any(a["code"] == "RETENTION_REVIEW" for a in c.get(f"{API_PREFIX}/auth/me").json()["alerts"])


# ---------------------------------------------------------------- 外发记录


def _calls(sm, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    items = [
        # 上海 10-20 23:59:59 与 10-21 00:00:00 的边界
        dict(user_id=alice.id, status="sent", created_at=datetime(2026, 10, 20, 15, 59, 59, tzinfo=UTC),
             request_text="脱敏后的请求" * 3, response_text="模型回复", task_title="合同一.docx", tokens_in=10, tokens_out=5),
        dict(user_id=alice.id, status="sent", created_at=datetime(2026, 10, 20, 16, 0, 0, tzinfo=UTC),
             request_text="第二条请求", response_text="回复二", task_title="合同二.docx"),
        dict(user_id=bob.id, status="blocked", created_at=datetime(2026, 10, 21, 3, 0, 0, tzinfo=UTC),
             request_text=None, block_reason="MAP@12；PHONE@30", task_title="合同三.docx"),
        dict(user_id=bob.id, status="error", created_at=datetime(2026, 10, 21, 15, 59, 59, tzinfo=UTC),
             request_text="第四条请求", response_text="bad", error_message="模型服务调用失败（HTTP 500）"),
        dict(user_id=None, status="sent", created_at=datetime(2026, 10, 21, 16, 0, 0, tzinfo=UTC),
             request_text="第五条", response_text="r", purpose="test"),
    ]
    with sm() as s:
        for i in items:
            s.add(LlmCall(purpose=i.pop("purpose", "review"), model_name="mock-model", **i))
        s.commit()
    return alice, bob


def test_outbound_list_filters_and_fields(admin, make_user, db_sessionmaker):
    c, _ = admin
    alice, bob = _calls(db_sessionmaker, make_user)
    allrows = c.get(A + "/outbound-logs").json()
    assert allrows["total"] == 5 and allrows["page_size"] == 20 and allrows["page"] == 1
    assert [r["created_at"][:10] for r in allrows["items"]] == ["2026-10-21"] * 3 + ["2026-10-20"] * 2
    top = allrows["items"][0]
    assert top["user_name"] is None and top["purpose"] == "test" and top["chars"] == 3
    blocked = next(r for r in allrows["items"] if r["status"] == "blocked")
    assert blocked["chars"] == 0 and blocked["user_name"] == "bob" and blocked["task_title"] == "合同三.docx"
    first = next(r for r in allrows["items"] if r["task_title"] == "合同一.docx")
    assert first["chars"] == 18 and first["tokens_in"] == 10 and first["tokens_out"] == 5 and first["model_name"] == "mock-model"
    assert "request_text" not in first

    def ids(**params):
        return sorted(r["id"] for r in c.get(A + "/outbound-logs", params=params).json()["items"])

    assert len(ids(user_id=str(alice.id))) == 2 and len(ids(user_id=str(bob.id))) == 2
    assert len(ids(status="sent")) == 3 and len(ids(status="blocked")) == 1 and len(ids(status="error")) == 1
    assert len(ids(user_id=str(bob.id), status="error")) == 1
    # 日期边界按 Asia/Shanghai：10-21 当天 = 10-20 16:00 UTC 起到 10-21 16:00 UTC 之前
    assert len(ids(**{"from": "2026-10-21", "to": "2026-10-21"})) == 3
    assert len(ids(**{"from": "2026-10-20", "to": "2026-10-20"})) == 1
    assert len(ids(**{"from": "2026-10-22"})) == 1
    assert len(ids(**{"to": "2026-10-20"})) == 1
    assert len(ids(**{"from": "2026-10-23"})) == 0


@pytest.mark.parametrize("params", [{"status": "bogus"}, {"user_id": "nope"}, {"from": "20261021"}, {"page": 0}])
def test_outbound_list_validation(admin, params):
    c, _ = admin
    assert c.get(A + "/outbound-logs", params=params).status_code == 422


def test_outbound_pagination(admin, db_sessionmaker):
    c, _ = admin
    with db_sessionmaker() as s:
        for i in range(25):
            s.add(LlmCall(purpose="chat", model_name="m", status="sent", request_text="x", created_at=utcnow()))
        s.commit()
    p1 = c.get(A + "/outbound-logs").json()
    p2 = c.get(A + "/outbound-logs", params={"page": 2}).json()
    assert (p1["total"], len(p1["items"]), len(p2["items"])) == (25, 20, 5)
    assert not {r["id"] for r in p1["items"]} & {r["id"] for r in p2["items"]}


def test_outbound_detail(admin, make_user, db_sessionmaker):
    c, _ = admin
    _calls(db_sessionmaker, make_user)
    rows = c.get(A + "/outbound-logs").json()["items"]
    sent = next(r for r in rows if r["task_title"] == "合同一.docx")
    d = c.get(f"{A}/outbound-logs/{sent['id']}").json()
    assert d["request_text"] == "脱敏后的请求" * 3 and d["response_text"] == "模型回复"
    assert d["block_reason"] is None and d["error_message"] is None and d["user_name"] == "alice"
    blocked = c.get(f"{A}/outbound-logs/{next(r for r in rows if r['status'] == 'blocked')['id']}").json()
    assert blocked["request_text"] is None and blocked["block_reason"] == "MAP@12；PHONE@30"
    assert blocked["body_notice"] is None
    err = c.get(f"{A}/outbound-logs/{next(r for r in rows if r['status'] == 'error')['id']}").json()
    assert err["error_message"] == "模型服务调用失败（HTTP 500）"


@pytest.mark.parametrize("log_id", ["999999", "abc", "-1", "99999999999999999999999"])
def test_outbound_detail_not_found(admin, log_id):
    c, _ = admin
    r = c.get(f"{A}/outbound-logs/{log_id}")
    assert r.status_code == 404 and r.json()["code"] == "OUTBOUND_LOG_NOT_FOUND"


def test_outbound_is_read_only_and_writes_no_audit(admin, make_user, db_sessionmaker):
    c, csrf = admin
    _calls(db_sessionmaker, make_user)
    c.get(A + "/outbound-logs")
    c.get(A + "/outbound-logs/1")
    assert c.delete(A + "/outbound-logs/1", headers=_hdr(csrf)).status_code in (404, 405)
    assert c.post(A + "/outbound-logs", json={}, headers=_hdr(csrf)).status_code in (404, 405)
    with db_sessionmaker() as s:
        assert s.scalars(select(AuditLog).where(AuditLog.action.like("outbound%"))).all() == []
        assert len(s.scalars(select(LlmCall)).all()) == 5


# ---------------------------------------------------------------- 系统状态


def test_system_status(admin, dirs, db_sessionmaker):
    from app.admin import data
    c, _ = admin
    d = c.get(A + "/system/status").json()
    assert d["version"] == "9.9.9-test" and d["queued_jobs"] == 0 and d["data_free_bytes"] is None
    assert d["estimated_days"] is None and d["estimate_reason"] == "insufficient_history"
    (dirs / "files").mkdir()
    data._storage_cache.clear()
    with db_sessionmaker() as s:
        s.add(Job(type="parse", payload={}))
        s.add(Job(type="parse", payload={}, status="done"))
        s.commit()
    d = c.get(A + "/system/status").json()
    assert d["queued_jobs"] == 1 and isinstance(d["data_free_bytes"], int) and d["data_free_bytes"] > 0


def _stats_samples(sm, now, *, span=10, start=10, finish=20, status="success", old=False):
    with sm() as db:
        for when, size in ((now-timedelta(days=31 if old else span), start), (now, finish)):
            db.add(BackupRecord(trigger="schedule", status=status, db_bytes=size * 1024**2,
                                dir_bytes=30, dir_free_bytes=40, size_bytes=50,
                                started_at=when-timedelta(minutes=1), finished_at=when))
        db.commit()


@pytest.mark.parametrize("scenario,expected,reason", [
    ("growth", 37, None), ("short", None, "insufficient_history"),
    ("old", None, "insufficient_history"), ("negative", None, "no_growth"),
    ("no_growth", None, "no_growth"), ("failed", None, "insufficient_history"),
    ("unavailable", None, "unavailable"),
])
def test_v2c03_storage_estimate(admin, db_sessionmaker, make_user, monkeypatch, scenario, expected, reason):
    from app.admin import data
    from app.db.models import Task, TaskFile
    now = utcnow()
    _stats_samples(db_sessionmaker, now, span=6 if scenario == "short" else 10,
                   start=20 if scenario == "negative" else 10,
                   finish=10 if scenario in ("negative", "no_growth") else 20,
                   status="failed" if scenario == "failed" else "success", old=scenario == "old")
    if scenario == "growth":
        user = make_user("storage")
        with db_sessionmaker() as db:
            task = Task(owner_id=user.id, title="虚构.docx", stance="sales", source="outgoing", policy="ai", status="parsing")
            db.add(task)
            db.flush()
            db.add(TaskFile(task_id=task.id, seq=1, is_main=True, original_name="虚构.docx", ext="docx",
                            size_bytes=20*1024**2, sha256="0"*64, storage_key="0"*32,
                            created_at=now-timedelta(days=5)))
            db.commit()
    monkeypatch.setattr(data, "utcnow", lambda: now)
    monkeypatch.setattr(data, "_free", lambda path: None if scenario == "unavailable" else 900*1024**2)
    d = admin[0].get(A + "/system/status").json()
    assert (d["estimated_days"], d["estimate_reason"]) == (expected, reason)
    assert d["db_bytes"] > 0


@pytest.mark.parametrize("legacy", [False, True])
def test_v2c03_no_measured_backup(admin, db_sessionmaker, legacy):
    if legacy:
        with db_sessionmaker() as db:
            db.add(BackupRecord(trigger="manual", status="success", size_bytes=123, started_at=utcnow(), finished_at=utcnow()))
            db.commit()
    d = admin[0].get(A + "/system/status").json()
    assert all(d[key] is None for key in ("backup_dir_bytes", "latest_backup_bytes", "backup_free_bytes", "backup_stats_at", "estimated_days"))
    assert d["estimate_reason"] == "insufficient_history"


def test_v2c03_snapshot_cache_and_live_queue(admin, dirs, db_sessionmaker, monkeypatch):
    from app.admin import data
    clock = [10]
    calls = dict(files=0, database=0, backup=0)
    def counted(key, value):
        def measure(*args):
            calls[key] += 1
            return value
        return measure
    monkeypatch.setattr(data.monotime, "monotonic", lambda: clock[0])
    monkeypatch.setattr(data, "_files_bytes", counted("files", 100))
    monkeypatch.setattr(data, "_db_bytes", counted("database", 200))
    monkeypatch.setattr(data, "_latest_backup", counted("backup", None))
    c = admin[0]
    first = c.get(A + "/system/status").json()
    with db_sessionmaker() as db:
        db.add(Job(type="parse", payload={}))
        db.commit()
    second = c.get(A + "/system/status").json()
    assert second["queued_jobs"] == 1 and first["queued_jobs"] == 0
    assert second["storage_measured_at"] == first["storage_measured_at"]
    assert calls == dict(files=1, database=1, backup=1)
    clock[0] += 601
    assert c.get(A + "/system/status").status_code == 200
    assert calls == dict(files=2, database=2, backup=2)


def test_v2c03_unreadable_files_and_backup_free_source(admin, db_sessionmaker, monkeypatch):
    from app.admin import data
    _stats_samples(db_sessionmaker, utcnow())
    monkeypatch.setattr(data.os, "scandir", lambda path: (_ for _ in ()).throw(PermissionError("private path")))
    c = admin[0]
    d = c.get(A + "/system/status").json()
    assert d["files_bytes"] is None and d["db_bytes"] > 0
    monkeypatch.setattr(data, "_free", lambda path: pytest.fail("备份接口不应访问文件系统剩余空间"))
    d = c.get(A + "/backup/records").json()
    assert d["free_bytes"] == 40 and d["free_measured_at"] is not None


def test_v2c03_files_walk_skips_links_and_failed_stat(tmp_path, monkeypatch):
    from app.admin import data
    from pathlib import Path
    root = tmp_path / "files"
    root.mkdir()
    (root / "regular").write_bytes(b"123")
    (root / "nested").mkdir()
    (root / "nested" / "content").write_bytes(b"12345")
    (root / "link-file").symlink_to(root / "regular")
    (root / "link-dir").symlink_to(root / "nested", target_is_directory=True)
    (root / "unreadable").write_bytes(b"ignored")
    original = Path.stat
    def stat(path, *args, **kwargs):
        if path.name == "unreadable":
            raise OSError("stat unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    assert data._files_bytes(str(root)) == 8
