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


def _info(name: str, type_=tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = type_
    info.linkname = linkname
    return info


def _with_manifest(members, **changes):
    out = []
    for info, data in members:
        if info.name == "manifest.json":
            data = json.dumps({**json.loads(data), **changes}).encode()
        out.append((info, data))
    return out


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


@pytest.mark.parametrize("name", ["files/XYZ", "other.txt", "files/" + "A" * 32, "../x", "files/../db.dump",
                                  "/etc/passwd", "./manifest.json", "files/" + KEY_A + "/x"])
def test_prepare_rejects_member_names(env, db_sessionmaker, name):
    members = _members(_backup_tar(env, db_sessionmaker))
    _bad(env, members + [(_info(name), b"x")], "不允许的成员名")


@pytest.mark.parametrize("type_", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.CHRTYPE,
                                   tarfile.FIFOTYPE])
def test_prepare_rejects_links_and_special_files(env, db_sessionmaker, type_):
    members = _members(_backup_tar(env, db_sessionmaker))
    _bad(env, members + [(_info("files/" + KEY_C, type_, linkname="/etc/passwd"), None)], "非普通文件")


def test_prepare_rejects_duplicate_order_and_missing_dump(env, db_sessionmaker):
    members = _members(_backup_tar(env, db_sessionmaker))
    file_member = next(m for m in members if m[0].name.startswith("files/"))
    _bad(env, members + [file_member], "成员重复")
    _bad(env, [members[1], members[0]] + members[2:], "备份清单不在首位")
    _bad(env, [m for m in members if m[0].name != "manifest.json"], "备份清单不在首位")
    _bad(env, [m for m in members if m[0].name != "db.dump"], "缺少数据库导出文件")
    _bad(env, [], "备份清单不在首位")


def test_prepare_rejects_manifest_problems(env, db_sessionmaker):
    members = _members(_backup_tar(env, db_sessionmaker))
    _bad(env, _with_manifest(members, file_count=1), "原件数多于备份清单")
    _bad(env, _with_manifest(members, format=2), "备份格式版本不支持")
    _bad(env, _with_manifest(members, format="1"), "备份格式版本不支持")
    _bad(env, _with_manifest(members, db_dump="other"), "备份清单不正确")
    broken = [(i, b"{not json" if i.name == "manifest.json" else d) for i, d in members]
    _bad(env, broken, "备份清单不正确")


def test_prepare_accepts_fewer_files_than_manifest(env, db_sessionmaker):
    """Q-114 ①：备份过程中被删除的原件会跳过，实际原件数可以少于 file_count。"""
    members = _members(_backup_tar(env, db_sessionmaker))
    tar = _write_tar(env["tmp"] / "fewer.tar", [m for m in members if m[0].name != f"files/{KEY_A}"])
    summary = restore.prepare(tar, env["work"])
    assert summary["file_count"] == 2 and summary["files_extracted"] == 1 and summary["key_checked"] is True
    assert [p.name for p in _new_dir(env).iterdir()] == [KEY_B]
    # 没有原件时跳过主密钥检查
    tar = _write_tar(env["tmp"] / "none.tar", [m for m in members if not m[0].name.startswith("files/")])
    summary = restore.prepare(tar, env["work"])
    assert summary["files_extracted"] == 0 and summary["key_checked"] is False
    assert list(_new_dir(env).iterdir()) == []


def test_prepare_rejects_unknown_revision(env, db_sessionmaker, monkeypatch):
    tar = _backup_tar(env, db_sessionmaker)
    monkeypatch.setattr(restore, "known_revisions", lambda: {"0001"})
    members = _members(tar)
    _bad(env, members, "备份来自更新的系统版本")


def test_prepare_rejects_corrupt_dump_and_tar(env, db_sessionmaker):
    members = _members(_backup_tar(env, db_sessionmaker))
    _bad(env, [(i, b"garbage" if i.name == "db.dump" else d) for i, d in members], "无法读取数据库导出文件")
    junk = env["tmp"] / "junk.tar"
    junk.write_bytes(os.urandom(2000))
    with pytest.raises(restore.RestoreError, match="不是有效的 tar 文件"):
        restore.prepare(junk, env["work"])


