"""余额对账（V351Q-21，SPEC 15.2）：用账户实际扣费发现厂商调价。

worker 每小时经网关查询一次支持的厂商（价格表 balance 字段：DeepSeek、Kimi）的账户余额，与本系统按价格表
记下的费用比较。账户按“接口主机 + 密钥”区分，同一账户下的多个模型合并计算。
- 对账窗口从首次查询、余额上升（充值或赠金）、价格表换版或满 30 天时重新起算，上一窗口的结论保留为 prev；
- 窗口内实际扣费与计算费用都不足 2 元时暂不判断（pending）；有未收录价格的调用时无法对账（unpriced）；
- 相差不超过 10% 为 ok，否则为 drift：价格表可能已过期，或同一账户还有别的程序在用。
状态存 settings 的 model.balance_watch，只有余额、金额与时间，没有任务数据；日志不写金额。
"""
import hashlib
import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting, set_setting
from app.db.base import utcnow
from app.db.models import LlmCall, LlmModel
from app.llm import gateway, pricing
from app.security import crypto

logger = logging.getLogger("app.llm.balance")

CHECK_EVERY = timedelta(minutes=55)
WINDOW = timedelta(days=30)
MIN_AMOUNT = Decimal("2")
TOLERANCE = Decimal("0.10")
_CENT = Decimal("0.01")


def _kind(base_url: str) -> str | None:
    host = pricing.host_of(base_url)
    return next((e.balance for e in pricing.table().entries if e.host == host and e.balance), None)


def account_id(base_url: str, api_key: str) -> str:
    return f"{pricing.host_of(base_url)}|{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"


def _money(value: Decimal) -> str:
    return str(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _window_cost(db: Session, model_ids: list[int], start: datetime, end: datetime) -> tuple[Decimal, int]:
    """窗口内本系统记下的费用（含中途出错但已计费的流式调用）与未收录价格的调用数。"""
    cost, unpriced = db.execute(select(
        func.coalesce(func.sum(LlmCall.cost), 0),
        func.count().filter(and_(LlmCall.cost.is_(None), LlmCall.status == "sent"))).where(
        LlmCall.model_id.in_(model_ids), LlmCall.status.in_(("sent", "error")),
        LlmCall.created_at >= start, LlmCall.created_at < end)).one()
    return Decimal(cost), unpriced


def _diff(spent: Decimal, cost: Decimal) -> str | None:
    """实际扣费比计算费用多（正）或少（负）的百分比，以两者较大者为基数。"""
    big = max(spent, cost)
    return str(((spent - cost) * 100 / big).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if big > 0 else None


def _verdict(spent: Decimal, cost: Decimal, unpriced: int) -> str:
    if unpriced:
        return "unpriced"
    big = max(spent, cost)
    if big < MIN_AMOUNT:
        return "pending"
    return "ok" if abs(spent - cost) <= big * TOLERANCE else "drift"


def check(db: Session, now: datetime | None = None) -> None:
    """定时工作：逐个账户查询余额并更新对账结论。查询失败只记错误码，不改窗口。"""
    now = now or utcnow()
    groups: dict[str, dict] = {}
    for row in db.scalars(select(LlmModel).order_by(LlmModel.id)):
        kind = _kind(row.base_url)
        if kind is None:
            continue
        key = crypto.decrypt_text(row.api_key_enc)
        group = groups.setdefault(account_id(row.base_url, key),
                                  {"base_url": row.base_url, "key": key, "kind": kind, "ids": [], "enabled": False})
        group["ids"].append(row.id)
        group["enabled"] = group["enabled"] or row.enabled
    watch = get_setting(db, "model.balance_watch")
    changed = False
    for acct, group in groups.items():
        state = watch.get(acct, {})
        last = state.get("checked_at")
        if not group["enabled"] or (last and now - datetime.fromisoformat(last) < CHECK_EVERY):
            continue
        changed = True
        state["checked_at"] = now.isoformat()
        try:
            balance = gateway.query_balance(group["base_url"], group["key"], group["kind"])
        except gateway.GatewayError as exc:
            state["error"] = str(exc)
            watch[acct] = state
            logger.warning("余额查询失败 账户=%s 错误=%s", acct.split("|")[0], exc.code)
            continue
        state.pop("error", None)
        version = pricing.table().version
        previous = Decimal(state["balance"]) if "balance" in state else None
        if (previous is None or balance > previous or state.get("version") != version
                or now - datetime.fromisoformat(state["base_at"]) > WINDOW):
            if state.get("verdict") in ("ok", "drift"):
                state["prev"] = {k: state.get(k) for k in ("verdict", "spent", "cost", "diff", "base_at")}
            state.update(base=str(balance), base_at=now.isoformat(), version=version, spent="0.00", cost="0.00",
                         diff=None, verdict="pending")
        else:
            spent = Decimal(state["base"]) - balance
            cost, unpriced = _window_cost(db, group["ids"], datetime.fromisoformat(state["base_at"]), now)
            state.update(spent=_money(spent), cost=_money(cost), diff=_diff(spent, cost),
                         verdict=_verdict(spent, cost, unpriced))
        state["balance"] = str(balance)
        watch[acct] = state
        logger.info("余额对账 账户=%s 结论=%s", acct.split("|")[0], state["verdict"])
    if changed:
        set_setting(db, "model.balance_watch", watch, None)
        db.commit()


def view(db: Session, row: LlmModel, api_key: str) -> dict | None:
    """模型卡片的对账信息；该厂商不支持余额查询时返回 None，尚未查询时 checked_at 为空。"""
    if _kind(row.base_url) is None:
        return None
    state = get_setting(db, "model.balance_watch").get(account_id(row.base_url, api_key), {})
    keys = ("balance", "checked_at", "base_at", "spent", "cost", "diff", "verdict", "error", "prev")
    return {k: state.get(k) for k in keys}
