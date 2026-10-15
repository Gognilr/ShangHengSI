"""模型用量统计（SPEC 15.2、18.18）。

只统计 status=sent 的记录；月份按 Asia/Shanghai（配置项 TZ）计算。费用用 Decimal 估算，保留 2 位小数（ROUND_HALF_UP）。
"""
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import LlmCall

_MILLION = Decimal(1_000_000)


def month_start(now: datetime | None = None) -> datetime:
    """本月 1 日 0 点（本地时区）对应的时刻。"""
    tz = ZoneInfo(get_settings().tz or "Asia/Shanghai")
    local = (now or utcnow()).astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def monthly_sent_calls(db: Session, now: datetime | None = None) -> int:
    return db.scalar(
        select(func.count()).select_from(LlmCall)
        .where(LlmCall.status == "sent", LlmCall.created_at >= month_start(now))
    ) or 0


def monthly_usage(db: Session, now: datetime | None = None) -> dict:
    """{calls, tokens_in, tokens_out, cost}；cost 按设置中的每百万 token 单价（元）估算。"""
    calls, tokens_in, tokens_out = db.execute(
        select(func.count(), func.coalesce(func.sum(LlmCall.tokens_in), 0), func.coalesce(func.sum(LlmCall.tokens_out), 0))
        .where(LlmCall.status == "sent", LlmCall.created_at >= month_start(now))
    ).one()
    price_in = Decimal(str(get_setting(db, "model.price_in_per_m")))
    price_out = Decimal(str(get_setting(db, "model.price_out_per_m")))
    cost = (Decimal(tokens_in) * price_in + Decimal(tokens_out) * price_out) / _MILLION
    return {
        "calls": int(calls),
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "cost": cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    }
