"""备份流程（SPEC 16.1–16.3，设计 docs/design/M7.md 3.2、3.3）。

run_backup()：advisory lock → 记录 running → pg_dump → tar（manifest、db.dump、files/）→ 分块流式加密写 .tmp → fsync
→ 写 .sha256 → 重命名为 .bak → 轮换 → 记录 success / failed 并写审计。
- 同一时间只允许一个备份：会话级 advisory lock，取不到时直接返回 None，不写记录。
- 数据库连接参数用环境变量传给 pg_dump，不出现在命令行参数中；pg_dump 的输出（stderr）不记录。
- 失败时删除 .tmp 和本次的 .sha256，记录 failed（中文原因，不含内容和密码），job 正常结束、不重试（Q-101）。
- 只删除符合 aireview_yyyyMMdd_HHmm.bak 命名规则的文件。
- 日志只写记录 id、文件名、大小、耗时、错误类名。
"""
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.audit import service as audit
from app.backup.archive import EncryptingWriter, load_backup_password
from app.config import get_settings
from app.db.base import utcnow
from app.db.jobs import BACKUP_INTERRUPTED_MSG
from app.db.models import BackupRecord, Task

logger = logging.getLogger("app.backup")

LOCK_KEY = 0x41495256_0001  # pg advisory lock 的固定键（"AIRV" + 1）
BAK_RE = re.compile(r"^aireview_\d{8}_\d{4}\.bak$")
TMP_RE = re.compile(r"^aireview_\d{8}_\d{4}\.bak\.tmp$")
_FILE_KEY_RE = re.compile(r"^[0-9a-f]{32}$")  # /data/files 中的原件文件名（tasks/storage.py）
WORK_DIR = ".work"
FORMAT_VERSION = 1
# .tmp / .bak / .sha256 的权限（Q-120）：组 aireview（gid 10001）可读，供运维账号 SFTP 拉取；备份本身已用备份密码加密
BACKUP_FILE_MODE = 0o640


class BackupError(Exception):
    """可预期的失败；消息是给管理员看的中文原因，不含内容和密码。"""


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().tz or "Asia/Shanghai")


def backup_name(now: datetime) -> str:
    return f"aireview_{now.astimezone(local_tz()):%Y%m%d_%H%M}.bak"


class _HashingFile:
    """写入底层文件的同时计算 SHA-256 和字节数（对加密后的内容）。"""

    def __init__(self, f):
        self.f = f
        self.sha = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.f.write(data)
        self.sha.update(data)
        self.size += len(data)
        return len(data)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _pg_env(db: Session) -> dict[str, str]:
    url = db.get_bind().url
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PGCONNECT_TIMEOUT": "10"}
    for key, value in (("PGHOST", url.host), ("PGPORT", url.port), ("PGUSER", url.username),
                       ("PGPASSWORD", url.password), ("PGDATABASE", url.database)):
        if value is not None:
            env[key] = str(value)
    return env