def test_prepare_rejects_wrong_master_key(env, db_sessionmaker):
    tar = _backup_tar(env, db_sessionmaker)
    env["key"].write_bytes(os.urandom(32))
    crypto.get_master_key.cache_clear()
    _bad(env, _members(tar), "当前主密钥与备份不匹配")


def test_prepare_rejects_leftovers_before_extracting(env, db_sessionmaker, pg_url):
    tar = _backup_tar(env, db_sessionmaker)
    (env["files"] / restore.OLD_DIR).mkdir()
    with pytest.raises(restore.RestoreError, match="旧原件目录"):
        restore.prepare(tar, env["work"])
    assert not _new_dir(env).exists()
    (env["files"] / restore.OLD_DIR).rmdir()

    old = make_url(pg_url).database + "_pre_restore"
    from tests.conftest import TEST_DATABASE_URL

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{old}"')
    try:
        with pytest.raises(restore.RestoreError, match="旧数据库"):
            restore.prepare(tar, env["work"])
        assert not _new_dir(env).exists() and not (env["work"] / "db.dump").exists()
    finally:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{old}" WITH (FORCE)')


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


def test_restore_db_refuses_when_old_database_exists(env, scenario, pg_url):
    d = scenario["target"]
    with _admin() as admin:
        admin.execute(f'CREATE DATABASE "{d}_pre_restore"')
    with pytest.raises(restore.RestoreError, match="旧数据库"):
        restore.restore_db(scenario["dump"], target_db=d)
    assert _titles(pg_url, d) == {"虚构任务二", "虚构任务三"}
    assert not _db_exists(d + "_restore")


def test_restore_db_leftover_restore_db_and_bad_names(env, scenario, pg_url):
    d = scenario["target"]
    with _admin() as admin:
        admin.execute(f'CREATE DATABASE "{d}_restore"')  # 上次中断的残留，会被重建
    restore.restore_db(scenario["dump"], target_db=d)
    assert _titles(pg_url, d) == {"虚构任务一", "虚构任务二"}
    for name in ("bad-name", "x; DROP", "a" * 41):
        with pytest.raises(restore.RestoreError, match="数据库名不符合要求"):
            restore.restore_db(scenario["dump"], target_db=name)
    with pytest.raises(restore.RestoreError, match="当前数据库不存在"):
        restore.restore_db(scenario["dump"], target_db="aireview_rt_missing")


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


def test_secrets_not_in_argv_logs_or_errors(env, scenario, pg_url, monkeypatch, caplog, capsys):
    d = scenario["target"]
    db_password = make_url(pg_url).password
    assert db_password
    calls = []
    real_run = subprocess.run

    def spy(args, **kw):
        calls.append((args, kw.get("env", {})))
        return real_run(args, **kw)

    monkeypatch.setattr(restore.subprocess, "run", spy)
    with caplog.at_level(logging.DEBUG):
        restore.restore_db(scenario["dump"], target_db=d)
        restore.dump_revision(scenario["dump"])
    assert len(calls) == 2
    # 命令行只有固定参数、库名和文件路径（测试库密码是 "test"，会出现在临时路径里，所以逐项比较）
    dump = str(scenario["dump"])
    assert calls[0][0] == ["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error",
                           "--single-transaction", "-d", d + "_restore", dump]
    assert calls[1][0] == ["pg_restore", "--data-only", "--table=alembic_version", "-f", "-", dump]
    assert calls[0][1]["PGPASSWORD"] == db_password
    assert calls[1][1] == {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}  # 读版本号不需要连接
    assert db_password not in caplog.text and PASSWORD not in caplog.text

    # 连接失败：消息只有类别，不含密码、主机
    wrong = _url_for(pg_url, d).set(password="fictive-wrong-db-pass")
    monkeypatch.setenv("DATABASE_URL", wrong.render_as_string(hide_password=False))
    _clear_caches()
    with pytest.raises(restore.RestoreError) as e:
        restore.restore_db(scenario["dump"], target_db=d)
    assert "fictive-wrong-db-pass" not in str(e.value) and "无法连接数据库" in str(e.value)
    assert cli.main(["restore-db", "--drop-old"]) == 1
    err = capsys.readouterr().err
    assert "fictive-wrong-db-pass" not in err and wrong.host not in err and "无法连接数据库" in err


