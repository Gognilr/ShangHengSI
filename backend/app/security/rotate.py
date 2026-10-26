"""停服主密钥轮换（V2-C-07）。显式使用两把密钥，不改变进程内主密钥缓存。"""
import base64
import binascii
import hashlib
import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import and_, select, tuple_, update

from app.db.base import Base
from app.db import models  # noqa: F401  注册全部元数据
from app.security import crypto


class RotationError(Exception):
    """只含表/主键或原件的不可逆位置标识，绝不携带底层异常。"""


def encrypted_columns():
    return [column for table in Base.metadata.sorted_tables for column in table.c if column.name.endswith("_enc")]


def _after_batch():
    """批次已持久化后的测试中断点；正常执行无操作。"""


def _rotate_blob(blob, old_key, new_key, location):
    try:
        crypto.decrypt(blob, key=new_key)
        return None
    except crypto.DecryptionError:
        pass
    try:
        return crypto.encrypt(crypto.decrypt(blob, key=old_key), key=new_key)
    except crypto.DecryptionError:
        raise RotationError(f"无法解密：{location}") from None


def _tables():
    groups = {}
    for col in encrypted_columns():
        groups.setdefault(col.table, []).append(col)
    return groups.items()


def _rotate_database(engine, old_key, new_key, batch_size):
    for table, columns in _tables():
        keys = list(table.primary_key)
        last = None
        while True:
            with engine.begin() as conn:
                query = select(*keys, *columns).order_by(*keys).limit(batch_size)
                if last is not None:
                    query = query.where(tuple_(*keys) > tuple_(*last))
                rows = conn.execute(query).mappings().all()
                if not rows:
                    break
                for row in rows:
                    values = {}
                    pk = [row[key.name] for key in keys]
                    location = f"{table.name} 主键 {','.join(str(value) for value in pk)}"
                    for column in columns:
                        blob = row[column.name]
                        if blob is not None:
                            changed = _rotate_blob(blob, old_key, new_key, location)
                            if changed is not None:
                                values[column.name] = changed
                    if values:
                        # 仅更换密文，不改变业务修改时间等 Python onupdate 字段。
                        values.update({col.name: col for col in table.c if col.onupdate is not None and col.name not in values})
                        conn.execute(update(table).where(and_(*(key == value for key, value in zip(keys, pk)))).values(**values))
                last = [rows[-1][key.name] for key in keys]
            _after_batch()
    table = models.Setting.__table__
    with engine.begin() as conn:
        value = conn.execute(select(table.c.value).where(table.c.key == "model.api_key_enc")).scalar_one_or_none()
        if value is not None:
            try:
                blob = base64.b64decode(value, validate=True)
            except (ValueError, TypeError, binascii.Error):
                raise RotationError("无法解密：settings 主键 model.api_key_enc") from None
            changed = _rotate_blob(blob, old_key, new_key, "settings 主键 model.api_key_enc")
            if changed is not None:
                conn.execute(update(table).where(table.c.key == "model.api_key_enc").values(
                    value=base64.b64encode(changed).decode("ascii"), updated_at=table.c.updated_at))
    _after_batch()


def _originals(directory):
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise RotationError("原件目录不可用：files")
    for path in sorted(directory.iterdir()):
        # 强制终止可能遗留密文临时文件；从不把它们当成业务原件。
        if path.name.startswith(".rotate-") and path.name.endswith(".tmp"):
            continue
        location = "files:" + hashlib.sha256(path.name.encode()).hexdigest()[:16]
        if path.is_symlink() or not path.is_file():
            raise RotationError(f"原件不是普通文件：{location}")
        yield path, location


def _replace_original(path, blob):
    temp = None
    try:
        info = path.stat()
        fd, name = tempfile.mkstemp(prefix=".rotate-", suffix=".tmp", dir=path.parent)
        temp = Path(name)
        with os.fdopen(fd, "wb") as handle:
            if hasattr(os, "fchown"):
                os.fchown(handle.fileno(), info.st_uid, info.st_gid)
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        # 改名也持久化，断电后仍是完整的新文件或旧文件。
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def rotate_key(engine, files_dir, old_key, new_key, *, batch_size=500):
    if len(old_key) != crypto.KEY_SIZE or len(new_key) != crypto.KEY_SIZE or old_key == new_key:
        raise RotationError("新旧主密钥须为不同的 32 字节密钥")
    if not 1 <= batch_size <= 500:
        raise RotationError("数据库批次须为 1–500 行")
    _rotate_database(engine, old_key, new_key, batch_size)
    for path, location in _originals(files_dir):
        try:
            changed = _rotate_blob(path.read_bytes(), old_key, new_key, location)
            if changed is not None:
                _replace_original(path, changed)
        except OSError:
            raise RotationError(f"原件读写失败：{location}") from None


def verify_key(engine, files_dir, key):
    for table, columns in _tables():
        with engine.connect() as conn:
            for col in columns:
                rows = conn.execute(select(*table.primary_key, col).where(col.is_not(None)).order_by(*table.primary_key).limit(20)).mappings()
                for row in rows:
                    try:
                        crypto.decrypt(row[col.name], key=key)
                    except crypto.DecryptionError:
                        pk = ','.join(str(row[c.name]) for c in table.primary_key)
                        raise RotationError(f"抽查失败：{table.name} 主键 {pk}") from None
    with engine.connect() as conn:
        value = conn.execute(select(models.Setting.value).where(models.Setting.key == "model.api_key_enc")).scalar_one_or_none()
        if value is not None:
            try:
                crypto.decrypt_from_b64(value, key=key)
            except (crypto.DecryptionError, ValueError, TypeError):
                raise RotationError("抽查失败：settings 主键 model.api_key_enc") from None
    for number, (path, location) in enumerate(_originals(files_dir)):
        if number >= 20:
            break
        try:
            crypto.decrypt_file(path, key=key)
        except (crypto.DecryptionError, OSError):
            raise RotationError(f"原件抽查失败：{location}") from None


def command(args):
    """CLI 边界只输出受控位置或异常类型，数据库异常可能含参数，不能直接打印。"""
    from app.config import get_settings
    from app.db.session import get_sessionmaker
    try:
        with get_sessionmaker()() as db:
            engine = db.get_bind()
        if args.command == "rotate-key":
            rotate_key(engine, get_settings().files_dir, crypto.load_master_key(args.old_key_file),
                       crypto.load_master_key(args.new_key_file))
            print("主密钥数据轮换已完成，请继续验证并按脚本切换密钥文件。")
        else:
            verify_key(engine, get_settings().files_dir, crypto.load_master_key(args.key_file))
            print("新主密钥抽查通过。")
        return 0
    except RotationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
    except Exception as exc:  # 异常消息可能含密文/连接串，只报告类型。
        print(f"错误：密钥操作失败（{type(exc).__name__}），请检查配置与数据库状态。", file=sys.stderr)
    return 1
