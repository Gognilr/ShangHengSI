"""系统管理员多模型管理；审计只记录修改字段，不含密钥。"""
import re
from decimal import Decimal
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import exists, func, select, update as sql_update
from sqlalchemy.orm import Session as DbSession
from typing_extensions import Annotated

from app.admin.settings_store import get_setting, set_setting
from app.audit import service as audit
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import AssistantMessage, LlmCall, LlmModel, ReviewRun, User
from app.db.session import get_db
from app.llm import gateway
from app.llm.usage import monthly_usage
from app.security import crypto
from app.security.permissions import require
from app.security.errors import AppError, NotFoundError
from app.security.sessions import client_ip

_perm = require("sys.models")
router = APIRouter(prefix="/admin", dependencies=[Depends(_perm)])
_PRICE = re.compile(r"\d{1,8}(\.\d{1,4})?")
_KEY_CHARS = re.compile(r"[\x21-\x7e]{8,500}")
_EXTRA_MSG = "附加请求参数须为 JSON 对象，且不能包含 messages"


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    description: str | None = Field(None, max_length=200)
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    model_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    temperature: Decimal = Field(default=Decimal("0.1"), ge=0, le=2, decimal_places=2)
    max_tokens: int = Field(default=32000, ge=1, le=1_000_000)
    timeout_s: int = Field(default=180, ge=10, le=600)
    json_mode: bool = True
    extra_params: Any = Field(default_factory=dict)
    chunk_chars: int = Field(default=60000, ge=1000, le=1_000_000)
    price_in_per_m: str = "0"
    price_out_per_m: str = "0"
    monthly_call_cap: int = Field(default=0, ge=0, le=1_000_000_000)
    for_review: bool = True
    for_assist: bool = True
    sort_order: int = 0
    api_key: str | None = None

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


class DefaultIn(BaseModel):
    purpose: Literal["review", "assist"]


class EnabledIn(BaseModel):
    enabled: bool


class DailyCapIn(BaseModel):
    value: int = Field(ge=0)


def _get(db, mid):
    row = db.get(LlmModel, mid)
    if row is None:
        raise NotFoundError("MODEL_NOT_FOUND", "模型不存在")
    return row


def _used(db, mid):
    # 测试连接不算使用：只测试过的模型仍可删除（V3Q-17）。
    return bool(db.scalar(select(exists().where(LlmCall.model_id == mid, LlmCall.purpose != "test"))))


def _usage(db, mid=None):
    result = monthly_usage(db, mid)
    return {**result, "cost": str(result["cost"])}


def _read(db, row):
    data = {c.name: getattr(row, c.name) for c in LlmModel.__table__.columns if c.name != "api_key_enc"}
    for field in ("temperature", "price_in_per_m", "price_out_per_m"):
        data[field] = str(data[field])
    data.update(api_key_last4=crypto.decrypt_text(row.api_key_enc)[-4:], usage=_usage(db, row.id),
                is_default_review=get_setting(db, "model.default_review_id") == row.id,
                is_default_assist=get_setting(db, "model.default_assist_id") == row.id,
                used=_used(db, row.id))
    return data


def _audit(db, request, admin, mid, keys):
    audit.record(db, "setting_update", user_id=admin.id, target_type="model", target_id=str(mid),
                 detail={"keys": keys}, ip=client_ip(request))


def _test(db, cfg, user_id):
    results = {}
    for name, structured in (("connection", False), ("structured", True)):
        try:
            result = gateway.test_config(db, cfg, user_id, structured=structured)
            results[name] = {"ok": True, "model": result.model_name, "duration_ms": result.duration_ms}
        except gateway.GatewayError as exc:
            results[name] = {"ok": False, "message": f"{exc}：{exc.detail}" if exc.detail else str(exc)}
    return results


def _defaults_guard(db, row, values):
    for purpose in ("review", "assist"):
        if not values[f"for_{purpose}"] and get_setting(db, f"model.default_{purpose}_id") == row.id:
            raise AppError(409, "DEFAULT_IN_USE", "请先更换默认模型")


def _save(db, row, body, request, admin):
    if not isinstance(body.extra_params, dict) or "messages" in body.extra_params:
        raise AppError(422, "EXTRA_PARAMS_INVALID", _EXTRA_MSG)
    values = body.model_dump(exclude={"api_key"})
    for key in ("price_in_per_m", "price_out_per_m"):
        values[key] = Decimal(values[key])
    new = row is None
    if new and body.api_key is None:
        raise AppError(422, "API_KEY_REQUIRED", "新增模型须填写 API 密钥")
    if db.scalar(select(LlmModel.id).where(LlmModel.display_name == body.display_name,
                 *([LlmModel.id != row.id] if row else []))) is not None:
        raise AppError(409, "MODEL_NAME_EXISTS", "显示名称已存在")
    if row is not None:
        _defaults_guard(db, row, values)
    key = body.api_key if body.api_key is not None else crypto.decrypt_text(row.api_key_enc)
    changed = [name for name, value in values.items() if new or getattr(row, name) != value]
    key_changed = new or crypto.decrypt_text(row.api_key_enc) != key
    if key_changed:
        changed.append("api_key_enc")
    connection_changed = new or key_changed or bool(set(changed) & {"base_url", "model_name", "json_mode", "extra_params"})
    results = None
    structured_ok = row.structured_ok if row is not None else False
    tested_at = row.structured_tested_at if row is not None else None
    if connection_changed:
        cfg = gateway._Config(id=row.id if row is not None else None, base_url=body.base_url, api_key=key,
                              name=body.model_name, temperature=float(body.temperature), max_tokens=body.max_tokens,
                              timeout_s=body.timeout_s, json_mode=body.json_mode, extra_params=body.extra_params)
        results = _test(db, cfg, admin.id)
        structured_ok, tested_at = results["structured"]["ok"], utcnow()
    if body.for_review and not structured_ok:
        return JSONResponse(status_code=422, content={"code": "STRUCTURED_REQUIRED",
                            "message": "该模型未通过结构化输出测试，不能用于智能审核", "tests": results})
    if results is not None and not results["connection"]["ok"]:
        return JSONResponse(status_code=422, content={"code": "CONNECTION_FAILED",
                            "message": results["connection"]["message"], "tests": results})
    if new:
        row = LlmModel(**values, api_key_enc=crypto.encrypt_text(key), structured_ok=structured_ok,
                       structured_tested_at=tested_at)
        db.add(row)
    else:
        for name in changed:
            if name != "api_key_enc":
                setattr(row, name, values[name])
        if key_changed:
            row.api_key_enc = crypto.encrypt_text(key)
        row.structured_ok, row.structured_tested_at = structured_ok, tested_at
    db.flush()
    # 尚无默认模型时，保存的模型自动成为对应默认（V3Q-17）。
    for purpose in ("review", "assist"):
        key = f"model.default_{purpose}_id"
        if row.enabled and values[f"for_{purpose}"] and get_setting(db, key) is None:
            set_setting(db, key, row.id, admin.id)
            changed.append(key)
    if changed:
        _audit(db, request, admin, row.id, changed)
    db.commit()
    return {**_read(db, row), "tests": results}