# ---------------------------------------------------------------- 原件


def _prepare_new(env, files: dict[str, bytes]) -> None:
    _new_dir(env).mkdir(mode=0o700)
    for name, data in files.items():
        (_new_dir(env) / name).write_bytes(data)


def test_swap_files(env):
    (env["files"] / KEY_C).write_bytes(b"created after backup")
    (env["files"] / (KEY_A + ".tmp")).write_bytes(b"half written")
    current_a = (env["files"] / KEY_A).read_bytes()
    _prepare_new(env, {KEY_A: b"backup A", KEY_B: b"backup B"})

    assert restore.swap_files() == {"moved_out": 3, "moved_in": 2}
    names = {p.name for p in env["files"].iterdir()}
    assert names == {KEY_A, KEY_B, "README", KEY_A + ".tmp", restore.OLD_DIR}
    assert (env["files"] / KEY_A).read_bytes() == b"backup A"
    old = env["files"] / restore.OLD_DIR
    assert oct(old.stat().st_mode & 0o777) == "0o700"
    assert {p.name for p in old.iterdir()} == {KEY_A, KEY_B, KEY_C}
    assert (old / KEY_A).read_bytes() == current_a

    # .restore-old 已存在时拒绝；没有 .restore-new 时拒绝
    _prepare_new(env, {KEY_C: b"x"})
    with pytest.raises(restore.RestoreError, match="旧原件目录"):
        restore.swap_files()
    assert restore.drop_old_files() is True and not old.exists()
    assert restore.drop_old_files() is False
    assert restore.drop_new_files() is True and restore.drop_new_files() is False
    with pytest.raises(restore.RestoreError, match="请先执行 restore-prepare"):
        restore.swap_files()
    assert not old.exists()


def test_swap_refuses_unexpected_files_in_new_dir(env):
    _prepare_new(env, {KEY_A: b"backup A", "evil": b"x"})
    with pytest.raises(restore.RestoreError, match="不应存在的文件"):
        restore.swap_files()
    assert not (env["files"] / restore.OLD_DIR).exists()
    assert (env["files"] / KEY_A).read_bytes() != b"backup A"


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


def test_cli_prepare_files_and_errors(env, db_sessionmaker, make_user, capsys):
    make_user("cli_owner")
    tar = _backup_tar(env, db_sessionmaker)
    (env["files"] / KEY_C).write_bytes(b"created after backup")
    assert cli.main(["restore-prepare", "--tar", str(tar), "--work", str(env["work"])]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["files_extracted"] == 2 and summary["key_checked"] is True

    assert cli.main(["restore-files"]) == 0
    assert "移出 3 个，恢复 2 个" in capsys.readouterr().out
    assert cli.main(["restore-files", "--verify"]) == 2
    assert cli.main(["restore-files", "--verify", "--expect-files", "2"]) == 0
    assert "核对通过" in capsys.readouterr().out
    assert cli.main(["restore-files", "--verify", "--expect-files", "5"]) == 1
    assert "核对不通过" in capsys.readouterr().err
    assert cli.main(["restore-files", "--drop-old"]) == 0
    assert not (env["files"] / restore.OLD_DIR).exists()

    # 失败时返回 1，只输出中文原因
    (env["work"] / "db.dump").unlink(missing_ok=True)
    junk = env["tmp"] / "junk.tar"
    junk.write_bytes(b"not a tar")
    assert cli.main(["restore-prepare", "--tar", str(junk), "--work", str(env["work"])]) == 1
    assert capsys.readouterr().err.startswith("错误：备份内容不正确")
    assert cli.main(["restore-files"]) == 1
    assert "请先执行 restore-prepare" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["restore-db"])  # --dump 与 --drop-old 必须二选一
