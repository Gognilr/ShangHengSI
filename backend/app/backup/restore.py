"""恢复（SPEC 16.3，设计 docs/design/M8.md 3.3–3.5，open_questions Q-117～Q-119）。

由 deploy/scripts/restore.sh 经 `python -m app.cli` 分步调用：
  prepare()        停服前：校验解密后的 tar（成员、清单、迁移版本、主密钥），通过后解出 db.dump 和原件。
                   任一项不通过抛 RestoreError，不留下任何解出的文件，系统不受影响。
  restore_db()     恢复到新库 {库名}_restore，清理会话与进行中的任务后，把原库改名为 {库名}_pre_restore、
                   新库改为原名；pg_restore 失败时原库不动。drop_old() 删除旧库。
  swap_files()     原件交换：现有原件移入 .restore-old/，解出的原件移入 FILES_DIR（同一文件系统内只做重命名）。
  verify()         核对原件数与 task_files 记录；drop_old_files() 删除旧原件目录。
数据库连接参数用环境变量传给 pg_restore，不出现在命令行参数中；pg_restore 的输出（stderr）不记录。
异常消息与日志只写类别和数量，不含文件内容、名称、密码。
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.config import get_settings
from app.db.base import utcnow
from app.db.jobs import BACKUP_INTERRUPTED_MSG
from app.db.models import BackupRecord, Job, TaskFile
from app.db.models import Session as UserSession
from app.db.session import normalize_url
from app.security import crypto

logger = logging.getLogger("app.backup")

FORMAT_VERSION = 1
NEW_DIR = ".restore-new"
OLD_DIR = ".restore-old"
RESTORED_JOB_ERROR = "RESTORED"
_FILE_MEMBER_RE = re.compile(r"files/([0-9a-f]{32})")
_FILE_KEY_RE = re.compile(r"[0-9a-f]{32}")
_DB_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,40}")
_MANIFEST_MAX = 1024 * 1024
_COPY_BUF = 1024 * 1024
_ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "db" / "alembic"


class RestoreError(Exception):
    """可预期的失败；消息是给运维人员看的中文原因，不含文件内容和密码。"""


# ---------------------------------------------------------------- 公共


def _files_dir() -> Path:
    return Path(get_settings().files_dir)


def _is_key(name: str) -> bool:
    return _FILE_KEY_RE.fullmatch(name) is not None


def _original_names(directory: Path) -> list[str]:
    """目录下文件名为 32 位小写十六进制的普通文件（不跟随链接）。"""
    if not directory.is_dir():
        return []
    with os.scandir(directory) as it:
        return sorted(e.name for e in it if _is_key(e.name) and e.is_file(follow_symlinks=False))


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _db_params() -> dict:
    """DATABASE_URL 的连接参数。"""
    raw = get_settings().database_url
    if not raw:
        raise RestoreError("未配置 DATABASE_URL")
    url = make_url(normalize_url(raw))
    return {"host": url.host, "port": url.port, "user": url.username, "password": url.password,
            "dbname": url.database}


def _pg_env(params: dict, dbname: str) -> dict[str, str]:
    """pg_restore 的连接参数全部经环境变量传入（与 service._pg_env 相同的做法）。"""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PGCONNECT_TIMEOUT": "10"}
    for key, value in (("PGHOST", params["host"]), ("PGPORT", params["port"]), ("PGUSER", params["user"]),
                       ("PGPASSWORD", params["password"]), ("PGDATABASE", dbname)):
        if value is not None:
            env[key] = str(value)
    return env


def _connect(params: dict, dbname: str) -> psycopg.Connection:
    kwargs = {k: params[k] for k in ("host", "port", "user", "password") if params[k] is not None}
    try:
        return psycopg.connect(dbname=dbname, autocommit=True, connect_timeout=10, **kwargs)
    except psycopg.Error as exc:
        # psycopg 的消息可能带主机、用户名，只报类别
        raise RestoreError(f"无法连接数据库（{type(exc).__name__}）") from None


def _db_names(name: str) -> tuple[str, str, str]:
    if not _DB_NAME_RE.fullmatch(name or ""):
        raise RestoreError("数据库名不符合要求（只允许字母、数字、下划线，最多 40 个字符）")
    return name, f"{name}_restore", f"{name}_pre_restore"


def _db_exists(conn: psycopg.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is not None


def _old_db_exists_msg(old: str) -> str:
    return f"上次恢复留下的旧数据库 {old} 尚未处理，请先按运维说明确认后删除，再恢复"


def known_revisions() -> set[str]:
    """当前代码 Alembic 脚本目录中的全部迁移版本。"""
    from alembic.script import ScriptDirectory

    return {s.revision for s in ScriptDirectory(str(_ALEMBIC_DIR)).walk_revisions()}


# ---------------------------------------------------------------- prepare


def _check_members(tar: tarfile.TarFile) -> tuple[tarfile.TarInfo, tarfile.TarInfo, list[tarfile.TarInfo]]:
    """成员只允许 manifest.json（第一个）、db.dump、files/<32 位小写十六进制>，且都是普通文件、不重复。"""
    members = tar.getmembers()
    seen: set[str] = set()
    files: list[tarfile.TarInfo] = []
    dump = None
    for i, m in enumerate(members):
        # 普通文件只认 REGTYPE / AREGTYPE；链接、目录、设备、稀疏文件一律拒绝
        if m.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
            raise RestoreError("备份内容不正确：含有非普通文件（链接、目录或设备文件）")
        name = m.name
        if name in seen:
            raise RestoreError("备份内容不正确：成员重复")
        seen.add(name)
        if name == "manifest.json":
            if i != 0:
                raise RestoreError("备份内容不正确：备份清单不在首位")
        elif name == "db.dump":
            dump = m
        elif _FILE_MEMBER_RE.fullmatch(name):
            files.append(m)
        else:
            raise RestoreError("备份内容不正确：含有不允许的成员名")
    if not members or members[0].name != "manifest.json":
        raise RestoreError("备份内容不正确：备份清单不在首位")
    if dump is None:
        raise RestoreError("备份内容不正确：缺少数据库导出文件")
    return members[0], dump, files


def _read_manifest(tar: tarfile.TarFile, member: tarfile.TarInfo, actual_files: int) -> dict:
    if member.size > _MANIFEST_MAX:
        raise RestoreError("备份清单不正确")
    try:
        manifest = json.loads(tar.extractfile(member).read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RestoreError("备份清单不正确") from None
    if not isinstance(manifest, dict):
        raise RestoreError("备份清单不正确")
    fmt = manifest.get("format")
    if type(fmt) is not int or fmt != FORMAT_VERSION:
        raise RestoreError("备份格式版本不支持")
    if manifest.get("db_dump") != "db.dump":
        raise RestoreError("备份清单不正确")
    file_count = manifest.get("file_count")
    if type(file_count) is not int or file_count < 0:
        raise RestoreError("备份清单不正确")
    # Q-114 ①：备份时列出后才被删除的原件会跳过，实际数可以比 file_count 少，但不能多
    if actual_files > file_count:
        raise RestoreError("备份内容不正确：原件数多于备份清单记录的数量")
    return manifest


def _copy_member(tar: tarfile.TarFile, member: tarfile.TarInfo, dest: Path) -> None:
    """把成员写到新文件（O_EXCL，600）并 fsync。"""
    src = tar.extractfile(member)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        shutil.copyfileobj(src, f, _COPY_BUF)
        f.flush()
        os.fsync(f.fileno())


def dump_revision(dump_path: Path) -> str:
    """从 db.dump 中取出 alembic_version 的版本号（pg_restore 只输出这一张表的数据，不连接数据库）。"""
    try:
        r = subprocess.run(
            ["pg_restore", "--data-only", "--table=alembic_version", "-f", "-", str(dump_path)],
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
    except FileNotFoundError:
        raise RestoreError("未找到 pg_restore") from None
    if r.returncode != 0:
        raise RestoreError(f"无法读取数据库导出文件（返回码 {r.returncode}）")
    rows: list[str] = []
    in_copy = False
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        if in_copy:
            if line == "\\.":
                break
            rows.append(line.strip())
        elif line.startswith("COPY ") and "alembic_version" in line:
            in_copy = True
    if len(rows) != 1 or not rows[0]:
        raise RestoreError("备份中的数据库版本信息不正确")
    return rows[0]


def _check_old_leftovers(files_dir: Path) -> None:
    """上次恢复留下的旧库、旧原件目录没处理时，停服之前就拒绝（swap_files、restore_db 也会再查一次）。"""
    if (files_dir / OLD_DIR).exists():
        raise RestoreError(f"上次恢复留下的旧原件目录 {OLD_DIR} 尚未处理，请先按运维说明确认后删除，再恢复")
    params = _db_params()
    _, _, old = _db_names(params["dbname"])
    with _connect(params, "postgres") as conn:
        if _db_exists(conn, old):
            raise RestoreError(_old_db_exists_msg(old))


def prepare(tar_path: str | Path, work_dir: str | Path) -> dict:
    """校验解密后的备份 tar，通过后把 db.dump 解到 work_dir、原件解到 FILES_DIR/.restore-new/，返回摘要。"""
    tar_path, work_dir = Path(tar_path), Path(work_dir)
    files_dir = _files_dir()
    new_dir = files_dir / NEW_DIR
    dump_dest = work_dir / "db.dump"
    if not work_dir.is_dir():
        raise RestoreError("工作目录不存在")
    if not files_dir.is_dir():
        raise RestoreError("原件目录不存在")
    _check_old_leftovers(files_dir)
    # 上次中断留下的残留
    shutil.rmtree(new_dir, ignore_errors=True)
    dump_dest.unlink(missing_ok=True)
    try:
        try:
            tar = tarfile.open(tar_path, mode="r:")
        except (OSError, tarfile.TarError):
            raise RestoreError("备份内容不正确：不是有效的 tar 文件") from None
        with tar:
            try:
                manifest_m, dump_m, file_ms = _check_members(tar)
            except tarfile.TarError:
                raise RestoreError("备份内容不正确：tar 文件已损坏") from None
            manifest = _read_manifest(tar, manifest_m, len(file_ms))

            _copy_member(tar, dump_m, dump_dest)
            revision = dump_revision(dump_dest)
            if revision not in known_revisions():
                raise RestoreError("备份来自更新的系统版本，请先升级到不低于备份时的版本再恢复")

            key_checked = False
            if file_ms:
                blob = tar.extractfile(file_ms[0]).read()
                try:
                    crypto.decrypt(blob)
                except crypto.DecryptionError:
                    raise RestoreError("当前主密钥与备份不匹配，恢复需要备份时使用的主密钥") from None
                key_checked = True

            new_dir.mkdir(mode=0o700)
            for m in file_ms:
                _copy_member(tar, m, new_dir / m.name.split("/", 1)[1])
            _fsync_dir(new_dir)
    except BaseException:
        shutil.rmtree(new_dir, ignore_errors=True)
        dump_dest.unlink(missing_ok=True)
        raise
    summary = {
        "created_at": manifest.get("created_at"),
        "app_version": manifest.get("app_version"),
        "task_count": manifest.get("task_count"),
        "file_count": manifest["file_count"],
        "files_extracted": len(file_ms),
        "revision": revision,
        "key_checked": key_checked,
    }
    logger.info("恢复准备完成 版本=%s 原件=%s", revision, len(file_ms))
    return summary


# ---------------------------------------------------------------- 数据库


def _pg_restore(params: dict, dbname: str, dump_path: Path) -> int:
    try:
        return subprocess.run(
            ["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction",
             "-d", dbname, str(dump_path)],
            env=_pg_env(params, dbname), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        ).returncode
    except FileNotFoundError:
        raise RestoreError("未找到 pg_restore") from None


def _after_restore(params: dict, dbname: str, files: int) -> str:
    """在恢复出的库中清理（Q-118）：删除全部会话，进行中的 job 与备份记录置为失败，写审计。返回迁移版本。"""
    url = make_url(normalize_url(get_settings().database_url)).set(database=dbname)
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            revision = db.scalar(text("SELECT version_num FROM alembic_version"))
            now = utcnow()
            db.execute(delete(UserSession))
            db.execute(update(Job).where(Job.status == "running")
                       .values(status="failed", last_error=RESTORED_JOB_ERROR, finished_at=now))
            db.execute(update(BackupRecord).where(BackupRecord.status == "running")
                       .values(status="failed", error_message=BACKUP_INTERRUPTED_MSG, finished_at=now))
            audit.record(db, "restore_run", detail={"revision": revision, "files": files})
            db.commit()
    finally:
        engine.dispose()
    return revision


def _drop(conn: psycopg.Connection, name: str) -> None:
    conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _rename(conn: psycopg.Connection, src: str, dst: str) -> None:
    conn.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(src), sql.Identifier(dst)))


def _terminate(conn: psycopg.Connection, name: str) -> None:
    conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                 (name,))


def restore_db(dump_path: str | Path, *, target_db: str | None = None) -> dict:
    """把 db.dump 恢复为当前数据库（Q-117）：要么完全换成备份的数据，要么当前库原样不动。

    target_db 只供测试指定库名；默认取 DATABASE_URL 中的库名。连接维护库 postgres 执行建库、改名。
    """
    dump_path = Path(dump_path)
    if not dump_path.is_file():
        raise RestoreError("数据库导出文件不存在")
    params = _db_params()
    current, new, old = _db_names(target_db or params["dbname"])
    files = len(_original_names(_files_dir() / NEW_DIR))
    started = time.monotonic()

    with _connect(params, "postgres") as conn:
        if _db_exists(conn, old):
            raise RestoreError(_old_db_exists_msg(old))
        if not _db_exists(conn, current):
            raise RestoreError("当前数据库不存在")
        _drop(conn, new)  # 上次中断留下的残留
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(new)))

    def _discard_new() -> None:
        with _connect(params, "postgres") as c:
            _drop(c, new)

    rc = _pg_restore(params, new, dump_path)
    if rc != 0:
        _discard_new()
        raise RestoreError(f"数据库恢复失败（返回码 {rc}）")
    try:
        revision = _after_restore(params, new, files)
    except Exception as exc:  # noqa: BLE001
        _discard_new()
        raise RestoreError(f"数据库恢复后的清理失败（{type(exc).__name__}）") from None

    with _connect(params, "postgres") as conn:
        # app、worker 已停止；仍有连接（如管理工具）时终止后重试几次
        for attempt in range(5):
            _terminate(conn, current)
            try:
                _rename(conn, current, old)
                break
            except psycopg.errors.ObjectInUse:
                if attempt == 4:
                    _drop(conn, new)
                    raise RestoreError("当前数据库仍有连接，无法替换；当前数据库未改动") from None
                time.sleep(1)
            except psycopg.Error as exc:
                _drop(conn, new)
                raise RestoreError(f"当前数据库改名失败（{type(exc).__name__}）；当前数据库未改动") from None
        try:
            _rename(conn, new, current)
        except psycopg.Error as exc:
            try:
                _rename(conn, old, current)
                state = "已改回原库"
            except psycopg.Error:
                state = f"原库现名为 {old}，恢复出的库为 {new}"
            raise RestoreError(f"新库改名失败（{type(exc).__name__}）；{state}") from None
    ms = int((time.monotonic() - started) * 1000)
    logger.info("数据库恢复完成 版本=%s 原件=%s 耗时=%sms", revision, files, ms)
    return {"database": current, "old_database": old, "revision": revision, "files": files}


def drop_old(*, target_db: str | None = None) -> str:
    """删除恢复前的旧库 {库名}_pre_restore。返回库名。"""
    params = _db_params()
    _, _, old = _db_names(target_db or params["dbname"])
    with _connect(params, "postgres") as conn:
        _drop(conn, old)
    return old


# ---------------------------------------------------------------- 原件


def swap_files() -> dict:
    """现有原件移入 .restore-old/，.restore-new/ 中的原件移入 FILES_DIR；不碰其他命名的文件。"""
    files_dir = _files_dir()
    new_dir, old_dir = files_dir / NEW_DIR, files_dir / OLD_DIR
    if old_dir.exists():
        raise RestoreError(f"上次恢复留下的旧原件目录 {OLD_DIR} 尚未处理，请先按运维说明确认后删除，再恢复")
    if not new_dir.is_dir():
        raise RestoreError(f"没有待恢复的原件（{NEW_DIR} 不存在），请先执行 restore-prepare")
    extra = [n for n in os.listdir(new_dir) if not _is_key(n) or not (new_dir / n).is_file()]
    if extra:
        raise RestoreError(f"{NEW_DIR} 中有不应存在的文件，已停止")
    old_dir.mkdir(mode=0o700)
    moved_out = 0
    for name in _original_names(files_dir):
        os.replace(files_dir / name, old_dir / name)
        moved_out += 1
    _fsync_dir(old_dir)
    moved_in = 0
    for name in _original_names(new_dir):
        os.replace(new_dir / name, files_dir / name)
        moved_in += 1
    new_dir.rmdir()
    _fsync_dir(files_dir)
    logger.info("原件交换完成 移出=%s 移入=%s", moved_out, moved_in)
    return {"moved_out": moved_out, "moved_in": moved_in}


def verify(expect_files: int) -> dict:
    """核对：FILES_DIR 下的原件数等于 expect_files；未删除原件的 task_files 记录，其文件都存在。"""
    from app.db.session import get_sessionmaker

    files_dir = _files_dir()
    present = set(_original_names(files_dir))
    with get_sessionmaker()() as db:
        keys = db.scalars(select(TaskFile.storage_key).where(TaskFile.original_deleted_at.is_(None))).all()
    missing = sum(1 for k in keys if k not in present)
    result = {"files": len(present), "expected": expect_files, "records": len(keys), "missing": missing,
              "ok": len(present) == expect_files and missing == 0}
    return result


def drop_old_files() -> bool:
    """删除 .restore-old/。目录不存在时返回 False。"""
    old_dir = _files_dir() / OLD_DIR
    if not old_dir.exists():
        return False
    shutil.rmtree(old_dir)
    return True


def drop_new_files() -> bool:
    """删除 .restore-new/（恢复在交换原件之前中止时清理，Q-133）。目录不存在时返回 False。"""
    new_dir = _files_dir() / NEW_DIR
    if not new_dir.exists():
        return False
    shutil.rmtree(new_dir)
    return True
