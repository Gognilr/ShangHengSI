"""格式化信息与金额识别（SPEC 9.3 第 4、5 步）。

只做识别，返回在节点原文中的位置；代号分配和替换在 engine.py。
所有正则只在单个节点的原文上运行，不跨节点。
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.redaction.dictionaries import common_terms, find_country, cn_surnames
from app.redaction.parties import normalize


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    category: str  # 9.2 代码
    key: str  # 同一对象的标识：key 相同的命中使用同一代号（编号、账号、SWIFT、金额、单价）
    currency: str | None = None  # 规范币种代码（CNY、USD…），未知为 None；只有 find_amounts 填写
    value: Decimal | None = None  # 换算后的数值（“万元”已乘 10000），无法解析为 None；只有 find_amounts 填写


# ---------------------------------------------------------------- 第 4 步：格式化信息

# 填空括号（Q-134 ①）：模板中用【】等括起填写内容；标签与值、币种与数字之间各允许一个。
# 只认这几种，不含圆括号和书名号。_OPEN / _CLOSE 都可选，只吃同一行内的空格和制表符
FILL_OPEN = "【［[〖"
FILL_CLOSE = "】］]〗"
_OPEN = r"(?:[【［\[〖][ \t]*)?"
_CLOSE = r"(?:[ \t]*[】］\]〗])?"

_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_URL = re.compile(r"(?:https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'*+,;=%]+", re.IGNORECASE)
_URL_TRAILING = ".,;:!?'"
# IBAN：连写，或每 4 位一组以空格分隔（最后一组须含数字，避免把后面的币种代码吞进来）
_IBAN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[A-Z0-9]{11,30}|(?: [A-Z0-9]{4}){2,7}(?: (?=[A-Z0-9]{0,2}\d)[A-Z0-9]{1,3})?)"
    r"(?![A-Za-z0-9])"
)
_ACCOUNT = re.compile(
    r"(?:开户行账号|银行账号|收款账号|付款账号|账号|帐号|账户|帐户|(?i:account\s*(?:no\.?|number)|a/c\s*no\.?|a/c))"
    r"\s*[:：]?\s*" + _OPEN + r"(\d[\d \-]{6,45}\d)(?![\d])"
)
_SWIFT = re.compile(
    r"(?i:swift(?:\s*/\s*bic)?(?:\s*code)?|bic(?:\s*code)?)\s*[:：]?\s*" + _OPEN +
    r"([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)(?![A-Za-z0-9])"
)
_CREDIT_CODE = re.compile(r"(?<![0-9A-Za-z])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Za-z])")
_ID_CARD = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
_REFNO = re.compile(
    r"(?:提单编号|提单号|信用证编号|信用证号|报关单编号|报关单号|项目编号|项目号|招标项目编号|招标编号|报价单号|报价编号|运单号|集装箱号|"
    r"(?i:(?:B/L|Bill\s+of\s+Lading|L/C|Credit|Customs\s+Declaration|Project|Tender|Bid|Quotation|Quote|Waybill|Container)\s+No\.?)|"
    r"合同编号|合同号|订单编号|订单号|发票编号|发票号|"
    r"(?i:(?:contract|order|invoice|pi|p\.?\s?o\.?)\s*no\.?|(?:contract|order|invoice)\s*number))"
    r"\s*[:：]?\s*" + _OPEN + r"([A-Za-z0-9][A-Za-z0-9\-/_.]{1,48}[A-Za-z0-9])"
)
# 邮编（Q-139 ①，设计 R1.md 第十一节）：只认标签，不按“6 位数字”猜测。标签后须有冒号、空白或填空括号；
# 值为 3–10 个字母或数字，相邻两个之间可有一个空格或短横线（“100000-1234”“SW1A 1AA”），两端不是分隔符，至少含一个数字
_POSTCODE = re.compile(
    r"(?:邮政编码|邮编|(?<![A-Za-z])(?i:postal\s*code|post\s*code|zip\s*code|zip)(?![A-Za-z]))"
    r"(?:\s*[:：]\s*|\s+|(?=[【［\[〖]))" + _OPEN +
    r"([A-Za-z0-9](?:[ \-]?[A-Za-z0-9]){2,9})(?![A-Za-z0-9])"
)
_PHONE_INTL = re.compile(r"(?<![\w+])\+\d{1,3}[ -]?\d[\d -]{6,14}\d(?!\d)")
_PHONE_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_PHONE_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")

# V2-B-01：即时通信必须带冒号，避免将叙述中的账号猜作标签值（设计 1.5 #7）。
_IM = re.compile(
    r"(?:微信号|微信|QQ号|钉钉|(?<![A-Za-z])(?i:WeChat|QQ|WhatsApp|Skype|Telegram|LINE)(?![A-Za-z]))"
    r"[ \t]*[:：][ \t]*" + _OPEN + r"([A-Za-z0-9_.+\-]{3,40})(?![A-Za-z0-9_.+\-])"
)
_LABELED_ID = re.compile(
    r"(?:护照号码|护照号|营业执照注册号|工商注册号|注册号|组织机构代码|纳税人识别号|税号|"
    r"(?<![A-Za-z])(?i:Passport\s+No\.?|Organization\s+Code|Tax\s+ID|VAT\s+No\.?|TIN)(?![A-Za-z]))"
    r"\s*[:：]?\s*" + _OPEN + r"([A-Za-z0-9\-]{5,30})(?![A-Za-z0-9\-])"
)
_BANK_CODE = re.compile(
    r"(?:开户行行号|大额支付行号|联行号|行号|(?<![A-Za-z])(?i:CNAPS|Sort\s+Code|Routing\s+No\.?|ABA|IFSC)(?![A-Za-z]))"
    r"\s*[:：]?\s*" + _OPEN + r"([A-Za-z0-9]{6,15})(?![A-Za-z0-9])"
)


def _norm_key(value: str) -> str:
    return re.sub(r"[\s\-]", "", value).upper()


def find_formatted(text: str) -> list[Hit]:
    """第 4 步的全部命中（可能互相重叠，由调用方按顺序取舍）。顺序即优先级。"""
    hits: list[Hit] = []

    def add(start: int, end: int, category: str, key: str = "") -> None:
        hits.append(Hit(start, end, category, key))

    for m in _EMAIL.finditer(text):
        add(m.start(), m.end(), "EMAIL")
    for m in _URL.finditer(text):
        end = m.end()
        while end > m.start() and text[end - 1] in _URL_TRAILING:
            end -= 1
        add(m.start(), end, "URL")
    # 账号、SWIFT 按对象编号（Q-59）：账号的 key 为去掉空格和短横线后的大写字符（IBAN 与本地写法视为不同账号）；
    # SWIFT 的 key 为大写代码，11 位且末尾为 XXX 时与前 8 位视为同一代码
    for m in _IBAN.finditer(text):
        add(m.start(), m.end(), "ACCOUNT", _norm_key(m.group()))
    for m in _ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if 8 <= len(digits) <= 30:
            add(m.start(1), m.end(1), "ACCOUNT", _norm_key(m.group(1)))
    for m in _SWIFT.finditer(text):
        code = m.group(1).upper()
        add(m.start(1), m.end(1), "SWIFT", code[:8] if len(code) == 11 and code.endswith("XXX") else code)
    for m in _BANK_CODE.finditer(text):
        add(m.start(1), m.end(1), "SWIFT", m.group(1).upper())
    for m in _LABELED_ID.finditer(text):
        if sum(c.isdigit() for c in m.group(1)) >= 2:
            add(m.start(1), m.end(1), "IDNO")
    for m in _IM.finditer(text):
        add(m.start(1), m.end(1), "IM")
    for m in _CREDIT_CODE.finditer(text):
        add(m.start(), m.end(), "IDNO")
    for m in _ID_CARD.finditer(text):
        add(m.start(), m.end(), "IDNO")
    for m in _REFNO.finditer(text):
        value = m.group(1).rstrip(".")
        if any(c.isdigit() for c in value):
            add(m.start(1), m.start(1) + len(value), "REFNO", _norm_key(value))
    for m in _POSTCODE.finditer(text):
        # 去掉末尾不含数字的词：“ZIP: 10001 New York”只取“10001”
        groups = m.group(1).split(" ")
        while groups and not any(c.isdigit() for c in groups[-1]):
            groups.pop()
        value = " ".join(groups).rstrip(" -")
        if len(value) >= 3:
            add(m.start(1), m.start(1) + len(value), "POSTCODE")
    for pattern in (_PHONE_INTL, _PHONE_MOBILE, _PHONE_LANDLINE):
        for m in pattern.finditer(text):
            add(m.start(), m.end(), "PHONE")
    return hits


# ---------------------------------------------------------------- 第 5 步：金额

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# 币种 → 规范代码；同一数值、同一币种使用同一代号
_CURRENCY = {
    "RMB": "CNY", "CNY": "CNY", "人民币": "CNY", "¥": "CNY", "￥": "CNY", "元": "CNY", "万元": "CNY", "亿元": "CNY",
    "USD": "USD", "US$": "USD", "$": "USD", "美元": "USD", "万美元": "USD",
    "EUR": "EUR", "€": "EUR", "欧元": "EUR", "GBP": "GBP", "£": "GBP", "英镑": "GBP",
    "JPY": "JPY", "日元": "JPY", "HKD": "HKD", "HK$": "HKD", "港元": "HKD", "港币": "HKD",
    "AED": "AED", "SAR": "SAR", "TRY": "TRY", "INR": "INR", "AUD": "AUD", "CAD": "CAD", "CHF": "CHF",
    "SGD": "SGD", "KRW": "KRW", "RUB": "RUB",
}
_CODES = "RMB|CNY|USD|EUR|JPY|GBP|HKD|AED|SAR|TRY|INR|AUD|CAD|CHF|SGD|KRW|RUB"
_PREFIX = re.compile(
    rf"(?:(?<![A-Za-z])(?:US\$|HK\$|{_CODES})(?![A-Za-z])|人民币|美元|欧元|[¥￥$€£])\s?{_OPEN}({_NUM})(?![\d,.]*\d)"
)
_SUFFIX = re.compile(
    # 后置币种不含“人民币”：避免把“2.3 人民币参考价”这类条款编号当成金额
    # 数字前不能是下划线或字母：外发文本中代号“[金额_1]元”的序号不是金额（出口复检的重扫用同一函数）
    rf"(?<![\d.,_A-Za-z])({_NUM}){_CLOSE}\s?(万美元|万元|亿元|美元|欧元|英镑|日元|港元|港币|元|(?:{_CODES})(?![A-Za-z]))"
)
_FOLLOWED_BY_NUMBER = re.compile(r"\s?\d")
_SUFFIX_AFTER_PREFIX = re.compile(_CLOSE + r"\s?(万美元|万元|亿元|美元|元)")
_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UPPER = re.compile(
    r"[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*[壹贰叁肆伍陆柒捌玖][零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*[元圆]"
    r"(?:[零壹贰叁肆伍陆柒捌玖][角分])*整?"
    r"|[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*[壹贰叁肆伍陆柒捌玖][零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*(?:[零壹贰叁肆伍陆柒捌玖][角分])+整?"
    # 填空括号内的大写、“元”在括号外：“人民币【贰拾贰万贰仟零陆拾叁】元整”只替换括号内的部分（Q-134 ①）
    r"|[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*[壹贰叁肆伍陆柒捌玖][零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*(?:[零壹贰叁肆伍陆柒捌玖][角分])*"
    r"(?=[ \t]*[】］\]〗][ \t]*[元圆])"
)
# 金额后紧跟“/吨、/MT、/kg、每吨”等计量单位时归为单价
_PER_UNIT = re.compile(_CLOSE + r"\s*(?:/|每)\s*(?:吨|公斤|千克|个|件|套|台|箱|桶|米|批|[A-Za-z]{1,5}\b)")
_BARE_NUMBER = re.compile(rf"^\s*({_NUM})\s*$")
_MULTIPLIER = {"万元": Decimal(10000), "万美元": Decimal(10000), "亿元": Decimal(100000000)}


def _decimal(num: str) -> Decimal | None:
    try:
        return Decimal(num.replace(",", ""))
    except InvalidOperation:
        return None


def chinese_upper_value(text: str) -> Decimal | None:
    """中文大写金额 → 数值，如“捌拾万元整”→ 800000。无法解析时返回 None。"""
    units = {"拾": 10, "佰": 100, "仟": 1000}
    total = Decimal(0)
    section = Decimal(0)  # 当前万、亿以下的部分
    digit = Decimal(0)
    fraction = Decimal(0)
    i = 0
    seen = False
    while i < len(text):
        ch = text[i]
        if ch in _CN_DIGITS:
            digit = Decimal(_CN_DIGITS.index(ch))
            seen = seen or digit > 0
            if i + 1 < len(text) and text[i + 1] in "角分":
                fraction += digit * (Decimal("0.1") if text[i + 1] == "角" else Decimal("0.01"))
                digit = Decimal(0)
                i += 2
                continue
        elif ch in units:
            seen = True
            section += (digit if digit else Decimal(1)) * units[ch]
            digit = Decimal(0)
        elif ch == "万":
            total += (section + digit) * 10000
            section = digit = Decimal(0)
        elif ch == "亿":
            total = (total + section + digit) * 100000000
            section = digit = Decimal(0)
        elif ch in "元圆":
            total += section + digit
            section = digit = Decimal(0)
        elif ch == "整":
            pass
        else:
            return None
        i += 1
    total += section + digit + fraction
    return total if seen else None


def _amount_key(category: str, currency: str | None, value: Decimal | None, surface: str) -> str:
    if value is None:
        return f"{category}|{currency or ''}|{surface}"
    return f"{category}|{currency or ''}|{value.normalize():f}"


# R4：无币种金额只供识别入口使用；出口复检仍调用 find_amounts，不重扫模型历史回复。
AMOUNT_LABELS = (
    "合同总价", "合同总额", "合同金额", "合同价款", "合同价", "总价款", "总价", "总金额", "总额", "金额", "价款", "货款",
    "报价", "单价", "含税单价", "不含税单价", "含税价", "不含税价", "合计", "总计", "小计", "预付款", "首付款", "定金",
    "订金", "进度款", "尾款", "质保金", "保证金", "履约保证金", "违约金", "服务费", "费用", "预算", "投资额", "采购额", "销售额", "税额",
    "Total Amount", "Total Price", "Total Value", "Contract Value", "Contract Price", "Contract Amount", "Amount", "Total",
    "Subtotal", "Unit Price", "Price", "Deposit", "Advance Payment", "Down Payment", "Balance", "Retention",
)
_BARE_LABEL_RE = re.compile("|".join(re.escape(w).replace(r"\ ", r"[ \t]+") for w in sorted(AMOUNT_LABELS, key=len, reverse=True)), re.IGNORECASE)
_BARE_UNIT = "|".join(re.escape(w) for w in sorted(_CURRENCY, key=len, reverse=True))
_BARE_LABEL_TAIL = re.compile(rf"[ \t　]*(?:[（(][ \t]*(?P<unit>{_BARE_UNIT})[ \t]*[）)])?[ \t　]*[:：]?[ \t　]*$", re.IGNORECASE)
_BARE_VALUE = re.compile(
    rf"[ \t　]*(?:[（(][ \t]*(?P<unit>{_BARE_UNIT})[ \t]*[）)])?[ \t　]*"
    rf"[为是:：]?[ \t　]*(?:不超过|不低于|共计|合计|约|计)?[ \t　]*{_OPEN}"
    rf"(?P<num>{_NUM})(?![\d,.]*\d)(?P<scale>[万亿])?{_CLOSE}", re.IGNORECASE)
_BARE_BAD_TAIL = re.compile(r"[A-Za-z0-9一-鿿%‰]")
_BARE_SPACED_UNIT = re.compile(r"[ \t　]+(?:[A-Za-z%‰]|平方米|立方米|个月|小时|公斤|千克|天|日|月|年|周|台|套|件|个|只|项|条|次|批|吨|克|米|人|家|份|页)")


def amount_label_kind(text: str) -> str | None:
    """紧邻左格必须完整等于金额标签（可有单位说明和末尾冒号）。"""
    compact = re.sub(r"\s+", "", text)
    for label in sorted(AMOUNT_LABELS, key=len, reverse=True):
        key = re.sub(r"\s+", "", label)
        if compact[:len(key)].casefold() == key.casefold() and _BARE_LABEL_TAIL.fullmatch(compact[len(key):]):
            return "PRICE" if "单价" in label or "price" in label.casefold() else "AMOUNT"
    return None


def find_bare_amounts(text: str) -> list[Hit]:
    """金额标签后的数字；只替换数字，保留单位与万/亿，币种规则优先（R4 3.2）。"""
    hits: list[Hit] = []
    existing = find_amounts(text)
    for label in _BARE_LABEL_RE.finditer(text):
        if label.start() and re.match(r"[第\dA-Za-z]", text[label.start() - 1]):
            continue
        m = _BARE_VALUE.match(text, label.end())
        if m is None or _BARE_BAD_TAIL.match(text, m.end()) or _BARE_SPACED_UNIT.match(text, m.end()):
            continue
        start, end = m.span("num")
        if any(h.start < end and start < h.end for h in existing):
            continue
        num, scale = m.group("num"), m.group("scale")
        if not (len(num.split(".")[0].replace(",", "")) >= 3 or "," in num
                or re.search(r"\.\d{2}$", num) or scale):
            continue
        value = _decimal(num)
        if value is not None and scale:
            value *= Decimal(10000 if scale == "万" else 100000000)
        unit = m.group("unit")
        currency = _CURRENCY.get(unit.upper() if unit and unit.isascii() else unit)
        cat = "PRICE" if "单价" in label.group() or "price" in label.group().casefold() else "AMOUNT"
        if value != 0:
            hits.append(Hit(start, end, cat, _amount_key(cat, currency, value, num), currency, value))
    return hits


def find_amounts(text: str, column: str | None = None) -> list[Hit]:
    """第 5 步：金额与单价。只替换数字部分，币种文字保留（9.2“币种保留”）。

    column 为表格列提示：PRICE（单价列）或 AMOUNT（金额列）；该列中带币种的金额按列归类，
    整格只有一个数字时也按列归为单价或金额。
    """
    hits: list[Hit] = []

    def category(end: int) -> str:
        if column == "PRICE" or _PER_UNIT.match(text, end):
            return "PRICE"
        return "AMOUNT"

    for m in _PREFIX.finditer(text):
        cur_text = text[m.start():m.start(1)].strip().rstrip(FILL_OPEN).strip()
        currency = _CURRENCY.get(cur_text)
        value = _decimal(m.group(1))
        tail = _SUFFIX_AFTER_PREFIX.match(text, m.end(1))
        end_for_unit = m.end(1)
        if tail:
            if value is not None and tail.group(1) in _MULTIPLIER:
                value *= _MULTIPLIER[tail.group(1)]
            end_for_unit = tail.end()
        cat = category(end_for_unit)
        hits.append(Hit(m.start(1), m.end(1), cat, _amount_key(cat, currency, value, m.group(1)), currency, value))
    for m in _SUFFIX.finditer(text):
        suffix = m.group(2)
        if _FOLLOWED_BY_NUMBER.match(text, m.end()):  # “3.1 USD 100”：USD 是后一个数字的前置币种
            continue
        value = _decimal(m.group(1))
        if value is not None and suffix in _MULTIPLIER:
            value *= _MULTIPLIER[suffix]
        cat = category(m.end())
        currency = _CURRENCY.get(suffix)
        hits.append(Hit(m.start(1), m.end(1), cat, _amount_key(cat, currency, value, m.group(1)), currency, value))
    for m in _CN_UPPER.finditer(text):
        cat = category(m.end())
        value = chinese_upper_value(m.group())
        hits.append(Hit(m.start(), m.end(), cat, _amount_key(cat, "CNY", value, m.group()), "CNY", value))
    if column in ("PRICE", "AMOUNT"):
        bare = re.fullmatch(rf"\s*({_NUM})(万)?\s*", text)
        if bare:
            value = _decimal(bare.group(1))
            if value is not None and bare.group(2):
                value *= 10000
            hits.append(Hit(bare.start(1), bare.end(1), column, _amount_key(column, None, value, bare.group(1)),
                            None, value))
    return [hit for hit in hits if hit.value != 0]


# ---------------------------------------------------------------- 第 6 步：人名（M4-01，设计 M4.md 2.1）

# 后接冒号或空格均可的标签 / 必须有冒号的标签（“签字盖章”“Contact us”“Name of Buyer”这类不是标签）
_PERSON_LABELS_OPT = (
    "法定代表人", "委托代理人", "授权代表", "签字人", "联系人", "经办人", "收件人", "代表人",
    r"(?i:attention)", r"(?i:attn\.?)", r"(?i:signed\s+by)",
)
_PERSON_LABELS_COLON = (
    "签字", "签名", r"(?i:contact\s+person)", r"(?i:contact)", r"(?i:signature)", r"(?i:name)", r"(?i:representative)",
)
# 标签后可有一组短的括号说明再接冒号：“法定代表人（签字）：”“授权代表(签章)：”（M4-04 评审补充，Q-76）
_LABEL_NOTE = r"[ \t]*[（(][^）)\n]{1,6}[）)]"
_PERSON_LABEL = re.compile(
    r"(?<![A-Za-z])(?:(?:" + "|".join(_PERSON_LABELS_OPT) + r")(?:" + _LABEL_NOTE + r"[ \t]*[:：]|[ \t]*[:：]?)"
    r"|(?:" + "|".join(_PERSON_LABELS_COLON) + r")(?:" + _LABEL_NOTE + r")?[ \t]*[:：])[ \t]*" + _OPEN
)
_CN_TITLES = ("总经理", "董事长", "先生", "女士", "小姐", "经理", "主任", "总裁", "代表")  # 长的在前
_CN_PERSON_CUT = ("电话", "手机", "传真", "邮箱", "邮件", "职务", "职位", "部门", "地址")
_CN_NOT_PERSON_END = set("部处室科组厅局院所司队厂")  # “采购部”“财务科”不是人名
_CN_NOT_PERSON = frozenset({"同上", "同前", "见下", "待定", "略", "不详", "本人", "本公司", "我司", "贵司", "对方",
                            "签署", "签字", "签章", "盖章", "姓名", "名称", "日期", "电话", "授权", "本单位", "法人",
                            "代理人", "委托人"})
# 人名标签后以这些词开头的汉字串是另一个标签或签字栏用语，不是人名（Q-134 ②）
_CN_NOT_NAME_START = ("电话", "手机", "传真", "邮箱", "邮件", "地址", "姓名", "名称", "职务", "职位", "日期", "签字",
                      "签章", "签名", "签署", "盖章", "授权", "委托", "代表", "法定", "联系", "经办", "收件", "代理")
_EN_TITLES = re.compile(r"(?:Mr|Mrs|Ms|Miss|Dr|Prof)(?:\.[ \t]*|[ \t]+)", re.IGNORECASE)
_EN_WORD = re.compile(r"[A-Z][A-Za-z'’\-]*\.?")
# 职务、部门词和信息标签：遇到即停止（小写形式比较）
_EN_STOP_WORDS = frozenset(w.casefold() for w in (
    "Manager", "Director", "Sales", "Purchasing", "Procurement", "Department", "Dept", "Team", "Officer", "Executive",
    "Engineer", "President", "Chairman", "Chairwoman", "CEO", "CFO", "COO", "GM", "Assistant", "Supervisor", "Representative",
    "Agent", "Head", "Chief", "Finance", "Accounts", "Accounting", "Logistics", "Marketing", "Export", "Import", "Legal",
    "Tel", "Telephone", "Phone", "Fax", "Mobile", "Cell", "Email", "E-mail", "Ext",
))
# 英文企业后缀词：名称里出现这些词说明标签后是公司名（“Company Name: ABC Ltd”），整个不当作人名
_EN_COMPANY_WORDS = frozenset(w.casefold() for w in (
    "Co", "Ltd", "Inc", "Corp", "Corporation", "Company", "LLC", "LLP", "PLC", "Limited", "GmbH", "Pte", "Pvt", "Sdn", "Bhd",
))


def _strip_cn_titles(run: str) -> str:
    changed = True
    while changed:
        changed = False
        for title in _CN_TITLES:
            if run.endswith(title) and len(run) > len(title):
                run = run[:-len(title)]
                changed = True
                break
    return run


def find_persons(text: str) -> list[Hit]:
    """第 6 步：人名标签之后的人名。中文 2–4 个汉字（去掉末尾称谓后），英文 1–3 个首字母大写的单词。

    不能确定时不识别（不猜测）。标签后紧接公司名称的情况由 engine 的占位处理（公司名已先被替换）。
    """
    hits: list[Hit] = []
    seen: set[tuple[int, int]] = set()
    terms = [t.casefold() for t in common_terms()]
    for m in _PERSON_LABEL.finditer(text):
        # Project Name 是项目标签，不能把其中 Name 当成人名标签抢先识别。
        if re.search(r"(?i:Project)[ \t]+$", text[:m.start()]) and m.group().lower().startswith("name"):
            continue
        pos = m.end()
        span: tuple[int, int] | None = None
        label = m.group()
        # 不带冒号的标签与人名之间须有空白或填空括号：“授权代表应签署”“联系人电话：”不是“标签 + 人名”（Q-134 ②）
        if not any(c in label for c in ":：") and label[-1] not in " \t" + FILL_OPEN:
            continue
        han = re.compile(r"[一-鿿]+").match(text, pos)
        if han:
            run = han.group()
            # 标签后接的是另一个标签（“授权代表：（空白）授权代表：”“联系人：电话：”）：以标签词开头，
            # 或整串（没有在“电话、手机”等词处截断时）后面紧跟冒号。“周示例电话：…”截断后冒号属于“电话”，仍是人名
            if run.startswith(_CN_NOT_NAME_START):
                continue
            for cut in _CN_PERSON_CUT:
                i = run.find(cut, 1)
                if i > 0:
                    run = run[:i]
            if run == han.group() and re.match(r"[ \t]*[:：]", text[han.end():han.end() + 3]):
                continue
            run = _strip_cn_titles(run)
            if (2 <= len(run) <= 4 and run[-1] not in _CN_NOT_PERSON_END and run not in _CN_NOT_PERSON
                    and "公司" not in run):
                span = (pos, pos + len(run))
        else:
            while (t := _EN_TITLES.match(text, pos)):
                pos = t.end()
            words: list[re.Match] = []
            cur = pos
            while len(words) < 3:
                w = _EN_WORD.match(text, cur)
                if w is None:
                    break
                token = w.group().rstrip(".")
                if token.casefold() in _EN_COMPANY_WORDS:
                    words = []
                    break
                rest = text[w.start():w.start() + 60].casefold()
                if (token.casefold() in _EN_STOP_WORDS or text[w.end():w.end() + 1] == ":"
                        or any(rest.startswith(t) and not rest[len(t):len(t) + 1].isalnum() for t in terms)):
                    break
                words.append(w)
                nxt = re.compile(r"[ \t]+").match(text, w.end())
                if nxt is None or w.group().endswith(".") and len(w.group()) > 2:
                    break
                cur = nxt.end()
            if words:
                end = words[-1].end()
                if text[end - 1] == "." and len(words[-1].group()) > 2:  # 句末的句号不属于姓名；“J.”这样的缩写保留
                    end -= 1
                span = (words[0].start(), end)
        if span and span not in seen:
            seen.add(span)
            hits.append(Hit(span[0], span[1], "PERSON", normalize(text[span[0]:span[1]])))
    # V2-B-03：独立称谓入口；原有标签识别的结果不变。
    titles = "总经理 副总经理 董事长 总监 经理 主任 主管 总工 工程师 律师 会计 老师 先生 女士 小姐 局长 处长 科长 院长 校长 书记 部长 总 工".split()
    pattern = re.compile("(?:" + "|".join(map(re.escape, cn_surnames())) + ")(?:"
                         + "|".join(sorted(titles, key=len, reverse=True)) + ")")
    left = ("请", "由", "与", "和", "及", "致", "向", "给", "找", "跟", "同", "经", "抄送", "联系", "通知", "委托", "授权")
    for m in pattern.finditer(text):
        if m.start() and re.match(r"[一-鿿]", text[m.start() - 1]) and not text[:m.start()].endswith(left):
            continue
        if not any(h.start < m.end() and m.start() < h.end for h in hits):
            hits.append(Hit(m.start(), m.end(), "PERSON", normalize(m.group())))
    for m in re.finditer(r"(?<![A-Za-z])(?:Mr|Mrs|Ms|Dr|Prof)\.[ \t]+([A-Z][A-Za-z'’\-]*(?:[ \t]+[A-Z][A-Za-z'’\-]*){0,2})", text):
        if not any(h.start < m.end(1) and m.start(1) < h.end for h in hits):
            hits.append(Hit(m.start(1), m.end(1), "PERSON", normalize(m.group(1))))
    return hits


# ---------------------------------------------------------------- 第 7 步：地址（M4-01）

_ADDRESS_LABEL = re.compile(
    r"(?:注册地址|通讯地址|办公地址|联系地址|收货地址|地址|(?<![A-Za-z])(?i:registered\s+address|address|add\.?))[ \t]*[:：]"
)
_INFO_LABEL = re.compile(
    r"(?:电话|手机|传真|邮箱|联系人|(?<![A-Za-z])(?i:tel|phone|fax|e-?mail|contact|attn)\.?)[ \t]*[:：]"
)
_ADDRESS_EDGE = " \t,，;；、.。:：\r" + FILL_OPEN + FILL_CLOSE
_CN_UNLABELED_ADDRESS = re.compile(
    r"(?:[一-鿿]{2,7}(?:省|自治区|市|自治州|地区|旗)|[一-鿿]{1,7}(?:区|县))+"
    r"[一-鿿0-9]{1,20}(?:大道|大街|胡同|路|街|道|巷|弄)[ \t]*\d{1,5}[ \t]*号"
    r"(?:[ \t]*[A-Za-z0-9一二三四五六七八九十\-]{1,10}[ \t]*(?:号楼|单元|栋|幢|座|楼|层|室))*"
)
# V2Q-45：无标签英文地址须有门牌+道路词，或套间/楼层+逗号分隔的城市/国家；
# 仅接受同一行内的地址组成部分，不把独立地名、港口或后续叙述并入地址。
_EN_UNIT = r"(?i:Suite|Floor|Unit|Room)[ \t]+[A-Za-z0-9-]{1,10}"
_EN_PLACE = r"[A-Z][a-z]+(?:[ \t-]+[A-Z][a-z]+){0,3}"
_EN_STREET = (r"\d{1,5}[ \t]+(?:[A-Z][A-Za-z'’-]*[ \t]+){1,6}"
              r"(?i:Street|St|Road|Rd|Lane|Ln|Quay|Avenue|Ave|Drive|Dr|Way|Boulevard|Blvd|Court|Ct)\b")
_EN_UNLABELED_ADDRESS = re.compile(
    r"(?<![\w])(?:" + _EN_STREET + r"(?:,[ \t]*(?:" + _EN_UNIT + r"|" + _EN_PLACE + r")){0,3}"
    r"|" + _EN_UNIT + r",[ \t]*" + _EN_PLACE + r"(?:,[ \t]*" + _EN_PLACE + r"){0,2})"
)


def find_addresses(text: str) -> list[Hit]:
    """第 7 步：地址标签（须有冒号）之后到行尾；遇到下一个信息标签、地址标签或带值的邮编标签时提前结束。key 为国家。"""
    hits: list[Hit] = []
    for m in _ADDRESS_LABEL.finditer(text):
        line_end = text.find("\n", m.end())
        limit = len(text) if line_end < 0 else line_end
        stop = [x.start() for x in (_INFO_LABEL.search(text, m.end(), limit), _ADDRESS_LABEL.search(text, m.end(), limit),
                                    _POSTCODE.search(text, m.end(), limit)) if x]
        end = min([limit, *stop])
        start = m.end()
        while start < end and text[start] in _ADDRESS_EDGE:
            start += 1
        while end > start and text[end - 1] in _ADDRESS_EDGE:
            end -= 1
        if end > start:
            hits.append(Hit(start, end, "ADDR", find_country(text[start:end]) or "国家未知"))
    for m in _CN_UNLABELED_ADDRESS.finditer(text):
        if not any(h.start < m.end() and m.start() < h.end for h in hits):
            # 不把叙述前缀算进行政区名称，如“货物运至江苏省”“乙方在东川市”。
            head = re.match(r".*?(?:省|自治区|市|自治州|地区|区|县|旗)", m.group()).group()
            boundary = list(re.finditer(r"运至|送至|交至|寄往|寄至|发往|送往|位于|坐落于|在|至", head))
            start = m.start() + boundary[-1].end() if boundary else m.start()
            hits.append(Hit(start, m.end(), "ADDR", "中国"))
    for m in _EN_UNLABELED_ADDRESS.finditer(text):
        surface = m.group()
        tail = surface.partition(',')[2]
        # Opus复核：套间/楼层后的大写词组未必是地名；Court/Way亦常见于普通句子。
        unit_only = re.match(_EN_UNIT, surface) is not None
        ambiguous_road = re.search(r'(?i:\bCourt|\bCt|\bWay)$', surface.partition(',')[0]) is not None
        if (unit_only or ambiguous_road) and (not tail or find_country(tail) is None):
            continue
        if not any(h.start < m.end() and m.start() < h.end for h in hits):
            hits.append(Hit(m.start(), m.end(), 'ADDR', find_country(m.group()) or '国家未知'))
    return hits


_PROJECT_LABEL = re.compile(r"(?:项目名称|工程名称|项目全称|(?<![A-Za-z])(?i:Project\s+Name|Project\s+Title))[ \t]*[:：][ \t]*" + _OPEN)
_PROJECT_EMPTY = frozenset({"见附件", "另行约定", "待定", "同上"})


def find_projects(text: str) -> list[Hit]:
    """项目名称在地址之后识别；保留信息标签和句尾分隔符。"""
    hits = []
    for m in _PROJECT_LABEL.finditer(text):
        stop = re.search(r"[\n；;。]", text[m.end():])
        end = m.end() + stop.start() if stop else len(text)
        labels = [x.start() for x in (_INFO_LABEL.search(text, m.end(), end),
                  _ADDRESS_LABEL.search(text, m.end(), end), _PROJECT_LABEL.search(text, m.end(), end),
                  _POSTCODE.search(text, m.end(), end)) if x]
        end = min([end, *labels])
        value = text[m.end():end].rstrip(_ADDRESS_EDGE)
        if 2 <= len(value) <= 60 and value not in _PROJECT_EMPTY:
            hits.append(Hit(m.end(), m.end() + len(value), "PROJECT", normalize(value)))
    return hits


# ---------------------------------------------------------------- 数量（M4-01，仅在“数量脱敏”开启时由 engine 调用）

_QTY_UNITS = (
    "万吨", "吨", "公斤", "千克", "件", "套", "台", "箱", "桶", "个", "批",
    "MT", "kg", "pcs", "sets", "units", "cartons", "bags", "drums",
)
_QTY_UNIT_RE = "|".join(sorted(_QTY_UNITS, key=len, reverse=True))
# “3 个工作日”“2个月”是期限不是数量
_QTY = re.compile(
    rf"(?<![\d.,])({_NUM})[ \t]?((?i:{_QTY_UNIT_RE}))(?![A-Za-z])"
    r"(?!月|工作日|营业日|自然日|银行日|工作|星期|周|日|天|小时|季度|年|百分点)"
)


def _qty_key(unit: str, num: str) -> str:
    value = _decimal(num)
    shown = format(value.normalize(), "f") if value is not None else num
    return f"QTY|{unit.casefold()}|{shown}"


def find_quantities(text: str, column: str | None = None) -> list[Hit]:
    """数字（可含千分位和小数）后紧跟计量单位；只替换数字，单位保留。column="QTY" 时整格只有一个数字也算数量。"""
    hits = [Hit(m.start(1), m.end(1), "QTY", _qty_key(m.group(2), m.group(1))) for m in _QTY.finditer(text)]
    if not hits and column == "QTY":
        bare = _BARE_NUMBER.match(text)
        if bare:
            hits.append(Hit(bare.start(1), bare.end(1), "QTY", _qty_key("", bare.group(1))))
    return hits
