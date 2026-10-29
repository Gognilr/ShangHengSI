"""恢复（SPEC 16.3，设计 docs/design/M8.md 3.3–3.6，open_questions Q-117～Q-119）。

用 M7-01 的 run_backup 在真实测试库上做出备份并解密成 tar 作为正常样本；FILES_DIR、BACKUP_DIR、主密钥、
备份密码都在临时目录。restore_db 只操作本测试新建的库（target_db），不碰会话测试库。全部内容虚构。
（tests/test_restore.py 已是代号还原的测试，本模块的测试放在这里，见 open_questions Q-132。）
"""
import io
import json
import logging
import os
import subprocess
import tarfile
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import cli
from app.backup import restore, service
from app.backup.archive import decrypt_stream
from app.config import get_settings
from app.db import session as db_session
from app.db.base import utcnow
from app.db.models import AuditLog, BackupRecord, Job, Task, TaskFile, User
from app.db.models import Session as UserSession
from app.db.session import normalize_url
from app.security import crypto

PASSWORD = "fictive-backup-password-2026"
KEY_A = "a" * 32
KEY_B = "0123456789abcdef" * 2
KEY_C = "c" * 32


def _clear_caches():
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    db_session.get_engine.cache_clear()


@pytest.fixture
def env(tmp_path, monkeypatch, pg_url):
    backup_dir, files_dir, work = tmp_path / "backup", tmp_path / "files", tmp_path / "work"
    for d in (backup_dir, files_dir, work):
        d.mkdir()
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    pw = tmp_path / "backup_password"
    pw.write_text(PASSWORD + "\n", encoding="utf-8")
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("FILES_DIR", str(files_dir))
    monkeypatch.setenv("BACKUP_PASSWORD_FILE", str(pw))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    _clear_caches()
    crypto.encrypt_to_file(files_dir / KEY_A, b"fictive original A")
    crypto.encrypt_to_file(files_dir / KEY_B, os.urandom(3000))
    (files_dir / "README").write_bytes(b"not an original")
    yield {"backup": backup_dir, "files": files_dir, "work": work, "key": key, "tmp": tmp_path}
    if db_session.get_engine.cache_info().currsize:
        db_session.get_engine().dispose()
    _clear_caches()


def _backup_tar(env, sm) -> Path:
    """做一份真实备份并解密成 tar。"""
    with sm() as db:
        rec = service.run_backup(db, trigger="manual")
    assert rec.status == "success"
    out = env["tmp"] / f"backup-{uuid.uuid4().hex[:6]}.tar"
    with open(env["backup"] / rec.file_name, "rb") as src, open(out, "wb") as dst:
        decrypt_stream(src, dst, PASSWORD.encode())
    (env["backup"] / rec.file_name).unlink()
    (env["backup"] / (rec.file_name + ".sha256")).unlink()
    return out