@router.get("/models")
def list_models(db: DbSession = Depends(get_db)):
    return {"items": [_read(db, row) for row in db.scalars(select(LlmModel).order_by(LlmModel.sort_order, LlmModel.id))],
            "user_daily_cap": get_setting(db, "model.user_daily_cap"), "usage": _usage(db)}


@router.post("/models", status_code=201)
def create(body: ModelIn, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(LlmModel)) >= 10:
        raise AppError(409, "MODEL_LIMIT", "最多配置 10 个模型（含停用）")
    return _save(db, None, body, request, admin)


@router.put("/models/{mid}")
def update(mid: int, body: ModelIn, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)):
    return _save(db, _get(db, mid), body, request, admin)


@router.post("/models/{mid}/test")
def test(mid: int, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)):
    row = _get(db, mid)
    # 停用模型仍允许管理员测试连接。
    cfg = gateway._Config(id=row.id, base_url=row.base_url, api_key=crypto.decrypt_text(row.api_key_enc),
                          name=row.model_name, temperature=float(row.temperature), max_tokens=row.max_tokens,
                          timeout_s=row.timeout_s, json_mode=row.json_mode, extra_params=row.extra_params)
    results = _test(db, cfg, admin.id)
    row.structured_ok, row.structured_tested_at = results["structured"]["ok"], utcnow()
    _audit(db, request, admin, mid, ["structured_ok", "structured_tested_at"])
    db.commit()
    return {"tests": results, "structured_ok": row.structured_ok,
            "message": "该模型不能继续用于智能审核，请修正后重新测试" if row.for_review and not row.structured_ok else None}


@router.post("/models/{mid}/default")
def default(mid: int, body: DefaultIn, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)):
    from app.models_api import select_model
    row = select_model(db, mid, body.purpose)
    key = f"model.default_{body.purpose}_id"
    set_setting(db, key, row.id, admin.id)
    _audit(db, request, admin, mid, [key])
    db.commit()
    return _read(db, row)


@router.post("/models/{mid}/enabled")
def enabled(mid: int, body: EnabledIn, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)):
    row = _get(db, mid)
    if not body.enabled and any(get_setting(db, f"model.default_{p}_id") == mid for p in ("review", "assist")):
        raise AppError(409, "DEFAULT_IN_USE", "默认模型不能停用，请先更换默认模型")
    row.enabled = body.enabled
    _audit(db, request, admin, mid, ["enabled"])
    db.commit()
    return _read(db, row)


@router.delete("/models/{mid}", status_code=204)
def delete(mid: int, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)):
    row = _get(db, mid)
    if _used(db, mid):
        raise AppError(409, "MODEL_IN_USE", "已被调用过的模型只能停用")
    if any(db.scalar(select(exists().where(model.model_id == mid))) for model in (ReviewRun, AssistantMessage)):
        raise AppError(409, "MODEL_IN_USE", "模型已关联审核轮次或助手回复，只能停用")
    for purpose in ("review", "assist"):
        key = f"model.default_{purpose}_id"
        if get_setting(db, key) == mid:
            set_setting(db, key, None, admin.id)
    # 测试记录与未保存模型的测试一样不再归属模型。
    db.execute(sql_update(LlmCall).where(LlmCall.model_id == mid).values(model_id=None))
    db.delete(row)
    _audit(db, request, admin, mid, ["delete"])
    db.commit()
    return Response(status_code=204)


@router.put("/settings/user-daily-cap")
def daily(body: DailyCapIn, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)):
    set_setting(db, "model.user_daily_cap", body.value, admin.id)
    _audit(db, request, admin, "all", ["model.user_daily_cap"])
    db.commit()
    return {"value": body.value}


@router.get("/usage")
def get_usage(db: DbSession = Depends(get_db)):
    return {**_usage(db), "month": utcnow().astimezone(ZoneInfo(get_settings().tz or "Asia/Shanghai")).strftime("%Y-%m"),
            "models": [{"id": row.id, "display_name": row.display_name, "cap": row.monthly_call_cap,
                        **_usage(db, row.id)} for row in db.scalars(select(LlmModel).order_by(LlmModel.sort_order, LlmModel.id))]}
