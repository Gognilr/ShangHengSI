"""模型计价（V351Q-21，SPEC 15.2、18.18）。

价格表 resources/model_prices.yaml 随发布包更新，运行时不联网。按“接口主机 + 模型名”匹配；网关记录每次调用时
按调用当时的价格算好费用写入 llm_calls.cost，查不到价格时为空（界面显示“未收录价格”）。
单次费用用 Decimal 保留 6 位小数（ROUND_HALF_UP），统计时再求和取 2 位。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml

logger = logging.getLogger("app.llm.pricing")

PRICES_FILE = Path(__file__).resolve().parents[2] / "resources" / "model_prices.yaml"
BEIJING = ZoneInfo("Asia/Shanghai")  # 厂商的高峰时段按北京时间，与界面时区设置无关
_MILLION = Decimal(1_000_000)
_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class Tier:
    input: Decimal
    cached: Decimal
    output: Decimal
    output_thinking: Decimal | None = None
    max_input: int | None = None
    max_output: int | None = None


@dataclass(frozen=True)
class Entry:
    vendor: str
    host: str
    names: tuple[str, ...]
    effective: date
    source: str
    tiers: tuple[Tier, ...]
    offpeak: tuple[Tier, ...] = ()
    peak_hours: tuple[tuple[time, time], ...] = ()
    balance: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Table:
    version: str
    entries: tuple[Entry, ...]
    holidays: frozenset[date]


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    thinking: bool = False


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def parse_usage(data) -> Usage:
    """各家 usage 写法（以官方文档为准）：DeepSeek prompt_cache_hit_tokens（同时有 prompt_tokens_details.cached_tokens）；
    智谱、通义、Kimi prompt_tokens_details.cached_tokens（Kimi 另有顶层 cached_tokens）。prompt_tokens 均含命中部分。"""
    if not isinstance(data, dict):
        return Usage()
    details = data.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    out_details = data.get("completion_tokens_details")
    out_details = out_details if isinstance(out_details, dict) else {}
    tokens_in = int(data.get("prompt_tokens") or 0)
    cached = _int(data.get("prompt_cache_hit_tokens")) or _int(details.get("cached_tokens")) or _int(data.get("cached_tokens"))
    return Usage(tokens_in, int(data.get("completion_tokens") or 0), min(cached, tokens_in),
                 thinking=_int(out_details.get("reasoning_tokens")) > 0)


def merge_usage(acc: dict, data) -> None:
    """流式回复的 usage 常只在最后一个事件出现，个别服务分几次给：后到的非空值覆盖先到的。"""
    if isinstance(data, dict):
        acc.update({k: v for k, v in data.items() if v not in (None, 0, "", {})})


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _tiers(rows) -> tuple[Tier, ...]:
    return tuple(Tier(input=_dec(r["input"]), cached=_dec(r["cached"]), output=_dec(r["output"]),
                      output_thinking=_dec(r["output_thinking"]) if r.get("output_thinking") is not None else None,
                      max_input=r.get("max_input"), max_output=r.get("max_output")) for r in rows or ())


def _hours(rows) -> tuple[tuple[time, time], ...]:
    result = []
    for text in rows or ():
        start, end = text.split("-")
        result.append((time.fromisoformat(start), time.fromisoformat(end)))
    return tuple(result)


def _holidays(rows) -> frozenset[date]:
    days = set()
    for text in rows or ():
        start, _, end = str(text).partition("..")
        day, last = date.fromisoformat(start), date.fromisoformat(end or start)
        while day <= last:
            days.add(day)
            day += timedelta(days=1)
    return frozenset(days)


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


@cache
def table() -> Table:
    data = yaml.safe_load(PRICES_FILE.read_text(encoding="utf-8"))
    entries = []
    for v in data["vendors"]:
        for m in v["models"]:
            entries.append(Entry(
                vendor=v["vendor"], host=v["host"].lower(), names=tuple(n.lower() for n in m["names"]),
                effective=_as_date(m.get("effective", v["effective"])), source=v["source"], tiers=_tiers(m["tiers"]),
                offpeak=_tiers(m.get("offpeak")), peak_hours=_hours(v.get("peak_hours")),
                balance=v.get("balance"), note=m.get("note") or v.get("note")))
    return Table(str(data["version"]), tuple(entries), _holidays(data.get("cn_holidays")))


def host_of(base_url: str | None) -> str:
    try:
        return (urlsplit((base_url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def _match(entry: Entry, name: str) -> int:
    """2 精确，1 前缀，0 不匹配。"""
    if name in entry.names:
        return 2
    return 1 if any(n.endswith("*") and name.startswith(n[:-1]) for n in entry.names) else 0


def lookup(base_url: str | None, model_name: str, when: datetime | None = None) -> Entry | None:
    """base_url 为空（已删除模型的旧测试记录）时只按模型名，只有一家收录该名称才算匹配。"""
    host, name = host_of(base_url) if base_url else None, (model_name or "").strip().lower()
    scored = [(_match(e, name), e) for e in table().entries if host is None or e.host == host]
    best = max((s for s, _ in scored), default=0)
    found = [e for s, e in scored if s and s == best]
    if not found or (host is None and len({e.host for e in found}) > 1):
        return None
    day = (when or datetime.now(timezone.utc)).astimezone(BEIJING).date()
    current = [e for e in found if e.effective <= day]
    return max(current, key=lambda e: e.effective) if current else min(found, key=lambda e: e.effective)


def is_peak(entry: Entry, when: datetime) -> bool:
    if not entry.peak_hours:
        return True
    local = when.astimezone(BEIJING)
    if local.weekday() >= 5 or local.date() in table().holidays:
        return False
    return any(start <= local.time() < end for start, end in entry.peak_hours)


def _tier(tiers: tuple[Tier, ...], usage: Usage) -> Tier:
    for t in tiers:
        if (t.max_input is None or usage.tokens_in <= t.max_input) and (t.max_output is None or usage.tokens_out <= t.max_output):
            return t
    return tiers[-1]


def call_cost(base_url: str | None, model_name: str, usage: Usage, when: datetime | None = None) -> Decimal | None:
    """单次调用费用（元）；未收录价格返回 None。价格表读不出时也返回 None，不能因此丢掉外发记录。"""
    when = when or datetime.now(timezone.utc)
    try:
        entry = lookup(base_url, model_name, when)
    except Exception as exc:  # noqa: BLE001
        logger.error("价格表无法读取: %s", type(exc).__name__)
        return None
    if entry is None:
        return None
    t = _tier(entry.offpeak if entry.offpeak and not is_peak(entry, when) else entry.tiers, usage)
    cached = min(usage.tokens_cached, usage.tokens_in)
    out = t.output_thinking if usage.thinking and t.output_thinking is not None else t.output
    total = (Decimal(usage.tokens_in - cached) * t.input + Decimal(cached) * t.cached + Decimal(usage.tokens_out) * out) / _MILLION
    return total.quantize(_PLACES, rounding=ROUND_HALF_UP)


def _tier_view(t: Tier) -> dict:
    return {"input": str(t.input), "cached": str(t.cached), "output": str(t.output),
            "output_thinking": str(t.output_thinking) if t.output_thinking is not None else None,
            "max_input": t.max_input, "max_output": t.max_output}


def describe(base_url: str, model_name: str) -> dict | None:
    """模型卡片与编辑抽屉的只读单价；未收录返回 None。"""
    try:
        entry = lookup(base_url, model_name)
        version = table().version
    except Exception as exc:  # noqa: BLE001
        logger.error("价格表无法读取: %s", type(exc).__name__)
        return None
    if entry is None:
        return None
    return {"vendor": entry.vendor, "version": version, "effective": entry.effective.isoformat(), "source": entry.source,
            "note": entry.note, "tiers": [_tier_view(t) for t in entry.tiers],
            "offpeak": [_tier_view(t) for t in entry.offpeak] or None,
            "peak_hours": [f"{a:%H:%M}-{b:%H:%M}" for a, b in entry.peak_hours] or None,
            "balance": entry.balance is not None}