def _pg_dump(db: Session, out: Path) -> None:
    try:
        rc = subprocess.run(
            ["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-f", str(out)],
            env=_pg_env(db), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
    except FileNotFoundError:
        raise BackupError("数据库导出失败（未找到 pg_dump）") from None
    if rc != 0:
        raise BackupError(f"数据库导出失败（返回码 {rc}）")


def _original_files(files_dir: Path) -> list[Path]:
    if not files_dir.is_dir():
        return []
    with os.scandir(files_dir) as it:
        return sorted(Path(e.path) for e in it if _FILE_KEY_RE.match(e.name) and e.is_file(follow_symlinks=False))


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mtime: float) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(mtime)
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def _write_archive(tmp: Path, password: bytes, manifest: dict, dump: Path, files: list[Path]) -> tuple[int, str, int]:
    """写加密的 .tmp 文件并 fsync，返回（大小, SHA-256, 实际放入的原件数）。"""
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, BACKUP_FILE_MODE)
    os.fchmod(fd, BACKUP_FILE_MODE)  # 不受 umask 影响
    with os.fdopen(fd, "wb") as f:
        hashing = _HashingFile(f)
        writer = EncryptingWriter(hashing, password)
        added = 0
        with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            # manifest 写在最前（设计 3.2），file_count 为列出的原件数；列出后才被删除的原件（任务刚被删除）跳过，
            # 这时 file_count 会比实际多，恢复不依赖这个数字
            manifest = {**manifest, "file_count": len(files)}
            _add_bytes(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"), time.time())
            with open(dump, "rb") as fh:
                tar.addfile(tar.gettarinfo(arcname="db.dump", fileobj=fh), fh)
            for p in files:
                try:
                    fh = open(p, "rb")
                except FileNotFoundError:
                    continue
                with fh:
                    info = tar.gettarinfo(arcname=f"files/{p.name}", fileobj=fh)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    tar.addfile(info, fh)
                added += 1
        writer.close()
        f.flush()
        os.fsync(f.fileno())
    return hashing.size, hashing.sha.hexdigest(), added


def rotate(backup_dir: Path, keep: int) -> list[str]:
    """删除超过保留份数的最旧备份（.bak 与同名 .sha256），只看符合命名规则的文件。返回删除的 .bak 文件名。"""
    names = sorted(p.name for p in backup_dir.iterdir() if BAK_RE.match(p.name) and p.is_file())
    removed = names[:-keep] if keep > 0 and len(names) > keep else []
    for name in removed:
        (backup_dir / name).unlink(missing_ok=True)
        (backup_dir / f"{name}.sha256").unlink(missing_ok=True)
    return removed


def _keep(db: Session) -> int:
    try:
        return min(60, max(1, int(get_setting(db, "backup.keep"))))
    except (TypeError, ValueError):
        return 7


def _measure_stat(func):
    """统计失败不能把有效备份置失败；每项独立测量且不重试。"""
    try:
        return int(func())
    except Exception as exc:  # noqa: BLE001
        logger.warning("备份统计失败 错误=%s", type(exc).__name__)
        return None


def _database_bytes(db: Session) -> int:
    # 数据库测量出错只回滚保存点，后续备份仍能提交成功记录。
    with db.begin_nested():
        return db.scalar(text("SELECT pg_database_size(current_database())"))


def _directory_bytes(directory: Path) -> int:
    with os.scandir(directory) as entries:
        return sum(entry.stat(follow_symlinks=False).st_size for entry in entries
                   if entry.is_file(follow_symlinks=False))


def _finish(db: Session, record_id: int, *, trigger: str, user_id: uuid.UUID | None, **fields) -> BackupRecord:
    record = db.get(BackupRecord, record_id)
    for k, v in fields.items():
        setattr(record, k, v)
    record.finished_at = utcnow()
    audit.record(db, "backup_run", user_id=user_id, target_type="backup", target_id=str(record_id),
                 detail={"trigger": trigger, "status": record.status})
    db.commit()
    return record


def _locked_backup(db: Session, *, trigger: str, user_id: uuid.UUID | None, now: datetime) -> BackupRecord:
    settings = get_settings()
    # 只有拿到锁的人在跑备份，其余 running 记录都是进程崩溃的遗留（Q-101）
    db.execute(update(BackupRecord).where(BackupRecord.status == "running")
               .values(status="failed", error_message=BACKUP_INTERRUPTED_MSG, finished_at=utcnow()))
    record = BackupRecord(trigger=trigger, status="running", started_at=utcnow())
    db.add(record)
    db.commit()
    record_id = record.id
    started = time.monotonic()

    backup_dir = Path(settings.backup_dir)
    name = backup_name(now)
    tmp = backup_dir / f"{name}.tmp"
    sha_path = backup_dir / f"{name}.sha256"
    work = backup_dir / WORK_DIR
    created: list[Path] = []
    try:
        if not backup_dir.is_dir() or not os.access(backup_dir, os.W_OK | os.X_OK):
            raise BackupError("备份目录不存在或不可写")
        try:
            password = load_backup_password(settings.backup_password_file)
        except RuntimeError as exc:
            raise BackupError(str(exc) if str(exc).startswith("备份密码文件无效") else "备份密码文件无效：无法读取") from None
        if (backup_dir / name).exists():
            raise BackupError("同一分钟内已有备份，请稍后再试")
        for p in backup_dir.iterdir():
            if TMP_RE.match(p.name):
                p.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(mode=0o700)
        dump = work / "db.dump"
        _pg_dump(db, dump)
        db_bytes = _measure_stat(lambda: _database_bytes(db))

        manifest = {"format": FORMAT_VERSION, "app_version": settings.app_version,
                    "created_at": utcnow().isoformat(), "task_count": db.scalar(select(func.count()).select_from(Task)),
                    "db_dump": "db.dump"}
        db.commit()  # 结束只读事务，避免写备份期间长时间占着事务
        files = _original_files(Path(settings.files_dir))
        created.append(tmp)
        size, digest, added = _write_archive(tmp, password, manifest, dump, files)

        created.append(sha_path)
        fd = os.open(sha_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, BACKUP_FILE_MODE)
        os.fchmod(fd, BACKUP_FILE_MODE)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(f"{digest}  {name}\n")  # 与 sha256sum -c 兼容
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, backup_dir / name)
        created.clear()  # 已是完整的备份，之后的失败不再删除它
        _fsync_dir(backup_dir)
        try:
            removed = rotate(backup_dir, _keep(db))
        except Exception as exc:  # noqa: BLE001  备份本身有效，但旧备份没删掉，记为失败让管理员看到
            logger.error("备份 %s 轮换失败 错误=%s", record_id, type(exc).__name__)
            return _finish(db, record_id, trigger=trigger, user_id=user_id, status="failed", file_name=name,
                           size_bytes=size, sha256=digest,
                           error_message=f"备份文件已生成，但删除旧备份失败（{type(exc).__name__}）")
    except Exception as exc:  # noqa: BLE001  失败一律记录，不让 job 重试
        for p in created:
            p.unlink(missing_ok=True)
        message = str(exc) if isinstance(exc, BackupError) else f"备份失败（{type(exc).__name__}）"
        db.rollback()
        logger.error("备份 %s 失败 trigger=%s 错误=%s", record_id, trigger, type(exc).__name__)
        return _finish(db, record_id, trigger=trigger, user_id=user_id, status="failed", error_message=message)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    ms = int((time.monotonic() - started) * 1000)
    logger.info("备份 %s 完成 %s 大小=%s 原件=%s 轮换删除=%s 耗时=%sms", record_id, name, size, added, len(removed), ms)
    return _finish(db, record_id, trigger=trigger, user_id=user_id, status="success", file_name=name,
                   size_bytes=size, sha256=digest, error_message=None, db_bytes=db_bytes,
                   dir_bytes=_measure_stat(lambda: _directory_bytes(backup_dir)),
                   dir_free_bytes=_measure_stat(lambda: shutil.disk_usage(backup_dir).free))


def run_backup(db: Session, *, trigger: str, user_id: uuid.UUID | None = None,
               now: datetime | None = None) -> BackupRecord | None:
    """执行一次备份。另一个备份正在运行（拿不到 advisory lock）时返回 None。"""
    if trigger not in ("schedule", "manual"):
        raise ValueError("trigger")
    with db.get_bind().connect() as lock_conn:
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        lock_conn.commit()
        if not got:
            logger.info("已有备份正在运行，本次跳过 trigger=%s", trigger)
            return None
        try:
            return _locked_backup(db, trigger=trigger, user_id=user_id, now=now or utcnow())
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            lock_conn.commit()
