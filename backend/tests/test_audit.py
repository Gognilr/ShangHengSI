"""审计日志核对（SPEC 17.7，设计 M7 第十节）：17.7 列出的每个动作都会写入，且全部 detail 不含敏感信息。全部内容虚构。"""
import json
import os

import pytest
from sqlalchemy import select

from app.admin import settings_store
from app.backup import service as backup_service
from app.config import get_settings
from app.db.models import AuditLog
from app.main import API_PREFIX
from app.security import crypto
from tests.test_report_docx import report_case  # noqa: F401
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_tasks_api import SECRET, docx_bytes, form

PASSWORD = "Test1234pass"
NEW_PASSWORD = "Brand9876new"
API_KEY = "sk-audit-ZZZZ9999YYYY"
TERM = "审计专用敏感词青松"
REQ_TEXT = "审计专用补充要求文字红杉"
PROFILE_TEXT = "审计专用公司说明文字白桦"
DISPLAY = "审计显示名灵芝"


def _hdr(csrf):
    return {"X-CSRF-Token": csrf}


def actions(sm):
    with sm() as s:
        return [r.action for r in s.scalars(select(AuditLog).order_by(AuditLog.id))]


def rows(sm, action):
    with sm() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)))


@pytest.fixture
def key_env(tmp_path, monkeypatch):
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    monkeypatch.setenv("FILES_DIR", str(tmp_path / "files"))
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    yield
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


@pytest.fixture
def admin(make_user, login, key_env):
    make_user("boss", role="admin")
    return login("boss")


