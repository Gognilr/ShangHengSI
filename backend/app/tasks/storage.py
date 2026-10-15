"""原件加密存储（SPEC 17.3、17.6）。

文件以随机 UUID（十六进制）命名，存放在 FILES_DIR 下，内容用主密钥 AES-256-GCM 加密；
原文件名只存数据库，不出现在磁盘路径里。
"""
import hashlib
import os
import re
import uuid
from pathlib import Path

from app.config import get_settings
from app.security import crypto

_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


def _path(storage_key: str) -> Path:
    # storage_key 只可能是 uuid4 十六进制；拒绝其他形式，避免路径穿越
    if not _KEY_RE.match(storage_key):
        raise ValueError("storage_key 格式不正确")
    return Path(get_settings().files_dir) / storage_key


def save_original(data: bytes) -> tuple[str, str]:
    """加密保存原件，返回（storage_key, 明文 SHA-256）。"""
    key = uuid.uuid4().hex
    directory = Path(get_settings().files_dir)
    os.makedirs(directory, exist_ok=True)
    crypto.encrypt_to_file(_path(key), data)
    return key, hashlib.sha256(data).hexdigest()


def load_original(storage_key: str) -> bytes:
    return crypto.decrypt_file(_path(storage_key))


def delete_original(storage_key: str) -> None:
    """删除原件文件；文件不存在不报错。"""
    _path(storage_key).unlink(missing_ok=True)
