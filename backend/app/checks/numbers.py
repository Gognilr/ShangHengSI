"""校验用数字解析与显示；不读取环境、不使用浮点数。"""
import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.redaction.detect import _CURRENCY

CENT = Decimal("0.01")
_CURRENCIES = re.compile("|".join(re.escape(k) for k in sorted(_CURRENCY, key=len, reverse=True)), re.IGNORECASE)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)")
_RATE = re.compile(r"\+?(?:\d+(?:\.\d+)?|\.\d+)(%)?")


def parse_number(text: str) -> Decimal | None:
    """单个明确数字，可带币种和单位；范围或多值不取其中一个。"""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    # Q-91：括号负数、倍率单位和百分比的含义未约定，宁可提示无法核验，不猜其数值。
    if not text or any(c in text for c in "()%万亿"):
        return None
    text = _CURRENCIES.sub(" ", text)
    matches = list(_NUMBER.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    if any(c in text[:match.start()] + text[match.end():] for c in ".,+-0123456789"):
        return None
    return Decimal(match.group().replace(",", ""))


def parse_rate(text: str) -> Decimal | None:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    match = _RATE.fullmatch(text)
    if not match:
        return None
    value = Decimal(text.rstrip("%"))
    return value / Decimal(100) if match.group(1) or value > 1 else value


def rounded(value: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = max(28, len(value.as_tuple().digits) + max(value.adjusted(), 0) + 4)
        return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money(d: Decimal) -> str:
    return f"{rounded(d):,.2f}"


def pct_diff(actual: Decimal, expected: Decimal) -> str:
    if expected == 0:
        return "无法按比例计算"
    with localcontext() as ctx:
        ctx.prec = max(28, len(actual.as_tuple().digits) + len(expected.as_tuple().digits) + 8)
        value = abs(actual - expected) / abs(expected) * Decimal(100)
        if value < Decimal("0.1"):
            return "不足 0.1%"
        return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):.1f}%"
