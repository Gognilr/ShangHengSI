"""模型设置：/api/v1/admin/settings/model*、/admin/usage（SPEC 11.1、15.2，设计 M7 第五节）。

- API 密钥用 crypto.encrypt_to_b64 加密后存 model.api_key_enc；任何响应、日志、审计都不出现完整密钥（含末 4 位也不进审计）。
- 单价在接口中收发字符串，存入 settings 也存字符串，计算一律 Decimal。
- 测试连接测的是已保存的设置（Q-106），经网关，写 llm_calls（purpose=test），不写审计。
"""
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy.orm import Session as DbSession
from typing_extensions import Annotated

from app.admin.settings_store import get_setting, get_settings_map, set_setting
from app.audit import service as audit
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import User
from app.db.session import get_db
from app.llm import gateway
from app.llm.usage import monthly_usage
from app.security import crypto
from app.security.deps import require_sys_admin
from app.security.errors import AppError
from app.security.sessions import client_ip

router = APIRouter(prefix="/admin", dependencies=[Depends(require_sys_admin)])

_PRICE = re.compile(r"\d{1,9}(\.\d{1,4})?")
_KEY_CHARS = re.compile(r"[\x21-\x7e]{8,500}")
_EXTRA_MSG = "附加请求参数须为 JSON 对象，且不能包含 messages"


def _norm_price(value: Any) -> str:
    """单价规范成至少 2 位小数的字符串（"0" → "0.00"，"1.5" → "1.50"）。"""
    d = Decimal(str(value))
    text = f"{d:f}"
    if "." not in text:
        return text + ".00"
    whole, frac = text.split(".")
    return f"{whole}.{frac.ljust(2, '0')}"


class ModelSettings(BaseModel):
    base_url: str
    name: str
    temperature: float
    max_tokens: int
    timeout_s: int
    json_mode: bool
    extra_params: dict
    chunk_chars: int
    price_in_per_m: str
    price_out_per_m: str
    monthly_call_cap: int
    user_daily_cap: int
    api_key_set: bool
    api_key_last4: Optional[str]


class ModelSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1, le=1_000_000)
    timeout_s: int = Field(ge=10, le=600)
    json_mode: bool
    extra_params: Any
    chunk_chars: int = Field(ge=1000, le=1_000_000)
    price_in_per_m: str
    price_out_per_m: str
    monthly_call_cap: int = Field(ge=0, le=1_000_000_000)
    user_daily_cap: int = Field(default=0, ge=0, le=10000)
    api_key: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not re.fullmatch(r"https?://\S+", v):
            raise ValueError("base_url")
        return v

    @field_validator("price_in_per_m", "price_out_per_m")
    @classmethod
    def _price(cls, v: str) -> str:
        v = v.strip()
        if not _PRICE.fullmatch(v):
            raise ValueError("price")
        return v

    @field_validator("api_key")
    @classmethod
    def _key(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if not _KEY_CHARS.fullmatch(v):
            raise ValueError("api_key")
        return v


class ConnectionResult(BaseModel):
    ok: bool
    model: Optional[str] = None
    duration_ms: Optional[int] = None
    reply: Optional[str] = None
    message: Optional[str] = None


class UsageOut(BaseModel):
    calls: int
    cap: int
    tokens_in: int
    tokens_out: int
    cost: str
    month: str


def _read(db: DbSession) -> ModelSettings:
    s = get_settings_map(db, "model.")
    enc = s["api_key_enc"]
    last4 = crypto.decrypt_from_b64(enc)[-4:] if enc else None
    extra = s["extra_params"] if isinstance(s["extra_params"], dict) else {}
    return ModelSettings(
        base_url=s["base_url"],
        name=s["name"],
        temperature=float(s["temperature"]),
        max_tokens=int(s["max_tokens"]),
        timeout_s=int(s["timeout_s"]),
        json_mode=bool(s["json_mode"]),
        extra_params=extra,
        chunk_chars=int(s["chunk_chars"]),
        price_in_per_m=_norm_price(s["price_in_per_m"]),
        price_out_per_m=_norm_price(s["price_out_per_m"]),
        monthly_call_cap=int(s["monthly_call_cap"] or 0),
        user_daily_cap=int(s["user_daily_cap"] or 0),
        api_key_set=bool(enc),
        api_key_last4=last4,
    )


@router.get("/settings/model", response_model=ModelSettings)
def get_model(db: DbSession = Depends(get_db)) -> ModelSettings:
    return _read(db)


@router.put("/settings/model", response_model=ModelSettings)
def put_model(
    body: ModelSettingsIn,
    request: Request,
    admin: User = Depends(require_sys_admin),
    db: DbSession = Depends(get_db),
) -> ModelSettings:
    extra = body.extra_params
    if not isinstance(extra, dict) or "messages" in extra:
        raise AppError(422, "EXTRA_PARAMS_INVALID", _EXTRA_MSG)

    new_values: dict[str, Any] = {
        "model.base_url": body.base_url,
        "model.name": body.name,
        "model.temperature": body.temperature,
        "model.max_tokens": body.max_tokens,
        "model.timeout_s": body.timeout_s,
        "model.json_mode": body.json_mode,
        "model.extra_params": extra,
        "model.chunk_chars": body.chunk_chars,
        "model.price_in_per_m": _norm_price(body.price_in_per_m),
        "model.price_out_per_m": _norm_price(body.price_out_per_m),
        "model.monthly_call_cap": body.monthly_call_cap,
        "model.user_daily_cap": body.user_daily_cap,
    }
    changed: list[str] = []
    for key, value in new_values.items():
        old = get_setting(db, key)
        if key in ("model.price_in_per_m", "model.price_out_per_m"):
            try:
                same = Decimal(str(old)) == Decimal(value)
            except InvalidOperation:
                same = False
        elif key == "model.temperature":
            same = float(old) == float(value)
        else:
            same = old == value
        if not same:
            set_setting(db, key, value, admin.id)
            changed.append(key)
    if body.api_key is not None:
        old_enc = get_setting(db, "model.api_key_enc")
        if not old_enc or crypto.decrypt_from_b64(old_enc) != body.api_key:
            set_setting(db, "model.api_key_enc", crypto.encrypt_to_b64(body.api_key), admin.id)
            changed.append("model.api_key_enc")
    if changed:
        audit.record(
            db,
            "setting_update",
            user_id=admin.id,
            target_type="setting",
            target_id="model",
            detail={"keys": changed},
            ip=client_ip(request),
        )
    db.commit()
    return _read(db)


@router.post("/settings/model/test", response_model=ConnectionResult)
def test_model(admin: User = Depends(require_sys_admin), db: DbSession = Depends(get_db)) -> ConnectionResult:
    try:
        result = gateway.test_connection(db, admin.id)
    except gateway.GatewayError as exc:
        # 附上模型服务给出的原因（如“模型名称不支持”），便于管理员自行排查；已去掉密钥（M8-05）
        message = f"{exc}：{exc.detail}" if exc.detail else str(exc)
        return ConnectionResult(ok=False, message=message)
    return ConnectionResult(ok=True, model=result.model_name, duration_ms=result.duration_ms, reply=result.content[:200])


@router.get("/usage", response_model=UsageOut)
def get_usage(db: DbSession = Depends(get_db)) -> UsageOut:
    u = monthly_usage(db)
    tz = ZoneInfo(get_settings().tz or "Asia/Shanghai")
    return UsageOut(
        calls=u["calls"],
        cap=int(get_setting(db, "model.monthly_call_cap") or 0),
        tokens_in=u["tokens_in"],
        tokens_out=u["tokens_out"],
        cost=str(u["cost"]),
        month=utcnow().astimezone(tz).strftime("%Y-%m"),
    )