def _members(tar_path: Path) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    with tarfile.open(tar_path) as tar:
        return [(m, tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers()]


def _write_tar(path: Path, members) -> Path:
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tar:
        for info, data in members:
            if data is None:
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return path


def _new_dir(env) -> Path:
    return env["files"] / restore.NEW_DIR


# ---------------------------------------------------------------- prepare


def test_prepare_ok(env, db_sessionmaker, make_user):
    make_user("restore_owner")
    originals = {k: (env["files"] / k).read_bytes() for k in (KEY_A, KEY_B)}
    tar = _backup_tar(env, db_sessionmaker)
    # 上次中断留下的 .restore-new 先被清掉
    _new_dir(env).mkdir()
    (_new_dir(env) / KEY_C).write_bytes(b"leftover")

    summary = restore.prepare(tar, env["work"])
    assert summary["file_count"] == 2 and summary["files_extracted"] == 2 and summary["task_count"] == 0
    assert summary["revision"] in restore.known_revisions() and summary["key_checked"] is True
    assert summary["created_at"] and summary["app_version"]
    assert set(summary) == {"created_at", "app_version", "task_count", "file_count", "files_extracted",
                            "revision", "key_checked"}
    dump = env["work"] / "db.dump"
    assert oct(dump.stat().st_mode & 0o777) == "0o600"
    assert oct(_new_dir(env).stat().st_mode & 0o777) == "0o700"
    assert {p.name: p.read_bytes() for p in _new_dir(env).iterdir()} == originals
    for p in _new_dir(env).iterdir():
        assert oct(p.stat().st_mode & 0o777) == "0o600"
    # 当前原件不受影响
    assert {p.name for p in env["files"].iterdir()} == {KEY_A, KEY_B, "README", restore.NEW_DIR}


def _bad(env, members, message: str):
    tar = _write_tar(env["tmp"] / "bad.tar", members)
    with pytest.raises(restore.RestoreError) as e:
        restore.prepare(tar, env["work"])
    assert message in str(e.value)
    assert not _new_dir(env).exists() and not (env["work"] / "db.dump").exists()
    assert {p.name for p in env["files"].iterdir()} == {KEY_A, KEY_B, "README"}


def test_prepare_rejects_wrong_master_key(env, db_sessionmaker):
    tar = _backup_tar(env, db_sessionmaker)
    env["key"].write_bytes(os.urandom(32))
    crypto.get_master_key.cache_clear()
    _bad(env, _members(tar), "当前主密钥与备份不匹配")


# ---------------------------------------------------------------- restore_db


def _admin():
    from tests.conftest import TEST_DATABASE_URL

    return psycopg.connect(TEST_DATABASE_URL, autocommit=True)


def _url_for(pg_url: str, name: str):
    return make_url(normalize_url(pg_url)).set(database=name)


def _pg_restore_into(pg_url: str, name: str, dump: Path) -> None:
    url = _url_for(pg_url, name)
    env = {"PATH": os.environ["PATH"], "PGHOST": url.host, "PGPORT": str(url.port or 5432),
           "PGUSER": url.username, "PGPASSWORD": url.password}
    r = subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "-d", name, str(dump)], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]


