"""系统设置的读写（SPEC 15、18.20）。

settings 表一个键一行，值为 JSON；表中没有的键返回 18.20 的默认值。
API 密钥以 crypto.encrypt_to_b64 加密后存于 model.api_key_enc，任何时候都不以明文写入数据库或日志。
本模块的写函数不提交事务，由调用方提交。
"""
import copy
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Setting

# SPEC 18.20：键名及默认值
DEFAULTS: dict[str, Any] = {
    "model.base_url": "",
    "model.api_key_enc": "",
    "model.name": "",
    "model.temperature": 0.1,
    "model.max_tokens": 32000,
    "model.timeout_s": 180,
    "model.json_mode": True,
    "model.extra_params": {},
    "model.chunk_chars": 60000,
    "model.price_in_per_m": 0,
    "model.price_out_per_m": 0,
    "model.monthly_call_cap": 0,
    "redaction.mask_quantity": False,
    "review.company_profile": "",
    "backup.time": "02:00",
    "backup.keep": 7,
    "retention.original_days": 30,
    "retention.result_days": 365,
    "retention.outbound_days": 180,
    "retention.audit_days": 0,
    "retention.unfinished_days": 30,
    "retention.review_pending": False,
}


def get_setting(db: Session, key: str) -> Any:
    """读取设置；未知的键抛出 KeyError。返回值是副本，修改它不影响默认值。"""
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.get(Setting, key)
    return copy.deepcopy(row.value if row is not None else DEFAULTS[key])


def get_settings_map(db: Session, prefix: str) -> dict[str, Any]:
    """一次取出某一组设置（如 "model."），键为去掉前缀后的短名。"""
    return {k[len(prefix):]: get_setting(db, k) for k in DEFAULTS if k.startswith(prefix)}


def set_setting(db: Session, key: str, value: Any, user_id: uuid.UUID | None) -> None:
    """写入设置（不提交）。未知的键抛出 KeyError。"""
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, updated_by=user_id))
    else:
        row.value = value
        row.updated_by = user_id
    db.flush()
