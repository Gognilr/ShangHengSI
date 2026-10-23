"""管理员提醒（SPEC 6.7，设计 M7 第十节）：三种提醒的有 / 无边界、普通用户为空。全部内容虚构。"""
from collections import namedtuple

import pytest

from app.admin import alerts as alerts_module
from app.admin import settings_store
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import BackupRecord, LlmCall
from app.main import API_PREFIX
from tests.review_support import env  # noqa: F401

ME = f"{API_PREFIX}/auth/me"
GB = 1024**3
Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    d = tmp_path / "backup"
    d.mkdir()
    monkeypatch.setenv("BACKUP_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


@pytest.fixture
def free_space(monkeypatch):
    """控制备份目录的剩余空间：free_space(n_bytes)。"""

    def _set(free):
        monkeypatch.setattr(alerts_module.shutil, "disk_usage", lambda p: Usage(100 * GB, 0, free))

    return _set


@pytest.fixture
def admin(make_user, login, backup_dir):
    make_user("boss", role="admin")
    return login("boss")[0]


def alerts(c):
    return c.get(ME).json()["alerts"]


def add_backup(sm, status, size=None):
    with sm() as s:
        s.add(BackupRecord(trigger="schedule", status=status, size_bytes=size, started_at=utcnow(),
                           finished_at=None if status == "running" else utcnow(),
                           file_name="aireview_x.bak" if status == "success" else None,
                           error_message="数据库导出失败（返回码 1）" if status == "failed" else None))
        s.commit()


def set_cap(sm, cap):
    with sm() as s:
        settings_store.set_setting(s, "model.monthly_call_cap", cap, None)
        s.commit()


def add_calls(sm, n, status="sent"):
    with sm() as s:
        for _ in range(n):
            s.add(LlmCall(purpose="chat", model_name="m", status=status, created_at=utcnow()))
        s.commit()


def test_no_alerts_by_default(admin):
    assert alerts(admin) == []


def test_prompt_term_conflict_enabled_and_disabled(admin, db_sessionmaker, env):
    from app.db.models import RedactionTerm
    from app.security import crypto

    with db_sessionmaker() as db:
        term = RedactionTerm(kind="custom", category="CUSTOM", term_hash="a" * 64,
                             term_enc=crypto.encrypt_text("验收"), enabled=True)
        db.add(term)
        db.commit()
        term_id = term.id
    got = alerts(admin)
    assert len(got) == 1 and got[0]["code"] == "PROMPT_TERM_CONFLICT"
    assert "1 个" in got[0]["message"] and "验收" not in got[0]["message"]
    with db_sessionmaker() as db:
        db.get(RedactionTerm, term_id).enabled = False
        db.commit()
    assert alerts(admin) == []


def test_regular_user_always_empty(make_user, login, db_sessionmaker, backup_dir):
    make_user("alice")
    add_backup(db_sessionmaker, "failed")
    set_cap(db_sessionmaker, 1)
    add_calls(db_sessionmaker, 5)
    c, _ = login("alice")
    assert alerts(c) == []


def test_must_change_password_admin_gets_no_alerts(make_user, login, db_sessionmaker, backup_dir):
    make_user("boss", role="admin", must_change_password=True)
    add_backup(db_sessionmaker, "failed")
    c, _ = login("boss")
    assert alerts(c) == []


# ---------------------------------------------------------------- 备份失败


def test_backup_failed(admin, db_sessionmaker):
    add_backup(db_sessionmaker, "failed")
    assert alerts(admin) == [
        {"code": "BACKUP_FAILED", "message": "最近一次备份失败，请到“设置 - 数据”查看原因"}]


def test_backup_failed_cleared_by_later_success(admin, db_sessionmaker, free_space):
    free_space(50 * GB)
    add_backup(db_sessionmaker, "failed")
    add_backup(db_sessionmaker, "success", GB)
    assert alerts(admin) == []


def test_running_record_is_ignored_for_failure(admin, db_sessionmaker, free_space):
    free_space(50 * GB)
    add_backup(db_sessionmaker, "failed")
    add_backup(db_sessionmaker, "running")
    assert [a["code"] for a in alerts(admin)] == ["BACKUP_FAILED"]  # 进行中的不算“已结束”
    add_backup(db_sessionmaker, "success", GB)
    assert alerts(admin) == []


# ---------------------------------------------------------------- 空间不足


def test_space_low_boundary(admin, db_sessionmaker, free_space):
    add_backup(db_sessionmaker, "success", 3 * GB)  # 需要 6 GB
    free_space(6 * GB)
    assert alerts(admin) == []  # 恰好等于：不提示
    free_space(6 * GB - 1)
    a = alerts(admin)
    assert [x["code"] for x in a] == ["BACKUP_SPACE_LOW"]
    assert a[0]["message"] == "备份目录剩余空间不足：剩余 6.0 GB，建议不少于 6.0 GB"
    free_space(int(1.5 * GB))
    assert alerts(admin)[0]["message"] == "备份目录剩余空间不足：剩余 1.5 GB，建议不少于 6.0 GB"


def test_space_not_checked_without_successful_backup(admin, db_sessionmaker, free_space):
    free_space(0)
    assert alerts(admin) == []
    add_backup(db_sessionmaker, "failed")
    assert [a["code"] for a in alerts(admin)] == ["BACKUP_FAILED"]


def test_space_uses_latest_successful_backup(admin, db_sessionmaker, free_space):
    free_space(5 * GB)
    add_backup(db_sessionmaker, "success", 10 * GB)
    add_backup(db_sessionmaker, "success", 1 * GB)
    assert alerts(admin) == []


def test_backup_dir_unavailable(admin, db_sessionmaker, backup_dir):
    add_backup(db_sessionmaker, "success", GB)
    backup_dir.rmdir()
    a = alerts(admin)
    assert a == [{"code": "BACKUP_SPACE_LOW", "message": "备份目录不可用"}]


def test_failed_and_space_low_together(admin, db_sessionmaker, free_space):
    free_space(GB)
    add_backup(db_sessionmaker, "success", 3 * GB)
    add_backup(db_sessionmaker, "failed")
    assert [a["code"] for a in alerts(admin)] == ["BACKUP_FAILED", "BACKUP_SPACE_LOW"]


# ---------------------------------------------------------------- 调用上限


def test_cap_zero_means_unlimited(admin, db_sessionmaker):
    add_calls(db_sessionmaker, 50)
    assert alerts(admin) == []


@pytest.mark.parametrize("cap,used,expected", [
    (10, 8, None),                      # 80%
    (10, 9, "本月模型调用已达上限的 90%（9/10 次）"),   # 恰好 90%
    (10, 10, "本月模型调用已达上限（10/10 次），审核和追问暂停"),
    (10, 12, "本月模型调用已达上限（12/10 次），审核和追问暂停"),
    (3, 2, None),                       # 66.7%
    (3, 3, "本月模型调用已达上限（3/3 次），审核和追问暂停"),
    (1, 0, None),
    (1000, 899, None),
    (1000, 900, "本月模型调用已达上限的 90%（900/1000 次）"),
])
def test_cap_near(admin, db_sessionmaker, cap, used, expected):
    set_cap(db_sessionmaker, cap)
    add_calls(db_sessionmaker, used)
    got = alerts(admin)
    if expected is None:
        assert got == []
    else:
        assert got == [{"code": "CALL_CAP_NEAR", "message": expected}]


def test_cap_counts_only_sent(admin, db_sessionmaker):
    set_cap(db_sessionmaker, 10)
    add_calls(db_sessionmaker, 20, status="blocked")
    add_calls(db_sessionmaker, 20, status="error")
    assert alerts(admin) == []


def test_alert_messages_contain_no_path(admin, db_sessionmaker, backup_dir, free_space):
    free_space(GB)
    add_backup(db_sessionmaker, "success", 3 * GB)
    add_backup(db_sessionmaker, "failed")
    set_cap(db_sessionmaker, 1)
    add_calls(db_sessionmaker, 1)
    text = " ".join(a["message"] for a in alerts(admin))
    assert str(backup_dir) not in text and "/" not in text.replace("（1/1 次）", "")
