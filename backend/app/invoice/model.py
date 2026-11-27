"""发票记录与金额工具（设计第三节）。金额一律用 Decimal（ROUND_HALF_UP）。"""
import re
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")

# 字段 → 核对提示里的叫法
FIELD_LABELS = {
    "code": "代码", "number": "号码", "date": "日期",
    "buyer_name": "购买方名称", "buyer_tax": "购买方税号", "seller_name": "销售方名称", "seller_tax": "销售方税号",
    "amount": "金额", "tax": "税额", "total": "价税合计",
}
# 来源优先级（设计第三节取值顺序）：结构化 > 文字层 > OCR
SOURCE_RANK = {"xml": 0, "ofd": 1, "text": 2, "scan": 3, "photo": 3}
OCR_SOURCES = ("scan", "photo")


def quantize(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


_MONEY_CHARS = re.compile(r"[¥￥,，\s]")


def parse_money(raw: str | None) -> Decimal | None:
    """“¥-1,234.50”“-¥12.00”“12.3” → Decimal；读不出时 None。"""
    if raw is None:
        return None
    s = _MONEY_CHARS.sub("", str(raw)).replace("－", "-")
    neg = s.count("-") % 2 == 1
    s = s.replace("-", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        return None
    try:
        v = quantize(Decimal(s))
    except InvalidOperation:
        return None
    return -v if neg else v


_CN_DIGIT = {"零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
             "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"拾": 10, "佰": 100, "仟": 1000, "十": 10, "百": 100, "千": 1000}
CN_AMOUNT_CHARS = set(_CN_DIGIT) | set(_CN_UNIT) | set("万亿元圆角分整正负")


def _cn_section(s: str) -> int | None:
    """万以下的一节：“壹仟零贰拾叁” → 1023。"""
    total, digit = 0, None
    for ch in s:
        if ch in _CN_DIGIT:
            digit = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            total += (1 if digit is None else digit) * _CN_UNIT[ch]
            digit = None
        else:
            return None
    return total + (digit or 0)


def cn_amount(raw: str | None) -> Decimal | None:
    """大写金额 → Decimal：“壹佰贰拾叁元肆角伍分”“负壹佰元整”“(负数)壹佰元整”。读不出时 None。"""
    if not raw:
        return None
    s = re.sub(r"[\s⊗ⓧ×（）()：:]", "", raw).replace("负数", "负").replace("圆", "元")
    neg = s.startswith("负")
    s = s.lstrip("负").rstrip("整正")
    if not s or any(ch not in CN_AMOUNT_CHARS for ch in s):
        return None
    if "元" in s:
        head, frac = s.split("元", 1)
    elif re.search(r"[角分]", s):  # 只有角分（“伍角”）
        head, frac = "", s
    else:
        return None
    integer = 0
    rest = head
    for unit, mul in (("亿", 10 ** 8), ("万", 10 ** 4)):
        if unit in rest:
            part, rest = rest.split(unit, 1)
            v = _cn_section(part) if part else 0
            if v is None:
                return None
            integer += v * mul
    if rest:
        v = _cn_section(rest)
        if v is None:
            return None
        integer += v
    jiao = fen = 0
    m = re.fullmatch(r"零?(?:(.)角)?零?(?:(.)分)?", frac)
    if m is None:
        return None
    if m.group(1):
        if m.group(1) not in _CN_DIGIT:
            return None
        jiao = _CN_DIGIT[m.group(1)]
    if m.group(2):
        if m.group(2) not in _CN_DIGIT:
            return None
        fen = _CN_DIGIT[m.group(2)]
    v = Decimal(integer) + Decimal(jiao) / 10 + Decimal(fen) / 100
    v = quantize(v)
    return -v if neg else v


def norm_date(y: str, m: str, d: str) -> str | None:
    try:
        yi, mi, di = int(y), int(m), int(d)
    except ValueError:
        return None
    if not (1990 <= yi <= 2100 and 1 <= mi <= 12 and 1 <= di <= 31):
        return None
    return f"{yi:04d}-{mi:02d}-{di:02d}"


@dataclass
class Qr:
    """票面二维码：01,种类码,代码,号码,金额,日期,校验码,末段（设计第八节实测）。cx、cy 为所在页面坐标。"""
    kind_code: str
    code: str
    number: str
    amount: Decimal | None
    date: str | None
    cx: float = 0.0
    cy: float = 0.0


@dataclass
class Invoice:
    kind: str = ""
    code: str = ""
    number: str = ""
    date: str = ""
    buyer_name: str = ""
    buyer_tax: str = ""
    seller_name: str = ""
    seller_tax: str = ""
    items: list[str] = field(default_factory=list)
    amount: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    total_cn: str = ""
    rates: list[str] = field(default_factory=list)
    remark: str = ""
    files: list[str] = field(default_factory=list)
    source: str = "text"
    low: list[str] = field(default_factory=list)  # OCR 置信度低的字段
    qr: Qr | None = None
    hints: list[str] = field(default_factory=list)

    @property
    def item_text(self) -> str:
        if not self.items:
            return ""
        return self.items[0] + (f" 等 {len(self.items)} 项" if len(self.items) > 1 else "")

    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("amount", "tax", "total"):
            d[k] = None if d[k] is None else str(d[k])
        if self.qr is not None:
            d["qr"]["amount"] = None if self.qr.amount is None else str(self.qr.amount)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Invoice":
        d = dict(d)
        for k in ("amount", "tax", "total"):
            d[k] = None if d.get(k) is None else Decimal(d[k])
        if d.get("qr"):
            q = dict(d["qr"])
            q["amount"] = None if q.get("amount") is None else Decimal(q["amount"])
            d["qr"] = Qr(**q)
        return cls(**d)


@dataclass
class Unrecognized:
    name: str
    reason: str