@pytest.fixture
def target(pg_url):
    """本测试专用的“当前库” D 及其 _restore、_pre_restore；结束时全部删除。"""
    name = "aireview_rt_" + uuid.uuid4().hex[:8]
    yield name
    with _admin() as admin:
        for n in (name, name + "_restore", name + "_pre_restore"):
            admin.execute(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')


def _db_exists(name: str) -> bool:
    with _admin() as admin:
        return admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is not None


def _task(owner_id, title: str) -> Task:
    return Task(owner_id=owner_id, title=title, stance="sales", source="outgoing", policy="ai", status="reviewed")


def _titles(pg_url: str, name: str) -> set[str]:
    engine = create_engine(_url_for(pg_url, name))
    try:
        with Session(engine) as s:
            return set(s.scalars(select(Task.title)))
    finally:
        engine.dispose()


@pytest.fixture
def scenario(env, db_sessionmaker, make_user, pg_url, target):
    """会话库：任务 T1、T2，一个会话，一个 running 的 job；备份（备份时自己的 backup_records 为 running）。
    然后用这份备份建出 D，并在 D 中删除 T1、新增 T3，模拟“备份之后的变化”。"""
    owner = make_user("restore_owner")
    with db_sessionmaker() as db:
        db.add_all([_task(owner.id, "虚构任务一"), _task(owner.id, "虚构任务二")])
        db.add(UserSession(token_hash="f" * 64, user_id=owner.id, csrf_token="e" * 64,
                           expires_at=utcnow()))
        db.add(Job(type="parse", payload={}, status="running"))
        db.add(Job(type="parse", payload={}, status="queued"))
        db.commit()
    tar = _backup_tar(env, db_sessionmaker)
    restore.prepare(tar, env["work"])
    dump = env["work"] / "db.dump"

    with _admin() as admin:
        admin.execute(f'CREATE DATABASE "{target}"')
    _pg_restore_into(pg_url, target, dump)
    engine = create_engine(_url_for(pg_url, target))
    with Session(engine) as s:
        s.execute(delete(Task).where(Task.title == "虚构任务一"))
        s.add(_task(s.scalar(select(User.id)), "虚构任务三"))
        s.commit()
    engine.dispose()
    assert _titles(pg_url, target) == {"虚构任务二", "虚构任务三"}
    return {"dump": dump, "target": target, "owner": owner}


def test_restore_db_replaces_database(env, scenario, pg_url):
    d = scenario["target"]
    r = restore.restore_db(scenario["dump"], target_db=d)
    assert r == {"database": d, "old_database": d + "_pre_restore", "revision": r["revision"], "files": 2}
    assert r["revision"] in restore.known_revisions()
    # 备份之后删除的任务回来、新增的任务消失；旧库原样保留
    assert _titles(pg_url, d) == {"虚构任务一", "虚构任务二"}
    assert _titles(pg_url, d + "_pre_restore") == {"虚构任务二", "虚构任务三"}
    assert not _db_exists(d + "_restore")

    engine = create_engine(_url_for(pg_url, d))
    with Session(engine) as s:
        assert s.scalars(select(UserSession)).all() == []
        assert sorted((j.status, j.last_error) for j in s.scalars(select(Job))) == [
            ("failed", "RESTORED"), ("queued", None)]
        assert all(j.finished_at for j in s.scalars(select(Job).where(Job.status == "failed")))
        # 备份时正在运行的正是这次备份本身，恢复后置为失败（Q-118）
        assert [(b.status, b.error_message) for b in s.scalars(select(BackupRecord))] == [("failed", "备份中断")]
        logs = s.scalars(select(AuditLog).where(AuditLog.action == "restore_run")).all()
        assert [(a.user_id, a.target_type, a.target_id, a.detail) for a in logs] == [
            (None, None, None, {"revision": r["revision"], "files": 2})]
    engine.dispose()

    assert restore.drop_old(target_db=d) == d + "_pre_restore"
    assert not _db_exists(d + "_pre_restore") and _db_exists(d)


def test_restore_db_failure_keeps_current(env, scenario, pg_url):
    d = scenario["target"]
    bad = env["tmp"] / "bad.dump"
    bad.write_bytes(b"PGDMP not really a dump")
    with pytest.raises(restore.RestoreError, match=r"数据库恢复失败（返回码 \d+）"):
        restore.restore_db(bad, target_db=d)
    assert _titles(pg_url, d) == {"虚构任务二", "虚构任务三"}
    assert not _db_exists(d + "_restore") and not _db_exists(d + "_pre_restore")


def test_restore_db_cli_end_to_end(env, scenario, pg_url, monkeypatch, capsys):
    """经 CLI：DATABASE_URL 指向 D 时，restore-db 恢复 D，--drop-old 删除旧库。"""
    d = scenario["target"]
    monkeypatch.setenv("DATABASE_URL", _url_for(pg_url, d).render_as_string(hide_password=False))
    _clear_caches()
    assert cli.main(["restore-db", "--dump", str(scenario["dump"])]) == 0
    out = capsys.readouterr().out
    assert "数据库已恢复" in out and f"{d}_pre_restore" in out
    assert _titles(pg_url, d) == {"虚构任务一", "虚构任务二"}
    assert cli.main(["restore-db", "--drop-old"]) == 0
    assert not _db_exists(d + "_pre_restore")


# ---------------------------------------------------------------- 原件


def test_verify(env, db_sessionmaker, make_user):
    owner = make_user("verify_owner")
    with db_sessionmaker() as db:
        task = _task(owner.id, "虚构任务")
        db.add(task)
        db.flush()
        for seq, key, deleted in ((1, KEY_A, None), (2, KEY_B, None), (3, KEY_C, utcnow())):
            db.add(TaskFile(task_id=task.id, seq=seq, is_main=seq == 1, original_name=f"虚构{seq}.docx", ext="docx",
                            size_bytes=1, sha256="0" * 64, storage_key=key, original_deleted_at=deleted))
        db.commit()
    assert restore.verify(2) == {"files": 2, "expected": 2, "records": 2, "missing": 0, "ok": True}
    assert restore.verify(3)["ok"] is False
    (env["files"] / KEY_B).unlink()
    r = restore.verify(1)
    assert r["missing"] == 1 and r["ok"] is False


# ---------------------------------------------------------------- CLI


