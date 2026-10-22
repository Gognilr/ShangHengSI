"""出口复检（SPEC 9.8，open_questions Q-19，设计 docs/design/M2.md 第六节）。

模型网关在每次发送前，对最终请求的全部文本调用 recheck()：
1. 本任务对照表中的全部真实值、启用中的已记住名称和自定义敏感词，任何一个出现即拦截；
2. 用 9.3 第 4、5 步的正则重新扫描，命中未替换的电话、邮箱、账号、证件号、带币种的金额即拦截。

拦截原因只有类别和位置，不含真实值；本模块不写日志。

数字类真实值（Q-19）：前后都不是数字时才算出现；6 位以下的短数字还要紧挨着币种或“元”等单位才算，
避免“数量 100 MT”被金额 100 误拦。长数字（账号、电话等）去掉空格和短横线后再比较一次，防止换了写法漏过。
同一数值换了千分位写法（“USD 400000”对“400,000.00”）且带币种时，按数值相等拦截。
"""
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RedactionTerm
from app.redaction import detect
from app.redaction.parties import name_pattern
from app.security import crypto

_RESCAN_CATEGORIES = {"PHONE", "EMAIL", "ACCOUNT", "IDNO"}
_NUMERIC_VALUE = re.compile(r"^[+\d][\d\s,.\-]*$")
_AMOUNT_VALUE = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^\d+(?:\.\d+)?$")
# 数字前不能是下划线：代号“[金额_10]”的序号不是数字类真实值
_NUMBER_IN_TEXT = re.compile(r"(?<![\d.,_])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d])")
# 币种与数字之间可隔一个填空括号：“人民币【123456】元”（Q-134 ①，与 detect.FILL_OPEN / FILL_CLOSE 相同）
_CURRENCY_BEFORE = re.compile(
    r"(?:US\$|HK\$|RMB|CNY|USD|EUR|JPY|GBP|HKD|AED|SAR|TRY|INR|AUD|CAD|CHF|SGD|KRW|RUB|人民币|美元|欧元|[¥￥$€£])\s?"
    r"(?:[【［\[〖][ \t]*)?$",
    re.IGNORECASE,
)
_CURRENCY_AFTER = re.compile(
    r"^(?:[ \t]*[】］\]〗])?\s?(?:万美元|万元|亿元|美元|欧元|英镑|日元|港元|港币|元|RMB|CNY|USD|EUR|JPY|GBP|HKD|AED|SAR|TRY|INR|AUD|CAD|CHF|SGD|KRW|RUB)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)
_SHORT_DIGITS = 6  # 少于 6 位数字的数字类真实值，须带币种或单位才算出现
_LONG_DIGITS = 8  # 至少 8 位数字的数字类真实值，去掉分隔符后也比较


@dataclass(frozen=True)
class BlockReason:
    category: str  # MAP / TERM / PHONE / EMAIL / ACCOUNT / IDNO / AMOUNT
    position: int  # 在被检查文本中的字符位置，不记真实值


@dataclass
class RecheckContext:
    real_values: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)  # 本任务对照表中的代号原文（Q-134 ②）

    def __repr__(self) -> str:  # 防止真实值被意外打印进日志
        return f"RecheckContext(real_values={len(self.real_values)}, terms={len(self.terms)})"


def _mask_placeholders(text: str, placeholders: Iterable[str]) -> str:
    """把代号原文换成等长的不可匹配字符，位置不变。只用于对照表真实值和词条的比对：
    代号“[电话]”中的“电话”二字不是外发的真实值（B-04）。格式化信息与金额的重扫仍在原文上做。"""
    for code in sorted({p for p in placeholders if p}, key=len, reverse=True):
        text = text.replace(code, "\x00" * len(code))
    return text


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _decimal(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def _has_currency(text: str, start: int, end: int) -> bool:
    return bool(_CURRENCY_BEFORE.search(text[max(0, start - 10):start]) or _CURRENCY_AFTER.match(text[end:end + 10]))


def _digit_bounded(text: str, start: int, end: int) -> bool:
    """前后都不是数字（也不是紧接数字的千分位或小数点）；前面是下划线时是代号序号（“[金额_10]”），不算。"""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before.isdigit() or after.isdigit() or before == "_":
        return False
    if before in ",." and start > 1 and text[start - 2].isdigit():
        return False
    if after in ",." and end + 1 < len(text) and text[end + 1].isdigit():
        return False
    return True


def _numeric_positions(text: str, value: str) -> list[int]:
    out = []
    n_digits = len(_digits(value))
    for m in re.finditer(re.escape(value), text):
        if _digit_bounded(text, m.start(), m.end()) and (
                n_digits >= _SHORT_DIGITS or _has_currency(text, m.start(), m.end())):
            out.append(m.start())
    if n_digits >= _LONG_DIGITS:
        compact = _digits(value)
        # 文本中的数字串（允许空格、短横线分隔）去掉分隔符后与真实值相同
        for m in re.finditer(r"(?<!\d)\d[\d \-]*\d(?!\d)", text):
            if compact in _digits(m.group()):
                out.append(m.start())
    if _AMOUNT_VALUE.match(value):
        target = _decimal(value)
        for m in _NUMBER_IN_TEXT.finditer(text):
            if _decimal(m.group(1)) == target and _has_currency(text, m.start(1), m.end(1)):
                out.append(m.start(1))
    return out


def _text_pattern(value: str) -> re.Pattern:
    # 全大写的短英文简称区分大小写，与脱敏时一致；其余英文不区分大小写、忽略多余空格
    short_caps = value.isascii() and value.isupper() and len(value) <= 6
    return name_pattern(value, case_sensitive=short_caps)


def _value_positions(text: str, value: str) -> list[int]:
    value = value.strip()
    if len(value) < 2:
        return []
    if _NUMERIC_VALUE.match(value):
        return _numeric_positions(text, value)
    return [m.start() for m in _text_pattern(value).finditer(text)]


def recheck(text: str, *, real_values: Iterable[str], terms: Iterable[str],
            placeholders: Iterable[str] = ()) -> list[BlockReason]:
    """返回全部拦截原因（按位置排序、去重）；空列表表示可以发送。

    placeholders 为本任务对照表中的代号原文：比对真实值、词条前先遮盖（只遮盖这些确切的代号，默认不遮盖）。"""
    reasons: set[BlockReason] = set()
    masked = _mask_placeholders(text, placeholders)
    for value in dict.fromkeys(real_values):
        reasons.update(BlockReason("MAP", p) for p in _value_positions(masked, value))
    for term in dict.fromkeys(terms):
        reasons.update(BlockReason("TERM", p) for p in _value_positions(masked, term))
    for hit in detect.find_formatted(text):
        if hit.category in _RESCAN_CATEGORIES:
            reasons.add(BlockReason(hit.category, hit.start))
    for hit in detect.find_amounts(text):
        reasons.add(BlockReason("AMOUNT", hit.start))
    return sorted(reasons, key=lambda r: (r.position, r.category))


def recheck_context(db: Session, task_id: uuid.UUID | None) -> RecheckContext:
    """汇集对照表真实值（有任务时）与启用中的已记住名称、自定义敏感词。"""
    from app.redaction.engine import load_placeholders, load_real_values  # 避免循环导入

    values = load_real_values(db, task_id) if task_id is not None else []
    codes = load_placeholders(db, task_id) if task_id is not None else []
    rows = db.scalars(select(RedactionTerm.term_enc).where(RedactionTerm.enabled.is_(True)))
    return RecheckContext(real_values=values, terms=[crypto.decrypt_text(blob) for blob in rows], placeholders=codes)
