"""读取环境变量与密钥文件（SPEC 20.3）。

本模块只负责读取，不校验密钥内容：主密钥必须恰好 32 字节的检查在 M1-04 的 security/crypto.py。
"""
import logging
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        # 不在消息中带出取值，避免泄露
        raise RuntimeError(f"环境变量 {name} 必须是整数") from None


@dataclass(frozen=True)
class Settings:
    database_url: str
    master_key_file: str
    backup_password_file: str
    files_dir: str
    backup_dir: str
    ocr_model_dir: str
    worker_concurrency: int
    tz: str
    max_upload_mb: int
    app_version: str


def load_settings() -> Settings:
    """从环境变量构造配置。缺省值见 SPEC 20.3；连接串和密钥路径缺省为空，使用处负责检查。"""
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        master_key_file=os.environ.get("APP_MASTER_KEY_FILE", ""),
        backup_password_file=os.environ.get("BACKUP_PASSWORD_FILE", ""),
        files_dir=os.environ.get("FILES_DIR", "/data/files"),
        backup_dir=os.environ.get("BACKUP_DIR", "/backup"),
        ocr_model_dir=os.environ.get("OCR_MODEL_DIR", ""),
        worker_concurrency=_int_env("WORKER_CONCURRENCY", 2),
        tz=os.environ.get("TZ", "Asia/Shanghai"),
        max_upload_mb=_int_env("MAX_UPLOAD_MB", 30),
        app_version=os.environ.get("APP_VERSION", "dev"),
    )


@lru_cache
def get_settings() -> Settings:
    return load_settings()


def read_secret_file(path: str) -> bytes:
    """读取密钥文件的原始字节。路径为空或文件不存在时抛出不含内容的错误。"""
    if not path:
        raise RuntimeError("未配置密钥文件路径")
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"密钥文件不存在：{path}")
    return p.read_bytes()


def setup_logging() -> None:
    """日志只输出到标准输出（SPEC 17.7）。

    只记录事件和标识，不记录请求体、原文、金额、密钥。
    """
    root = logging.getLogger()
    if any(getattr(h, "_aireview", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler._aireview = True  # type: ignore[attr-defined]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)
