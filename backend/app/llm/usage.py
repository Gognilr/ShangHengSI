"""模型用量统计（SPEC 15.2、18.18）。

只统计 status=sent 的记录；月份按 Asia/Shanghai（配置项 TZ）计算。费用用 Decimal 估算，保留 2 位小数（ROUND_HALF_UP）。
"""
from datetime import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import LlmCall, LlmModel

_MILLION = Decimal(1_000_000)


def month_start(now: datetime | None = None) -> datetime:
    """本月 1 日 0 点（本地时区）对应的时刻。"""
    tz = ZoneInfo(get_settings().tz or "Asia/Shanghai")
    local = (now or utcnow()).astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def monthly_sent_calls(db: Session, model_id: int | None = None, now: datetime | None = None) -> int:
    return db.scalar(
        select(func.count()).select_from(LlmCall)
        .where(LlmCall.status == "sent", LlmCall.created_at >= month_start(now),
               *([LlmCall.model_id == model_id] if model_id is not None else []))
    ) or 0


def cap_reached(db: Session, user_id: uuid.UUID | None, purpose: str, model_id: int) -> str | None:
    """只读上限预检，不预占名额；网关在发送前仍会再次检查。"""
    if purpose == "test":
        return None
    cap = db.get(LlmModel, model_id).monthly_call_cap
    if cap and monthly_sent_calls(db, model_id) >= cap:
        return "CAP_REACHED"
    daily_cap = int(get_setting(db, "model.user_daily_cap") or 0)
    if not daily_cap or user_id is None:
        return None
    start = utcnow().astimezone(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar(select(func.count()).select_from(LlmCall).where(
        LlmCall.user_id == user_id, LlmCall.status == "sent", LlmCall.purpose != "test",
        LlmCall.created_at >= start)) or 0
    return "USER_CAP_REACHED" if used >= daily_cap else None


def monthly_usage(db: Session, model_id: int | None = None, now: datetime | None = None) -> dict:
    """按每个模型单价估算，再合计；未保存模型的测试记录只有用量、无定价。"""
    query = select(LlmCall.model_id, func.count(), func.sum(LlmCall.tokens_in), func.sum(LlmCall.tokens_out)).where(
        LlmCall.status == "sent", LlmCall.created_at >= month_start(now))
    if model_id is not None:
        query = query.where(LlmCall.model_id == model_id)
    result = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": Decimal("0.00")}
    for mid, calls, tin, tout in db.execute(query.group_by(LlmCall.model_id)):
        result["calls"] += calls
        result["tokens_in"] += tin
        result["tokens_out"] += tout
        model = db.get(LlmModel, mid) if mid is not None else None
        if model is not None:
            cost = (Decimal(tin) * model.price_in_per_m + Decimal(tout) * model.price_out_per_m) / _MILLION
            result["cost"] += cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return result


def remaining_calls(db: Session, user_id: uuid.UUID, model_id: int) -> dict[str, int | None]:
    """翻译分段预检的剩余调用数；None 表示该项不限次数，网关发送前仍再次检查。"""
    cap = db.get(LlmModel, model_id).monthly_call_cap
    daily = int(get_setting(db, "model.user_daily_cap") or 0)
    start = utcnow().astimezone(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar(select(func.count()).select_from(LlmCall).where(
        LlmCall.user_id == user_id, LlmCall.status == "sent", LlmCall.purpose != "test", LlmCall.created_at >= start)) or 0
    return {"monthly": max(0, cap - monthly_sent_calls(db, model_id)) if cap else None,
            "daily": max(0, daily - used) if daily else None}
