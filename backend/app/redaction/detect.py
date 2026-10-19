"""格式化信息与金额识别（SPEC 9.3 第 4、5 步）。

只做识别，返回在节点原文中的位置；代号分配和替换在 engine.py。
所有正则只在单个节点的原文上运行，不跨节点。
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.redaction.dictionaries import common_terms, find_country
from app.redaction.parties import normalize


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    category: str  # 9.2 代码
    key: str  # 同一对象的标识：key 相同的命中使用同一代号（编号、金额、单价）
    currency: str | None = None  # 规范币种代码（CNY、USD…），未知为 None；只有 find_amounts 填写
    value: Decimal | None = None  # 换算后的数值（“万元”已乘 10000），无法解析为 None；只有 find_amounts 填写


# ---------------------------------------------------------------- 第 4 步：格式化信息

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
    r"\s*[:：]?\s*(\d[\d \-]{6,45}\d)(?![\d])"
)
_SWIFT = re.compile(
    r"(?i:swift(?:\s*/\s*bic)?(?:\s*code)?|bic(?:\s*code)?)\s*[:：]?\s*"
    r"([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)(?![A-Za-z0-9])"
)
_CREDIT_CODE = re.compile(r"(?<![0-9A-Za-z])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Za-z])")
_ID_CARD = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
_REFNO = re.compile(
    r"(?:合同编号|合同号|订单编号|订单号|发票编号|发票号|"
    r"(?i:(?:contract|order|invoice|pi|p\.?\s?o\.?)\s*no\.?|(?:contract|order|invoice)\s*number))"
    r"\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-/_.]{1,48}[A-Za-z0-9])"
)
_PHONE_INTL = re.compile(r"(?<![\w+])\+\d{1,3}[ -]?\d[\d -]{6,14}\d(?!\d)")
_PHONE_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_PHONE_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")


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
    for m in _IBAN.finditer(text):
        add(m.start(), m.end(), "ACCOUNT")
    for m in _ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if 8 <= len(digits) <= 30:
            add(m.start(1), m.end(1), "ACCOUNT")
    for m in _SWIFT.finditer(text):
        add(m.start(1), m.end(1), "SWIFT")
    for m in _CREDIT_CODE.finditer(text):
        add(m.start(), m.end(), "IDNO")
    for m in _ID_CARD.finditer(text):
        add(m.start(), m.end(), "IDNO")
    for m in _REFNO.finditer(text):
        value = m.group(1).rstrip(".")
        if any(c.isdigit() for c in value):
            add(m.start(1), m.start(1) + len(value), "REFNO", _norm_key(value))
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
    rf"(?:(?<![A-Za-z])(?:US\$|HK\$|{_CODES})(?![A-Za-z])|人民币|美元|欧元|[¥￥$€£])\s?({_NUM})(?![\d,.]*\d)"
)
_SUFFIX = re.compile(
    # 后置币种不含“人民币”：避免把“2.3 人民币参考价”这类条款编号当成金额
    rf"(?<![\d.,])({_NUM})\s?(万美元|万元|亿元|美元|欧元|英镑|日元|港元|港币|元|(?:{_CODES})(?![A-Za-z]))"
)
_FOLLOWED_BY_NUMBER = re.compile(r"\s?\d")
_SUFFIX_AFTER_PREFIX =re.compile(r"\s?(万美元|万元|亿元|美元|元)")
_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UPPER = re.compile(
    r"[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*[壹贰叁肆伍陆柒捌玖][零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*[元圆]"
    r"(?:[零壹贰叁肆伍陆柒捌玖][角分])*整?"
    r"|[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*[壹贰叁肆伍陆柒捌玖][零壹贰叁肆伍陆柒捌玖拾佰仟万亿]*(?:[零壹贰叁肆伍陆柒捌玖][角分])+整?"
)
# 金额后紧跟“/吨、/MT、/kg、每吨”等计量单位时归为单价
_PER_UNIT = re.compile(r"\s*(?:/|每)\s*(?:吨|公斤|千克|个|件|套|台|箱|桶|米|批|[A-Za-z]{1,5}\b)")
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
        cur_text = text[m.start():m.start(1)].strip()
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
        bare = _BARE_NUMBER.match(text)
        if bare:
            value = _decimal(bare.group(1))
            hits.append(Hit(bare.start(1), bare.end(1), column, _amount_key(column, None, value, bare.group(1)),
                            None, value))
    return hits


# ---------------------------------------------------------------- 第 6 步：人名（M4-01，设计 M4.md 2.1）

# 后接冒号或空格均可的标签 / 必须有冒号的标签（“签字盖章”“Contact us”“Name of Buyer”这类不是标签）
_PERSON_LABELS_OPT = (
    "法定代表人", "委托代理人", "授权代表", "签字人", "联系人", "经办人", "收件人", "代表人",
    r"(?i:attention)", r"(?i:attn\.?)", r"(?i:signed\s+by)",
)
_PERSON_LABELS_COLON = (
    "签字", "签名", r"(?i:contact\s+person)", r"(?i:contact)", r"(?i:signature)", r"(?i:name)", r"(?i:representative)",
)
_PERSON_LABEL = re.compile(
    r"(?<![A-Za-z])(?:(?:" + "|".join(_PERSON_LABELS_OPT) + r")[ \t]*[:：]?|(?:" + "|".join(_PERSON_LABELS_COLON) + r")[ \t]*[:：])[ \t]*"
)
_CN_TITLES = ("总经理", "董事长", "先生", "女士", "小姐", "经理", "主任", "总裁", "代表")  # 长的在前
_CN_PERSON_CUT = ("电话", "手机", "传真", "邮箱", "邮件", "职务", "职位", "部门", "地址")
_CN_NOT_PERSON_END = set("部处室科组厅局院所司队厂")  # “采购部”“财务科”不是人名
_CN_NOT_PERSON = frozenset({"同上", "同前", "见下", "待定", "略", "不详", "本人", "本公司", "我司", "贵司", "对方"})
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
        pos = m.end()
        span: tuple[int, int] | None = None
        han = re.compile(r"[一-鿿]+").match(text, pos)
        if han:
            run = han.group()
            for cut in _CN_PERSON_CUT:
                i = run.find(cut, 1)
                if i > 0:
                    run = run[:i]
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
    return hits


# ---------------------------------------------------------------- 第 7 步：地址（M4-01）

_ADDRESS_LABEL = re.compile(
    r"(?:注册地址|通讯地址|办公地址|联系地址|收货地址|地址|(?<![A-Za-z])(?i:registered\s+address|address|add\.?))[ \t]*[:：]"
)
_INFO_LABEL = re.compile(
    r"(?:电话|手机|传真|邮箱|联系人|(?<![A-Za-z])(?i:tel|phone|fax|e-?mail|contact|attn)\.?)[ \t]*[:：]"
)
_ADDRESS_EDGE = " \t,，;；、.。:：\r"


def find_addresses(text: str) -> list[Hit]:
    """第 7 步：地址标签（须有冒号）之后到行尾；遇到下一个信息标签或地址标签时提前结束。key 为国家。"""
    hits: list[Hit] = []
    for m in _ADDRESS_LABEL.finditer(text):
        line_end = text.find("\n", m.end())
        limit = len(text) if line_end < 0 else line_end
        stop = [x.start() for x in (_INFO_LABEL.search(text, m.end(), limit), _ADDRESS_LABEL.search(text, m.end(), limit)) if x]
        end = min([limit, *stop])
        start = m.end()
        while start < end and text[start] in _ADDRESS_EDGE:
            start += 1
        while end > start and text[end - 1] in _ADDRESS_EDGE:
            end -= 1
        if end > start:
            hits.append(Hit(start, end, "ADDR", find_country(text[start:end]) or "国家未知"))
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