def test_login_and_login_failed_are_audited(make_user, new_client, db_sessionmaker):
    make_user("alice")
    c = new_client()
    assert c.post(f"{API_PREFIX}/auth/login", json={"username": "alice", "password": "wrong-pass-1"}).status_code == 401
    assert c.post(f"{API_PREFIX}/auth/login", json={"username": "ghost-user", "password": "wrong-pass-2"}).status_code == 401
    assert c.post(f"{API_PREFIX}/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200
    assert actions(db_sessionmaker) == ["login_failed", "login_failed", "login"]
    dump = json.dumps([r.detail for r in rows(db_sessionmaker, "login_failed")])
    assert "ghost-user" not in dump and "wrong-pass" not in dump


def test_setting_and_account_changes_are_audited(admin, make_user, db_sessionmaker):
    c, csrf = admin
    u = make_user("alice")
    # 账号变更
    assert c.post(f"{API_PREFIX}/admin/users", headers=_hdr(csrf), json={
        "username": "carol", "display_name": DISPLAY, "password": NEW_PASSWORD, "role": "user"}).status_code == 201
    assert c.patch(f"{API_PREFIX}/admin/users/{u.id}", headers=_hdr(csrf), json={"display_name": DISPLAY}).status_code == 200
    assert c.post(f"{API_PREFIX}/admin/users/{u.id}/reset-password", headers=_hdr(csrf),
                  json={"new_password": NEW_PASSWORD}).status_code == 204
    # 设置修改：模型、脱敏、审核要求、备份、保留期
    assert c.put(f"{API_PREFIX}/admin/settings/model", headers=_hdr(csrf), json={
        "base_url": "https://llm.example.test/v1", "name": "mock-model", "temperature": 0.1, "max_tokens": 32000,
        "timeout_s": 180, "json_mode": True, "extra_params": {}, "chunk_chars": 60000, "price_in_per_m": "0",
        "price_out_per_m": "0", "monthly_call_cap": 0, "api_key": API_KEY}).status_code == 200
    assert c.put(f"{API_PREFIX}/admin/settings/redaction", headers=_hdr(csrf), json={"mask_quantity": True}).status_code == 200
    assert c.post(f"{API_PREFIX}/admin/terms", headers=_hdr(csrf), json={"term": TERM}).status_code == 201
    assert c.post(f"{API_PREFIX}/admin/company-requirements", headers=_hdr(csrf), json={
        "text": REQ_TEXT, "doc_types": ["all"], "stances": ["all"], "enabled": True, "sort": 1}).status_code == 201
    assert c.put(f"{API_PREFIX}/admin/company-profile", headers=_hdr(csrf), json={"text": PROFILE_TEXT}).status_code == 200
    assert c.put(f"{API_PREFIX}/admin/settings/backup", headers=_hdr(csrf), json={"time": "03:00", "keep": 5}).status_code == 200
    assert c.put(f"{API_PREFIX}/admin/settings/retention", headers=_hdr(csrf), json={
        "original_days": 30, "result_days": 0, "outbound_days": 0, "audit_days": 0,
        "unfinished_days": 0}).status_code == 200

    acts = actions(db_sessionmaker)
    assert acts.count("user_create") == 1 and acts.count("user_update") == 2
    targets = {(r.target_type, r.target_id) for r in rows(db_sessionmaker, "setting_update")}
    assert {("setting", "model"), ("setting", "redaction"), ("setting", "backup"), ("setting", "retention"),
            ("company_profile", None)} <= targets
    assert any(t == "term" for t, _ in targets) and any(t == "company_requirement" for t, _ in targets)
    assert all(r.user_id is not None for r in rows(db_sessionmaker, "setting_update"))


def test_task_create_delete_and_report_export_are_audited(make_user, login, key_env, db_sessionmaker, report_case):  # noqa: F811
    owner, task, run = report_case
    c, csrf = login(owner.username)
    up = c.post(f"{API_PREFIX}/tasks", data=form(), headers=_hdr(csrf),
                files=[("files", ("a.docx", docx_bytes(), "application/octet-stream"))])
    assert up.status_code == 201, up.text
    new_id = up.json()["id"]
    assert c.get(f"{API_PREFIX}/runs/{run.id}/report").status_code == 200
    assert c.delete(f"{API_PREFIX}/tasks/{new_id}", headers=_hdr(csrf)).status_code in (200, 204)
    acts = actions(db_sessionmaker)
    for a in ("task_create", "task_delete", "report_export"):
        assert a in acts, a
    assert [r.target_id for r in rows(db_sessionmaker, "task_create")] == [new_id]
    assert [r.target_id for r in rows(db_sessionmaker, "task_delete")] == [new_id]


def test_backup_run_is_audited_on_failure(tmp_path, monkeypatch, db_sessionmaker, key_env):
    """备份目录不存在时记为 failed，同样写 backup_run 审计。"""
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("BACKUP_PASSWORD_FILE", str(tmp_path / "no-password"))
    get_settings.cache_clear()
    with db_sessionmaker() as db:
        rec = backup_service.run_backup(db, trigger="manual", user_id=None)
        assert rec is not None and rec.status == "failed"
    r = rows(db_sessionmaker, "backup_run")
    assert len(r) == 1 and r[0].target_type == "backup" and r[0].target_id == str(rec.id)
    assert r[0].detail == {"trigger": "manual", "status": "failed"}


def test_audit_details_never_contain_secrets(admin, make_user, login, db_sessionmaker):
    """走一遍带敏感输入的操作后，扫描全部审计行：密码、API 密钥（含末 4 位）、词条、补充要求、公司说明、显示名、原文都不出现。"""
    test_setting_and_account_changes_are_audited(admin, make_user, db_sessionmaker)
    c, csrf = login("alice") if False else admin
    c.post(f"{API_PREFIX}/tasks", data=form(), headers=_hdr(csrf),
           files=[("files", ("a.docx", docx_bytes(), "application/octet-stream"))])
    with db_sessionmaker() as s:
        dump = "\n".join(
            f"{r.action}|{r.target_type}|{r.target_id}|{json.dumps(r.detail, ensure_ascii=False)}|{r.ip}"
            for r in s.scalars(select(AuditLog)))
    assert dump
    for secret in (PASSWORD, NEW_PASSWORD, API_KEY, API_KEY[-4:], TERM, REQ_TEXT, PROFILE_TEXT, DISPLAY, SECRET,
                   "a.docx", "审计专用"):
        assert secret not in dump, secret


def test_every_17_7_action_name_present_in_codebase():
    """17.7 列出的动作各有对应的 action 名（登录、登录失败、设置修改、账号变更、任务创建与删除、报告导出、备份执行）。"""
    expected = {"login", "login_failed", "setting_update", "user_create", "user_update", "task_create",
                "task_delete", "report_export", "backup_run"}
    import pathlib

    source = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path("app").rglob("*.py"))
    for name in expected:
        assert f'"{name}"' in source, name
