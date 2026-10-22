"""备份流程、触发与解密命令（SPEC 16.2、16.3、T-16，设计 docs/design/M7.md 3.3–3.5）。

使用真实测试库和 pg_dump；BACKUP_DIR、FILES_DIR、备份密码文件都在临时目录。全部内容虚构。
"""
import hashlib
import io
import json
import logging
import os
import subprocess
import tarfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app import cli
from app.admin.settings_store import set_setting
from app.backup import job as backup_job
from app.backup import service
from app.backup.archive import decrypt_stream
from app.config import get_settings
from app.db import jobs
from app.db.models import AuditLog, BackupRecord, Job

PASSWORD = "fictive-backup-password-2026"
SH = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 22, 2, 0, tzinfo=SH)
KEY_A = "a" * 32
KEY_B = "0123456789abcdef" * 2


@pytest.fixture
def env(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backup"
    files_dir = tmp_path / "files"
    backup_dir.mkdir()
    files_dir.mkdir()
    (files_dir / KEY_A).write_bytes(b"encrypted-original-A" * 10)
    (files_dir / KEY_B).write_bytes(os.urandom(3000))
    (files_dir / (KEY_A + ".tmp")).write_bytes(b"half written")
    (files_dir / "README").write_bytes(b"not an original")
    pw = tmp_path / "backup_password"
    pw.write_text(PASSWORD + "\n", encoding="utf-8")
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("FILES_DIR", str(files_dir))
    monkeypatch.setenv("BACKUP_PASSWORD_FILE", str(pw))
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    get_settings.cache_clear()
    yield {"backup": backup_dir, "files": files_dir, "pw": pw, "tmp": tmp_path}
    get_settings.cache_clear()


def _run(sm, now=T0, trigger="manual", user_id=None):
    with sm() as db:
        return service.run_backup(db, trigger=trigger, user_id=user_id, now=now)


def _names(d: Path) -> set[str]:
    return {p.name for p in d.iterdir()}


def _decrypt(path: Path) -> bytes:
    out = io.BytesIO()
    with open(path, "rb") as f:
        decrypt_stream(f, out, PASSWORD.encode())
    return out.getvalue()


# ---------------------------------------------------------------- 成功


def test_backup_success_layout_contents_and_record(env, db_sessionmaker, make_user, tmp_path):
    admin = make_user("backup_admin", role="admin")
    rec = _run(db_sessionmaker, user_id=admin.id)
    name = "aireview_20261022_0200.bak"
    assert rec.status == "success" and rec.file_name == name and rec.trigger == "manual"
    assert _names(env["backup"]) == {name, name + ".sha256"}  # 无 .tmp、无 .work
    bak = env["backup"] / name
    data = bak.read_bytes()
    assert rec.size_bytes == len(data) and rec.sha256 == hashlib.sha256(data).hexdigest()
    assert (env["backup"] / (name + ".sha256")).read_text() == f"{rec.sha256}  {name}\n"
    assert subprocess.run(["sha256sum", "-c", name + ".sha256"], cwd=env["backup"],
                          capture_output=True).returncode == 0
    # Q-120：.bak、.sha256 为 0640，组 aireview 可读以便 SFTP 拉取
    assert oct(bak.stat().st_mode & 0o777) == "0o640"
    assert oct((env["backup"] / (name + ".sha256")).stat().st_mode & 0o777) == "0o640"

    with tarfile.open(fileobj=io.BytesIO(_decrypt(bak)), mode="r:") as tar:
        names = tar.getnames()
        assert names[0] == "manifest.json"
        assert sorted(names) == sorted(["manifest.json", "db.dump", f"files/{KEY_A}", f"files/{KEY_B}"])
        manifest = json.loads(tar.extractfile("manifest.json").read())
        assert manifest["format"] == 1 and manifest["file_count"] == 2 and manifest["task_count"] == 0
        assert manifest["db_dump"] == "db.dump" and manifest["app_version"]
        for key in (KEY_A, KEY_B):
            assert tar.extractfile(f"files/{key}").read() == (env["files"] / key).read_bytes()
        dump = tmp_path / "db.dump"
        dump.write_bytes(tar.extractfile("db.dump").read())
    listed = subprocess.run(["pg_restore", "--list", str(dump)], capture_output=True, text=True)
    assert listed.returncode == 0 and "TABLE public tasks" in listed.stdout

    with db_sessionmaker() as db:
        logs = db.scalars(select(AuditLog).where(AuditLog.action == "backup_run")).all()
        assert [(a.user_id, a.target_type, a.target_id, a.detail) for a in logs] == [
            (admin.id, "backup", str(rec.id), {"trigger": "manual", "status": "success"})]


def test_only_tmp_exists_while_writing(env, db_sessionmaker, monkeypatch):
    """T-16：写入期间只有 .tmp，没有 .bak 和 .sha256。"""
    seen: list[set[str]] = []
    real = service.EncryptingWriter

    class Spy(real):
        def write(self, data):
            if not seen:
                seen.append(_names(env["backup"]))
            return super().write(data)

    monkeypatch.setattr(service, "EncryptingWriter", Spy)
    rec = _run(db_sessionmaker)
    assert rec.status == "success"
    assert seen and seen[0] == {"aireview_20261022_0200.bak.tmp", ".work"}


def test_rotation_deletes_only_oldest_matching_files(env, db_sessionmaker):
    d = env["backup"]
    for stamp in ("20261020_0200", "20261021_0200"):
        (d / f"aireview_{stamp}.bak").write_bytes(b"old")
        (d / f"aireview_{stamp}.bak.sha256").write_text("x")
    keep_alone = {"aireview_x.txt", "other.bak", "aireview_2026.bak", "aireview_20261019_0200.bak.sha256"}
    for n in keep_alone:
        (d / n).write_bytes(b"keep")
    with db_sessionmaker() as db:
        set_setting(db, "backup.keep", 2, None)
        db.commit()
    assert _run(db_sessionmaker).status == "success"
    assert _names(d) == keep_alone | {"aireview_20261021_0200.bak", "aireview_20261021_0200.bak.sha256",
                                      "aireview_20261022_0200.bak", "aireview_20261022_0200.bak.sha256"}


def test_stale_tmp_and_interrupted_record_cleaned(env, db_sessionmaker):
    (env["backup"] / "aireview_20261021_0200.bak.tmp").write_bytes(b"crashed")
    (env["backup"] / "keep.tmp").write_bytes(b"not ours")
    with db_sessionmaker() as db:
        db.add(BackupRecord(trigger="schedule", status="running"))
        db.commit()
    rec = _run(db_sessionmaker)
    assert rec.status == "success"
    assert "aireview_20261021_0200.bak.tmp" not in _names(env["backup"]) and "keep.tmp" in _names(env["backup"])
    with db_sessionmaker() as db:
        rows = db.scalars(select(BackupRecord).order_by(BackupRecord.id)).all()
        assert [(r.status, r.error_message) for r in rows] == [("failed", "备份中断"), ("success", None)]
        assert rows[0].finished_at is not None


# ---------------------------------------------------------------- 失败


def _assert_failed(env, sm, message: str, *, extra: set[str] = frozenset()):
    with sm() as db:
        rec = db.scalars(select(BackupRecord).order_by(BackupRecord.id.desc())).first()
        assert rec.status == "failed" and rec.error_message == message and rec.finished_at is not None
        assert rec.file_name is None
        audit = db.scalars(select(AuditLog).where(AuditLog.action == "backup_run")).all()[-1]
        assert audit.detail == {"trigger": "manual", "status": "failed"}
    assert _names(env["backup"]) == set(extra)


def test_pg_dump_failure(env, db_sessionmaker, monkeypatch):
    (env["backup"] / "aireview_20261001_0200.bak").write_bytes(b"old")
    with db_sessionmaker() as db:
        set_setting(db, "backup.keep", 1, None)
        db.commit()
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    rec = _run(db_sessionmaker)
    assert rec.status == "failed"
    # 失败时不轮换
    _assert_failed(env, db_sessionmaker, "数据库导出失败（返回码 1）", extra={"aireview_20261001_0200.bak"})


def test_write_failure_removes_tmp(env, db_sessionmaker, monkeypatch):
    real = service.EncryptingWriter

    class Broken(real):
        def write(self, data):
            raise OSError("disk full: /secret/path")

    monkeypatch.setattr(service, "EncryptingWriter", Broken)
    _run(db_sessionmaker)
    _assert_failed(env, db_sessionmaker, "备份失败（OSError）")


@pytest.mark.parametrize("content", [b"", b"short\n"])
def test_invalid_password_file(env, db_sessionmaker, content):
    env["pw"].write_bytes(content)
    _run(db_sessionmaker)
    _assert_failed(env, db_sessionmaker, "备份密码文件无效：密码至少 12 个字符")


def test_missing_password_file_and_backup_dir(env, db_sessionmaker, monkeypatch):
    env["pw"].unlink()
    _run(db_sessionmaker)
    _assert_failed(env, db_sessionmaker, "备份密码文件无效：无法读取")
    monkeypatch.setenv("BACKUP_DIR", str(env["tmp"] / "no-such-dir"))
    get_settings.cache_clear()
    rec = _run(db_sessionmaker)
    assert rec.status == "failed" and rec.error_message == "备份目录不存在或不可写"


def test_same_minute_backup_fails_without_overwriting(env, db_sessionmaker):
    first = _run(db_sessionmaker)
    before = (env["backup"] / first.file_name).read_bytes()
    _run(db_sessionmaker)
    _assert_failed(env, db_sessionmaker, "同一分钟内已有备份，请稍后再试",
                   extra={first.file_name, first.file_name + ".sha256"})
    assert (env["backup"] / first.file_name).read_bytes() == before


def test_lock_held_skips_without_record(env, db_sessionmaker, db_engine):
    with db_engine.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": service.LOCK_KEY}).scalar()
        other.commit()
        assert _run(db_sessionmaker) is None
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": service.LOCK_KEY})
        other.commit()
    with db_sessionmaker() as db:
        assert db.scalars(select(BackupRecord)).all() == []
    assert _run(db_sessionmaker).status == "success"  # 锁已释放


def test_secrets_not_in_argv_logs_or_records(env, db_sessionmaker, monkeypatch, caplog):
    calls = []
    real_run = subprocess.run

    def spy(args, **kw):
        calls.append((args, kw.get("env", {})))
        return real_run(args, **kw)

    monkeypatch.setattr(service.subprocess, "run", spy)
    with caplog.at_level(logging.DEBUG):
        rec = _run(db_sessionmaker)
    assert rec.status == "success"
    (args, pg_env), = calls
    db_password = db_sessionmaker.kw["bind"].url.password
    assert pg_env["PGPASSWORD"] == db_password and pg_env["PGDATABASE"]
    # 命令行只有固定参数和输出路径，连接串与密码只在环境变量中
    assert args[:5] == ["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-f"] and len(args) == 6
    assert args[5].endswith("/backup/.work/db.dump")
    assert PASSWORD not in caplog.text and "PGPASSWORD" not in caplog.text
    with db_sessionmaker() as db:
        for r in db.scalars(select(BackupRecord)):
            assert PASSWORD not in (r.error_message or "")
    assert PASSWORD.encode() not in (env["backup"] / rec.file_name).read_bytes()


# ---------------------------------------------------------------- 触发


def test_enqueue_backup_rejects_when_busy(env, db_sessionmaker):
    with db_sessionmaker() as db:
        first = backup_job.enqueue_backup(db, trigger="manual", user_id=None)
        db.commit()
        assert first is not None and first.payload == {"trigger": "manual", "user_id": None}
        assert backup_job.enqueue_backup(db, trigger="manual", user_id=None) is None
        db.rollback()
        db.get(Job, first.id).status = "done"
        db.add(BackupRecord(trigger="schedule", status="running"))
        db.commit()
        assert backup_job.enqueue_backup(db, trigger="manual", user_id=None) is None


def test_check_schedule_once_per_local_day(env, db_sessionmaker):
    # jobs.created_at 取数据库当前时间，所以“今天”用真实的当天
    today = datetime.now(SH).replace(second=0, microsecond=0)
    with db_sessionmaker() as db:
        set_setting(db, "backup.time", "02:00", None)
        db.commit()
        assert backup_job.check_schedule(db, today.replace(hour=1, minute=59)) is False
        assert backup_job.check_schedule(db, today.replace(hour=2, minute=0)) is True
        job = db.scalars(select(Job)).one()
        assert job.type == "backup" and job.payload == {"trigger": "schedule", "user_id": None}
        job.status = "done"
        db.commit()
        # 同一天不再入队；修改时间后当天已做过的不重做
        assert backup_job.check_schedule(db, today.replace(hour=9, minute=0)) is False
        set_setting(db, "backup.time", "08:00", None)
        db.commit()
        assert backup_job.check_schedule(db, today.replace(hour=9, minute=0)) is False
        # 第二天到点再入队
        assert backup_job.check_schedule(db, today.replace(hour=8, minute=1) + timedelta(days=1)) is True
        assert len(db.scalars(select(Job)).all()) == 2


def test_check_schedule_catch_up_and_busy(env, db_sessionmaker):
    with db_sessionmaker() as db:
        # 当天任何时间晚于设定时间启动都会补做；有立即备份在排队时等下一分钟
        backup_job.enqueue_backup(db, trigger="manual", user_id=None)
        db.commit()
        now = datetime.now(SH).replace(hour=23, minute=30)
        assert backup_job.check_schedule(db, now) is False
        db.execute(text("UPDATE jobs SET status = 'done'"))
        db.commit()
        assert backup_job.check_schedule(db, now) is True


def test_check_schedule_invalid_time_skips(env, db_sessionmaker, caplog):
    with db_sessionmaker() as db:
        set_setting(db, "backup.time", "25:00", None)
        db.commit()
        assert backup_job.check_schedule(db, T0 + timedelta(hours=20)) is False
        assert db.scalars(select(Job)).all() == []


def test_handler_registered_and_runs(env, db_sessionmaker, make_user):
    from app import worker

    assert worker.HANDLERS["backup"] is backup_job.handle_backup
    assert "backup_schedule" in worker._PERIODIC
    admin = make_user("sched_admin", role="admin")
    with db_sessionmaker() as db:
        backup_job.handle_backup(db, {"trigger": "manual", "user_id": str(admin.id)})
        with pytest.raises(ValueError):
            backup_job.handle_backup(db, {"trigger": "other"})
    with db_sessionmaker() as db:
        rec = db.scalars(select(BackupRecord)).one()
        assert rec.status == "success" and rec.trigger == "manual"


def test_finalize_failed_marks_running_backup_record(db_sessionmaker):
    with db_sessionmaker() as db:
        db.add(BackupRecord(trigger="manual", status="running"))
        db.add(BackupRecord(trigger="manual", status="success"))
        db.commit()
        jobs.finalize_failed(db, "backup", {"trigger": "manual"})
        db.commit()
        assert [(r.status, r.error_message) for r in db.scalars(select(BackupRecord).order_by(BackupRecord.id))] == [
            ("failed", "备份中断"), ("success", None)]


# ---------------------------------------------------------------- 解密命令


def test_decrypt_backup_cli(env, db_sessionmaker, capsys):
    rec = _run(db_sessionmaker)
    bak = env["backup"] / rec.file_name
    out = env["tmp"] / "restore.tar"
    assert cli.main(["decrypt-backup", "--in", str(bak), "--out", str(out), "--password-file", str(env["pw"])]) == 0
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    with tarfile.open(out) as tar:
        assert "db.dump" in tar.getnames()
    # 默认读 BACKUP_PASSWORD_FILE；输出已存在时拒绝
    assert cli.main(["decrypt-backup", "--in", str(bak), "--out", str(out)]) == 1
    assert "已存在" in capsys.readouterr().err
    out2 = env["tmp"] / "restore2.tar"
    assert cli.main(["decrypt-backup", "--in", str(bak), "--out", str(out2)]) == 0

    wrong = env["tmp"] / "wrong_pw"
    wrong.write_text("another-password-0001\n")
    out3 = env["tmp"] / "restore3.tar"
    assert cli.main(["decrypt-backup", "--in", str(bak), "--out", str(out3), "--password-file", str(wrong)]) == 1
    err = capsys.readouterr().err
    assert not out3.exists() and "密码不正确" in err and "another-password" not in err

    truncated = env["tmp"] / "cut.bak"
    truncated.write_bytes(bak.read_bytes()[:-5])
    assert cli.main(["decrypt-backup", "--in", str(truncated), "--out", str(out3)]) == 1
    assert not out3.exists()
    assert cli.main(["decrypt-backup", "--in", str(env["tmp"] / "none.bak"), "--out", str(out3)]) == 1


def test_backup_restores_into_fresh_database(env, db_sessionmaker, make_user, pg_url, tmp_path):
    """备份中的 db.dump 能用 pg_restore 恢复到一个新库，数据一致（M8 恢复演练的前提）。"""
    import psycopg

    from tests.conftest import TEST_DATABASE_URL  # noqa: I001

    make_user("restore_check_user")
    rec = _run(db_sessionmaker)
    with tarfile.open(fileobj=io.BytesIO(_decrypt(env["backup"] / rec.file_name)), mode="r:") as tar:
        dump = tmp_path / "db.dump"
        dump.write_bytes(tar.extractfile("db.dump").read())
    name = "aireview_test_restore_" + uuid.uuid4().hex[:8]
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = db_sessionmaker.kw["bind"].url.set(database=name)
        pg_env = {"PATH": os.environ["PATH"], "PGHOST": url.host, "PGPORT": str(url.port or 5432),
                  "PGUSER": url.username, "PGPASSWORD": url.password, "PGDATABASE": name}
        r = subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "-d", name, str(dump)], env=pg_env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-500:]
        with psycopg.connect(url.render_as_string(hide_password=False).replace("+psycopg", "")) as conn:
            assert conn.execute("SELECT username FROM users").fetchall() == [("restore_check_user",)]
    finally:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
