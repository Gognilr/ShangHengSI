"""管理员提醒（SPEC 6.7，设计 M7 第十节）：三种提醒的有 / 无边界、普通用户为空。全部内容虚构。"""
import shutil
from datetime import datetime

import pytest

from app.admin import settings_store
from app.backup.service import local_tz
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import BackupRecord, LlmCall, LlmModel
from app.main import API_PREFIX
from tests.review_support import env  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")

ME = f"{API_PREFIX}/auth/me"
GB = 1024**3
DATA = f"{API_PREFIX}/admin/backup/records"


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    d = tmp_path / "backup"
    d.mkdir()
    monkeypatch.setenv("BACKUP_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


@pytest.fixture
def admin(make_user, login, backup_dir):
    make_user("boss", role="admin")
    return login("boss")[0]


def alerts(c):
    return c.get(ME).json()["alerts"]


def add_backup(sm, status, size=None, free=None):
    with sm() as s:
        s.add(BackupRecord(trigger="schedule", status=status, size_bytes=size, dir_free_bytes=free, started_at=utcnow(),
                           finished_at=None if status == "running" else utcnow(),
                           file_name="aireview_x.bak" if status == "success" else None,
                           error_message="数据库导出失败（返回码 1）" if status == "failed" else None))
        s.commit()


def set_cap(sm, cap):
    with sm() as s:
        s.get(LlmModel, 1).monthly_call_cap = cap
        s.commit()


def add_calls(sm, n, status="sent"):
    with sm() as s:
        for _ in range(n):
            s.add(LlmCall(model_id=1, purpose="chat", model_name="m", status=status, created_at=utcnow()))
        s.commit()


def test_no_alerts_by_default(admin):
    assert alerts(admin) == []


def test_regular_user_always_empty(make_user, login, db_sessionmaker, backup_dir):
    make_user("alice")
    add_backup(db_sessionmaker, "failed")
    set_cap(db_sessionmaker, 1)
    add_calls(db_sessionmaker, 5)
    c, _ = login("alice")
    assert alerts(c) == []


# ---------------------------------------------------------------- 备份失败


def test_backup_failed(admin, db_sessionmaker):
    add_backup(db_sessionmaker, "failed")
    assert alerts(admin) == [
        {"code": "BACKUP_FAILED", "message": "最近一次备份失败，请到“设置 - 数据”查看原因"}]


# ---------------------------------------------------------------- 空间不足


def test_space_low_boundary(admin, db_sessionmaker):
    add_backup(db_sessionmaker, "success", 3 * GB, 6 * GB)
    assert alerts(admin) == []  # 恰好等于：不提示
    add_backup(db_sessionmaker, "success", 3 * GB, 6 * GB - 1)
    assert [x["code"] for x in alerts(admin)] == ["BACKUP_SPACE_LOW"]

    # Docker 的 tmpfs 模拟只有 worker 可见的卷，容量与 app 所见普通目录不同。
    worker_free = shutil.disk_usage("/dev/shm").free
    assert shutil.disk_usage(get_settings().backup_dir).free > worker_free * 2
    add_backup(db_sessionmaker, "success", worker_free, worker_free)
    add_backup(db_sessionmaker, "failed")
    records = admin.get(DATA).json()
    assert records["free_bytes"] == worker_free
    sample = datetime.fromisoformat(records["free_measured_at"]).astimezone(local_tz()).strftime("%Y-%m-%d %H:%M")
    a = alerts(admin)
    assert [x["code"] for x in a] == ["BACKUP_FAILED", "BACKUP_SPACE_LOW"]
    assert a[1]["message"].endswith(f"（{sample} 备份时统计）")

    add_backup(db_sessionmaker, "success", 3 * GB)
    records = admin.get(DATA).json()
    assert records["free_bytes"] is None and records["free_measured_at"] is None
    assert alerts(admin) == []


# ---------------------------------------------------------------- 调用上限


@pytest.mark.parametrize("cap,used,expected", [
    (10, 8, None),                      # 80%
    (10, 9, "模型“模拟模型”本月调用已达上限的 90%（9/10 次）"),   # 恰好 90%
    (10, 10, "模型“模拟模型”本月调用已达上限（10/10 次），使用该模型的审核和追问暂停"),
    (10, 12, "模型“模拟模型”本月调用已达上限（12/10 次），使用该模型的审核和追问暂停"),
    (3, 2, None),                       # 66.7%
    (3, 3, "模型“模拟模型”本月调用已达上限（3/3 次），使用该模型的审核和追问暂停"),
    (1, 0, None),
    (1000, 899, None),
    (1000, 900, "模型“模拟模型”本月调用已达上限的 90%（900/1000 次）"),
])
def test_cap_near(admin, db_sessionmaker, cap, used, expected):
    set_cap(db_sessionmaker, cap)
    add_calls(db_sessionmaker, used)
    got = alerts(admin)
    if expected is None:
        assert got == []
    else:
        assert got == [{"code": "CALL_CAP_NEAR", "message": expected}]


