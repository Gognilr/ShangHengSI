"""模型用量统计（SPEC 15.2、18.18）。

只统计 status=sent 的记录；月份按 Asia/Shanghai（配置项 TZ）计算。费用用 Decimal 估算，保留 2 位小数（ROUND_HALF_UP）。
"""
from datetime import datetime
from contextlib import contextmanager
from contextvars import ContextVar
import uuid
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, case, cast, func, select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import LlmCall, LlmModel

_MILLION = Decimal(1_000_000)
_review_round: ContextVar[uuid.UUID | None] = ContextVar("review_round", default=None)


@contextmanager
def review_round(run_id: uuid.UUID):
    """审核线程的额度上下文；只影响额度检查，网关仍逐次复检并记录实际调用。"""
    token = _review_round.set(run_id)
    try:
        yield
    finally:
        _review_round.reset(token)


def _quota_calls(db: Session, start: datetime, *conditions) -> int:
    """审核分组、分段及重试按轮占一次额度；其他用途和无轮次旧记录仍按请求计。

    实际用量统计保持逐次请求计数，避免少报 token、费用或外发次数。
    """
    key = case((and_(LlmCall.purpose == "review", LlmCall.run_id.is_not(None)),
                "run:" + cast(LlmCall.run_id, String)), else_="call:" + cast(LlmCall.id, String))
    return db.scalar(select(func.count(func.distinct(key))).where(
        LlmCall.status == "sent", LlmCall.created_at >= start, *conditions)) or 0


def month_start(now: datetime | None = None) -> datetime:
    """本月 1 日 0 点（本地时区）对应的时刻。"""
    tz = ZoneInfo(get_settings().tz or "Asia/Shanghai")
    local = (now or utcnow()).astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def day_start() -> datetime:
    """今天 0 点（本地时区，与月份统计同一时区）对应的时刻。"""
    tz = ZoneInfo(get_settings().tz or "Asia/Shanghai")
    return utcnow().astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)


def monthly_sent_calls(db: Session, model_id: int | None = None, now: datetime | None = None) -> int:
    """月度额度与告警用次数：审核按轮，其余按请求。用量页面使用 monthly_usage 的实际次数。"""
    return _quota_calls(db, month_start(now), *([LlmCall.model_id == model_id] if model_id is not None else []))


def cap_reached(db: Session, user_id: uuid.UUID | None, purpose: str, model_id: int) -> str | None:
    """只读上限预检，不预占名额；网关在发送前仍会再次检查。"""
    if purpose == "test":
        return None
    run_id = _review_round.get() if purpose == "review" else None
    if run_id is not None and db.scalar(select(LlmCall.id).where(
        LlmCall.run_id == run_id, LlmCall.model_id == model_id, LlmCall.user_id == user_id,
        LlmCall.purpose == "review", LlmCall.status == "sent").limit(1)) is not None:
        return None  # 本轮已占用额度，后续分组、分段与格式重试继续经网关发送。
    cap = db.get(LlmModel, model_id).monthly_call_cap
    if cap and _quota_calls(db, month_start(), LlmCall.model_id == model_id) >= cap:
        return "CAP_REACHED"
    daily_cap = int(get_setting(db, "model.user_daily_cap") or 0)
    if not daily_cap or user_id is None:
        return None
    start = day_start()
    used = _quota_calls(db, start, LlmCall.user_id == user_id, LlmCall.purpose != "test")
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
    start = day_start()
    used = _quota_calls(db, start, LlmCall.user_id == user_id, LlmCall.purpose != "test")
    return {"monthly": max(0, cap - _quota_calls(db, month_start(), LlmCall.model_id == model_id)) if cap else None,
            "daily": max(0, daily - used) if daily else None}
