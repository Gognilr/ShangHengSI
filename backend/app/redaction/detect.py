"""格式化信息与金额识别（SPEC 9.3 第 4、5 步）。

只做识别，返回在节点原文中的位置；代号分配和替换在 engine.py。
所有正则只在单个节点的原文上运行，不跨节点。
"""
import dataclasses
import re
from functools import lru_cache
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.redaction.dictionaries import common_terms, find_country, cn_surnames, cn_surname_pattern
from app.redaction.parties import normalize, bilingual_label_text
from app.redaction import validators
from app.redaction.memo import by_text


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    category: str  # 9.2 代码
    key: str  # 同一对象的标识：key 相同的命中使用同一代号（编号、账号、SWIFT、金额、单价）
    currency: str | None = None  # 规范币种代码（CNY、USD…），未知为 None；只有 find_amounts 填写
    value: Decimal | None = None  # 换算后的数值（“万元”已乘 10000），无法解析为 None；只有 find_amounts 填写
    validated: bool | None = None


def recognition_view(text: str) -> str:
    """等长规则 / 实体识别副本；不改原文与对照表。"""
    text = ''.join(chr(ord(c) - 0xFEE0) if '\uff01' <= c <= '\uff5e' else ' ' if c == '\u3000' else c for c in text)
    protected = None
    def digits(match):
        nonlocal protected
        word = match.group()
        count = sum(c in 'OoIl|SB' for c in word)
        if sum(c.isdigit() for c in word) >= 6 and 0 < count <= 2:
            if protected is None:
                protected = [m.span() for pattern, validator in ((_CREDIT_CODE, validators.uscc), (_IBAN, validators.iban))
                             for m in pattern.finditer(text) if validator(m.group())]
            if any(start < match.end() and match.start() < end for start, end in protected):
                return word  # 校验已通过的原代码不把合法字母误改为 OCR 数字。
            return word.translate(str.maketrans('OoIl|SB', '0011158'))
        return word
    return re.sub(r'(?<![A-Za-z0-9])[0-9OoIl|SB][0-9OoIl|SB .\t,()（）-]*[0-9OoIl|SB](?![A-Za-z0-9])', digits, text)


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
    r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[A-Z0-9]{11,30}|(?:[ .-][A-Z0-9]{4}){2,7}(?:[ .-](?=[A-Z0-9]{0,2}\d)[A-Z0-9]{1,3})?)"
    r"(?![A-Za-z0-9])", re.IGNORECASE
)
_LABELED_IBAN = re.compile(
    r'(?:IBAN(?:\s*(?:No\.?|number))?|银行账号|收款账号|付款账号|账号|帐号|账户|帐户|account\s*(?:no\.?|number))'
    r'\s*[:：]?\s*' + _OPEN + '(' + _IBAN.pattern + ')', re.IGNORECASE)
_ACCOUNT = re.compile(
    r"(?:开户行账号|银行账号|收款账号|付款账号|账号|帐号|账户|帐户|(?i:account\s*(?:no\.?|number)|a/c\s*no\.?|a/c))"
    r"\s*[:：]?\s*" + _OPEN + r"(\d[\d \-]{6,45}\d)(?![\d])"
)
_ACCOUNT_SIMPLE = re.compile(r'(?<![A-Za-z])(?i:Account|A/C|Acct\.?)[ \t]+'
    + _OPEN + r'(\d[\d -]{6,45}\d)(?!\d)')
_SWIFT = re.compile(
    r"(?i:swift(?:\s*/\s*bic)?(?:\s*code)?|bic(?:\s*code)?)\s*[:：]?\s*" + _OPEN +
    r"([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)(?![A-Za-z0-9])", re.IGNORECASE
)
_CREDIT_CODE = re.compile(r"(?<![0-9A-Za-z])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Za-z])")
_OCR_CREDIT = re.compile(r'(?<![A-Za-z0-9])[0-9A-HJ-NPQRTUWXYOoIl|SB]{2}[0-9OoIl|SB]{6}'
                         r'[0-9A-HJ-NPQRTUWXYOoIl|]{10}(?![A-Za-z0-9])')
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
_PHONE_SEPARATED = re.compile(
    r"(?<![A-Za-z0-9-])(?:(?:\+\d{1,3}[ -]?)?\(0?\d{2,4}\)[ -]?\d{3,4}[ -]?\d{4}"
    r"|1[3-9]\d[ -]\d{4}[ -]\d{4}|0\d{2,3}[ -]\d{3,4}[ -]?\d{4})(?![A-Za-z0-9-])"
)
_IM_NO_COLON = re.compile(
    r"(?:微信号[ \t]*|(?:微信|QQ号|(?<![A-Za-z])(?:(?i:WeChat(?:[ \t]*ID)?|QQ|WhatsApp|Telegram|Skype)|LINE)(?![A-Za-z]))[ \t]+)"
    r"([A-Za-z0-9_.+\-]{3,40})(?![A-Za-z0-9_.+\-])"
)

# V2-B-01：即时通信必须带冒号，避免将叙述中的账号猜作标签值（设计 1.5 #7）。
_IM = re.compile(
    r"(?:微信号|微信|QQ号|钉钉|(?<![A-Za-z])(?i:WeChat|QQ|WhatsApp|Skype|Telegram|LINE)(?![A-Za-z]))"
    r"[ \t]*[:：][ \t]*" + _OPEN + r"([A-Za-z0-9_.+\-]{3,40})(?![A-Za-z0-9_.+\-])"
)
_LABELED_ID = re.compile(
    r"(?:身份证号码|身份证号|证件号码|证件号|统一社会信用代码|护照号码|护照号|营业执照注册号|工商注册号|注册号|组织机构代码|纳税人识别号|税号|"
    r"(?<![A-Za-z])(?i:Passport\s+No\.?|Organization\s+Code|Tax\s+ID|VAT\s+No\.?|TIN|ИНН|КПП|ОГРН|UEN|ABN|EIN|CNPJ)(?![A-Za-z]))"
    r"\s*[:：]?\s*" + _OPEN + r"([A-Za-z0-9]{1,4}(?:[ \t.\-](?:[A-Za-z0-9]*\d[A-Za-z0-9]*|[A-Z]{1,4})){1,8}"
    r"|[A-Za-z0-9\-]{5,30})(?![A-Za-z0-9\-])"
)
_BANK_CODE = re.compile(
    r"(?:开户行行号|大额支付行号|联行号|行号|(?<![A-Za-z])(?i:CNAPS|Sort\s+Code|Routing\s+No\.?|ABA|IFSC)(?![A-Za-z]))"
    r"\s*[:：]?\s*" + _OPEN + r"([A-Za-z0-9]{6,15})(?![A-Za-z0-9])"
)

# V31-S-01a：只按证照标签取值，括号为编号的一部分；不吞下一句或下一行。
_LICENSE = re.compile(
    r'(?:非药品类易制毒化学品经营备案证明|危险化学品经营许可证|危险化学品安全使用许可证|'
    r'安全生产许可证|危险化学品登记证|全国工业产品生产许可证|排污许可证|道路运输经营许可证|'
    r'食品经营许可证|药品经营许可证|许可证)(?:编号|号码|号)?|备案编号|证书编号|'
    r'(?<![A-Za-z])(?i:License|Permit|Certificate)\s+No\.?(?![A-Za-z])')
_LICENSE_VALUE = re.compile(r'[ \t]*[:：]?[ \t]*([^\\\r\n，。；;！!？?：:.]{4,40})(?=$|[\\\r\n，。；;！!？?：:.])')
_GENERIC_ID = re.compile(r'(?:证件号码|证件号|证照号|许可证编号|证书编号|资质证书编号)[ \t]*[:：]?[ \t]*'
                         r'([^\\\r\n,，。；;！!？?：:]{5,60})')
_LABELED_SWIFT = re.compile(r'(?i:SWIFT(?:[ \t]+Code)?|BIC|银行识别码)[ \t]*[:：]?[ \t]*'
                           r'([A-Za-z0-9]{8,11})(?![A-Za-z0-9])')


def _norm_key(value: str) -> str:
    return re.sub(r"[\s.\-]", "", value).upper()


@by_text
def find_formatted(text: str) -> list[Hit]:
    """第 4 步的全部命中（可能互相重叠，由调用方按顺序取舍）。顺序即优先级。"""
    # 逐位中文数字只进入格式化号码规则，不进入名称 / 人名模型视图。
    text = recognition_view(bilingual_label_text(text).translate(str.maketrans('〇零一二三四五六七八九', '00123456789')))
    hits: list[Hit] = []

    def add(start: int, end: int, category: str, key: str = "", validated: bool | None = None) -> None:
        hits.append(Hit(start, end, category, key, validated=validated))

    for m in _EMAIL.finditer(text):
        add(m.start(), m.end(), "EMAIL")
    for m in _URL.finditer(text):
        end = m.end()
        while end > m.start() and text[end - 1] in _URL_TRAILING:
            end -= 1
        add(m.start(), end, "URL")
    # 账号、SWIFT 按对象编号（Q-59）：账号的 key 为去掉空格和短横线后的大写字符（IBAN 与本地写法视为不同账号）；
    # SWIFT 的 key 为大写代码，11 位且末尾为 XXX 时与前 8 位视为同一代码
    for m in _LABELED_IBAN.finditer(text):
        add(m.start(1), m.end(1), 'ACCOUNT', _norm_key(m.group(1)), validators.iban(m.group(1)))
    for m in _IBAN.finditer(text):
        valid = validators.iban(m.group())
        if valid or m.group().isupper():
            add(m.start(), m.end(), "ACCOUNT", _norm_key(m.group()), valid)
    for m in re.finditer(_IBAN.pattern.replace(r'\d{2}', '[0-9OoIl|SB]{2}'), text, re.IGNORECASE):
        value = m.group()
        corrected = value[:2] + value[2:4].translate(str.maketrans('OoIl|SB', '0011158')) + value[4:]
        if sum(c.isdigit() for c in value) >= 6 and corrected != value and validators.iban(_norm_key(corrected)):
            add(m.start(), m.end(), 'ACCOUNT', _norm_key(corrected), True)
    for m in _ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if 8 <= len(digits) <= 30:
            add(m.start(1), m.end(1), "ACCOUNT", _norm_key(m.group(1)))
    for m in _ACCOUNT_SIMPLE.finditer(text):
        if 8 <= sum(c.isdigit() for c in m.group(1)) <= 30 and not any(
                h.category == 'ACCOUNT' and (h.start, h.end) == m.span(1) for h in hits):
            add(m.start(1), m.end(1), 'ACCOUNT', _norm_key(m.group(1)))
    for m in _SWIFT.finditer(text):
        code = m.group(1).upper()
        add(m.start(1), m.end(1), "SWIFT", code[:8] if len(code) == 11 and code.endswith("XXX") else code,
            validators.bic(code))
    for m in _LABELED_SWIFT.finditer(text):
        code = m.group(1).upper()
        add(m.start(1), m.end(1), 'SWIFT', code[:8] if len(code) == 11 and code.endswith('XXX') else code)
    for m in _GENERIC_ID.finditer(text):
        value = m.group(1).strip().rstrip('.')
        if sum(c.isdigit() for c in value) >= 5:
            start = m.start(1) + len(m.group(1)) - len(m.group(1).lstrip())
            add(start, start + len(value), 'IDNO')
    for m in _BANK_CODE.finditer(text):
        add(m.start(1), m.end(1), "SWIFT", m.group(1).upper())
    for m in _LABELED_ID.finditer(text):
        if sum(c.isdigit() for c in m.group(1)) >= 2:
            value = m.group(1)
            valid = (validators.id_card(value) if _ID_CARD.fullmatch(value) else
                     validators.uscc(value) if _CREDIT_CODE.fullmatch(value) else None)
            add(m.start(1), m.end(1), "IDNO", validated=valid)
    for label in _LICENSE.finditer(text):
        m = _LICENSE_VALUE.match(text, label.end())
        if m:
            value = m.group(1).strip().rstrip('.')
            if 4 <= len(value) <= 40 and any(c.isdigit() for c in value):
                start = m.start(1) + len(m.group(1)) - len(m.group(1).lstrip())
                add(start, start + len(value), 'IDNO')
    for m in _IM.finditer(text):
        add(m.start(1), m.end(1), "IM")
    for m in _IM_NO_COLON.finditer(text):
        value = m.group(1)
        label = text[m.start():m.start(1)].strip().casefold()
        if (re.fullmatch(r'\d{5,11}', value) if label in ('qq', 'qq号')
                else any(c.isdigit() or c == '_' for c in value)):
            add(m.start(1), m.end(1), 'IM')
    for m in _REFNO.finditer(text):
        value = m.group(1).rstrip(".")
        if any(c.isdigit() for c in value):
            add(m.start(1), m.start(1) + len(value), "REFNO", _norm_key(value))
    # 标签强于号码形状，订单编号等先占位；无标签的校验失败项由引擎列为不确定项。
    for pattern, validator in ((_CREDIT_CODE, validators.uscc), (_ID_CARD, validators.id_card)):
        for m in pattern.finditer(text):
            add(m.start(), m.end(), "IDNO", validated=validator(m.group()))
    for m in _OCR_CREDIT.finditer(text):
        raw = m.group()
        letters = str.maketrans('OoIl|', '00111')
        corrected = raw[:2].translate(letters) + raw[2:8].translate(str.maketrans('OoIl|SB', '0011158')) + raw[8:].translate(letters)
        if corrected != raw and sum(c.isdigit() for c in raw) >= 6:
            add(m.start(), m.end(), 'IDNO', validated=validators.uscc(corrected))
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
    for m in _PHONE_SEPARATED.finditer(text):
        # 不截取更长的分组数字串；账号、证件号仍优先。
        if re.match(r'[ -]+\d', text[m.end():]):
            continue
        if any(h.category in ('ACCOUNT', 'IDNO') and h.start < m.end() and m.start() < h.end for h in hits):
            continue
        if any(h.category == 'PHONE' and (h.start, h.end) == m.span() for h in hits):
            continue
        add(m.start(), m.end(), 'PHONE')
    return hits


# ---------------------------------------------------------------- 第 5 步：金额

_NUM = r"\d{1,3}(?:[, \t]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# 币种 → 规范代码；同一数值、同一币种使用同一代号
_CURRENCY = {
    "RMB": "CNY", "CNY": "CNY", "人民币": "CNY", "¥": "CNY", "￥": "CNY", "元": "CNY", "万元": "CNY", "亿元": "CNY",
    "USD": "USD", "US$": "USD", "$": "USD", "美元": "USD", "万美元": "USD", "美金": "USD",
    "块": "CNY", "块钱": "CNY",
    "EUR": "EUR", "€": "EUR", "欧元": "EUR", "GBP": "GBP", "£": "GBP", "英镑": "GBP",
    "JPY": "JPY", "日元": "JPY", "HKD": "HKD", "HK$": "HKD", "港元": "HKD", "港币": "HKD",
    "AED": "AED", "SAR": "SAR", "TRY": "TRY", "INR": "INR", "AUD": "AUD", "CAD": "CAD", "CHF": "CHF",
    "SGD": "SGD", "KRW": "KRW", "RUB": "RUB",
}
_CODES = "RMB|CNY|USD|EUR|JPY|GBP|HKD|AED|SAR|TRY|INR|AUD|CAD|CHF|SGD|KRW|RUB"
_PREFIX = re.compile(
    rf"(?:(?<![A-Za-z])(?:US\$|HK\$|{_CODES})(?![A-Za-z])|人民币|美元|欧元|日元|英镑|港币|港元|美金|[¥￥$€£])\s?{_OPEN}({_NUM})(?![\d,.]*\d)"
)
_SUFFIX = re.compile(
    # 后置币种不含“人民币”：避免把“2.3 人民币参考价”这类条款编号当成金额
    # 数字前不能是下划线或字母：外发文本中代号“[金额_1]元”的序号不是金额（出口复检的重扫用同一函数）
    # 逗号只在紧跟数字时才算千分位：识别视图把全角“，”变成半角，“计算,10元”的 10 仍是金额（V31-U5F-02）
    rf"(?<![\d._A-Za-z])(?<!\d,)({_NUM}){_CLOSE}\s?(万美元|万元|亿元|美元|欧元|英镑|日元|港元|港币|元|(?:{_CODES})(?![A-Za-z]))"
)
_FOLLOWED_BY_NUMBER = re.compile(r"\s?\d")
_PREFIX_WORDS = {"美元", "欧元", "英镑", "日元", "港元", "港币", "美金", *_CODES.split("|")}
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
# V2-B-10：独立的后置语法，不扩展无币种金额的标签或门槛。
_SCALE = {'千': Decimal(1000), '万': Decimal(10000), '百万': Decimal(1000000),
          '千万': Decimal(10000000), '亿': Decimal(100000000)}
_MONEY_UNIT = rf"美元|美金|欧元|英镑|日元|港元|港币|元|(?:{_CODES})(?![A-Za-z])"
_MONEY_TAIL = (rf"(?P<before>[余多])?(?P<scale>千万|百万|千|万|亿)?(?P<after>[余多])?"
               rf"(?P<unit>{_MONEY_UNIT})")
_EXT_SUFFIX = re.compile(rf"(?<![第\d._A-Za-z])(?<!\d,)(?P<num>{_NUM})(?![\d,.]*\d)[ \t]*"
                         rf"(?:{_MONEY_TAIL}|(?P<block>块)(?=钱|整|[^一-鿿]|$)|"
                         rf"[（(][ \t]*(?P<br_scale>千万|百万|千|万|亿)?(?P<br_unit>{_MONEY_UNIT})[ \t]*[）)])")
_LOWER_CN = re.compile(rf"(?<!\d,)(?<![第每\d._零壹贰叁肆伍陆柒捌玖拾佰仟一二三四五六七八九两〇十百千万亿])"
                       rf"(?P<num>[一二三四五六七八九两〇零十百千万亿]+)(?P<unit>{_MONEY_UNIT}|圆)整?")
_LOWER_TO_UPPER = str.maketrans('一二三四五六七八九两〇十百千', '壹贰叁肆伍陆柒捌玖贰零拾佰仟')


def _prefix_of_next(text: str, end: int, unit: str | None, num: str) -> bool:
    """“3.1 USD 100”：USD 是后一个数字的前置币种，前面的短数字是条款编号。

    元、万元、块等不能前置；带千分位或三位以上的数字也不是条款编号，仍算金额（V2-B-11，“30000元 3日内”）。
    """
    return (unit in _PREFIX_WORDS and "," not in num and len(num.split(".")[0]) <= 2
            and _FOLLOWED_BY_NUMBER.match(text, end) is not None)


def _decimal(num: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r'[, \t]', '', num))
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
_CN_NUMBER = r'[零〇一二三四五六七八九两十百千壹贰叁肆伍陆柒捌玖拾佰仟万亿]+'
_OCR_NUM = r'[0-9OoIl|SB]{1,3}(?:[, \t][0-9OoIl|SB]{3})+(?:\.[0-9OoIl|SB]+)?|[0-9OoIl|SB]+(?:\.[0-9OoIl|SB]+)?'
_BARE_CN_VALUE = re.compile(_BARE_VALUE.pattern.replace(f'(?P<num>{_NUM})', f'(?P<num>{_CN_NUMBER})'), re.I)
_BARE_OCR_VALUE = re.compile(_BARE_VALUE.pattern.replace(f'(?P<num>{_NUM})', f'(?P<num>{_OCR_NUM})'), re.I)


def amount_label_kind(text: str) -> str | None:
    """紧邻左格必须完整等于金额标签（可有单位说明和末尾冒号）。"""
    compact = re.sub(r"\s+", "", text)
    for label in sorted(AMOUNT_LABELS, key=len, reverse=True):
        key = re.sub(r"\s+", "", label)
        if compact[:len(key)].casefold() == key.casefold() and _BARE_LABEL_TAIL.fullmatch(compact[len(key):]):
            return "PRICE" if "单价" in label or "price" in label.casefold() else "AMOUNT"
    return None


@by_text
def find_bare_amounts(text: str) -> list[Hit]:
    return _find_bare_amounts(_unsigned_amount_view(text))


def _unsigned_amount_view(text: str) -> str:
    view = re.sub(r'[-−－](?=\d)', ' ', text).translate(str.maketrans('叄貳參陸萬億圓', '叁贰叁陆万亿元'))
    chars = list(view)
    # 只遮空数字的会计括号，保留“合同金额（元）”等既有单位标签。
    for match in re.finditer(rf'[(（][ \t]*(?:{_NUM})[ \t]*[)）]', view):
        chars[match.start()] = chars[match.end() - 1] = ' '
    return ''.join(chars)


def _find_bare_amounts(text: str) -> list[Hit]:
    """金额标签后的数字；只替换数字，保留单位与万/亿，币种规则优先（R4 3.2）。"""
    hits: list[Hit] = []
    existing = find_amounts(text)
    for label in _BARE_LABEL_RE.finditer(text):
        if label.start() and re.match(r"[第\dA-Za-z]", text[label.start() - 1]):
            continue
        m = _BARE_VALUE.match(text, label.end())
        if m is None or _BARE_BAD_TAIL.match(text, m.end()) or _BARE_SPACED_UNIT.match(text, m.end()):
            m = next((candidate for pattern in (_BARE_CN_VALUE, _BARE_OCR_VALUE)
                      if (candidate := pattern.match(text, label.end())) is not None
                      and not _BARE_BAD_TAIL.match(text, candidate.end())
                      and not _BARE_SPACED_UNIT.match(text, candidate.end())), None)
            if m is None:
                continue
        start, end = m.span("num")
        if any(h.start < end and start < h.end for h in existing):
            continue
        num, scale = m.group("num"), m.group("scale")
        if not (len(num.split(".")[0].replace(",", "")) >= 3 or "," in num
                or re.search(r"\.\d{2}$", num) or scale or re.search(r'[十百千万亿拾佰仟]', num)):
            continue
        value = _decimal(num)
        if value is None and re.search(r'[零〇一二三四五六七八九两壹贰叁肆伍陆柒捌玖]', num):
            value = chinese_upper_value(num.translate(_LOWER_TO_UPPER))
        if value is None and sum(c in 'OoIl|SB' for c in num) <= 2 and any(c.isdigit() for c in num):
            value = _decimal(num.translate(str.maketrans('OoIl|SB', '0011158')))
        if value is not None and scale:
            value *= Decimal(10000 if scale == "万" else 100000000)
        unit = m.group("unit")
        currency = _CURRENCY.get(unit.upper() if unit and unit.isascii() else unit)
        cat = "PRICE" if "单价" in label.group() or "price" in label.group().casefold() else "AMOUNT"
        if value != 0:
            hits.append(Hit(start, end, cat, _amount_key(cat, currency, value, num), currency, value))
    return hits


_EN_NUMBERS = dict(zip(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety'.split(),
    [*range(20), 20, 30, 40, 50, 60, 70, 80, 90]))
_EN_SCALES = {'hundred': 100, 'thousand': 1000, 'million': 1000000, 'billion': 1000000000}
_EN_NUMBER_WORD = '|'.join([*_EN_NUMBERS, *_EN_SCALES, 'and'])
_EN_MONEY = re.compile(rf'(?<![A-Za-z])(?:{_EN_NUMBER_WORD})(?:[ \t-]+(?:{_EN_NUMBER_WORD}))+(?![A-Za-z])', re.I)
_EN_CURRENCIES = re.compile(r'\b(?:(?:Singapore|Hong Kong|HK|Australian|Canadian|New Taiwan)[ \t]+Dollars|US Dollars|Dollars|SGD|AUD|CAD|TWD|USD|EUR|Euros|RMB|Yuan|Renminbi|Pounds|GBP|JPY|Yen|HKD|CNY)\b', re.I)
_EN_CURRENCY_CODES = {'us dollars': 'USD', 'dollars': 'USD', 'euros': 'EUR',
    'singapore dollars': 'SGD', 'hong kong dollars': 'HKD', 'hk dollars': 'HKD', 'australian dollars': 'AUD',
    'canadian dollars': 'CAD', 'new taiwan dollars': 'TWD',
    'yuan': 'CNY', 'renminbi': 'CNY', 'rmb': 'CNY', 'pounds': 'GBP', 'yen': 'JPY'}


def _english_value(words: str) -> Decimal:
    total = current = 0
    for word in re.split(r'[ \t-]+', words.casefold()):
        if word in _EN_NUMBERS:
            current += _EN_NUMBERS[word]
        elif word == 'hundred':
            current *= 100
        elif word in _EN_SCALES:
            total += current * _EN_SCALES[word]
            current = 0
    return Decimal(total + current)


def _english_amounts(text: str) -> list[Hit]:
    hits = []
    currencies = list(_EN_CURRENCIES.finditer(text))
    for m in _EN_MONEY.finditer(text):
        words = re.split(r'[ \t-]+', m.group().casefold())
        if not any(w in _EN_SCALES for w in words):
            continue
        # 前置币种只在同一句内有效；Say … Only 可沿用本节点最近币种。
        sentence = max((text.rfind(c, 0, m.start()) for c in '\n;；。!?'), default=-1) + 1
        periods = list(re.finditer(r'(?<!\d)\.(?!\d)', text[:m.start()]))
        if periods:
            sentence = max(sentence, periods[-1].end())
        before = [c for c in currencies if sentence <= c.start() < m.start()]
        after = next((c for c in currencies if re.fullmatch(r'[ \t]*', text[m.end():c.start()])
                      and c.start() >= m.end()), None)
        say = re.search(r'\bSay\b[^\n;；。!?]*$', text[:m.start()], re.I)
        end = m.end()
        value = _english_value(m.group())
        # 尾部 and 可属于 cents / 分数部分，不把它留在整数匹配中。
        trailing_and = re.search(r'[ \t-]+and$', m.group(), re.I)
        if trailing_and:
            end = m.start() + trailing_and.start()
        # 元与分以最后一个 and 分开：英式读法“One Hundred and Twenty Thousand and Fifty Cents”（V2Q-173）。
        cents = next(reversed([*re.finditer(r'\band[ \t]+', m.group(), re.I)]), None)
        if cents and re.match(r'[ \t]+cents\b', text[end:], re.I):
            value = _english_value(m.group()[:cents.start()]) + _english_value(m.group()[cents.end():]) / 100
            end += re.match(r'[ \t]+cents\b', text[end:], re.I).end()
        fraction = re.match(r'[ \t]+and[ \t]+(\d{1,2})/100\b', text[end:], re.I)
        if fraction:
            value += Decimal(fraction.group(1)) / 100
            end += fraction.end()
        named_cents = re.match(rf'[ \t]+and[ \t]+cents[ \t]+((?:{_EN_NUMBER_WORD})(?:[ \t-]+(?:{_EN_NUMBER_WORD}))*)\b', text[end:], re.I)
        if named_cents:
            value += _english_value(named_cents.group(1)) / 100
            end += named_cents.end()
        only = re.match(r'[ \t]+Only\b', text[end:], re.I)
        if not before and after is None and not (say and only):
            continue
        currency_match = before[-1] if before else after
        if currency_match is None and say and only and currencies:
            currency_match = min(currencies, key=lambda c: abs(c.start() - m.start()))
        currency = None
        if currency_match:
            token = re.sub(r'[ 	]+', ' ', currency_match.group().casefold())
            currency = _EN_CURRENCY_CODES.get(token, token.upper())
        hits.append(Hit(m.start(), end, 'AMOUNT', _amount_key('AMOUNT', currency, value, m.group()), currency, value))
    return hits


_OCR_WINDOW = 200


def _shift(hit: Hit, offset: int) -> Hit:
    return dataclasses.replace(hit, start=hit.start + offset, end=hit.end + offset)


@by_text
def find_amounts(text: str, column: str | None = None) -> list[Hit]:
    # 符号与会计括号只在识别视图中遮空，命中仅覆盖绝对值，原文位置不变。
    view = _unsigned_amount_view(text)
    hits = _find_amounts(view, column)
    # 短 OCR 数字仅在明确币种 / 金额列语境中核验，不放宽全局数字识别视图。
    # F1-S1-B2 等位置编号的尾部不能因紧随 USD 而被误认作金额；独立负数仍可识别。
    for match in re.finditer(r'(?<![A-Za-z0-9])(?<![A-Za-z0-9]-)[0-9OoIl|SB][0-9OoIl|SB,. \t]*', view):
        token = match.group().rstrip()
        wrong = [i for i, c in enumerate(token) if c in 'OoIl|SB']
        if not wrong or len(wrong) > 2 or not any(c.isdigit() for c in token):
            continue
        corrected = token.translate(str.maketrans('OoIl|SB', '0011158'))
        # 只在该处前后 _OCR_WINDOW 字内重新识别：命中必须覆盖被纠正的字符，只取决于附近的币种与单位；
        # 整段重扫会使长文本（出口复检的全文）随这类写法的个数成倍变慢（V31-U5F-01）。
        lo = max(0, match.start() - _OCR_WINDOW)
        hi = min(len(view), match.start() + len(token) + _OCR_WINDOW)
        candidate = view[lo:match.start()] + corrected + view[match.start() + len(token):hi]
        for hit in _find_amounts(_unsigned_amount_view(candidate), column):
            if ((hit.currency is not None or column in ('PRICE', 'AMOUNT'))
                    and any(hit.start + lo <= match.start() + i < hit.end + lo for i in wrong)):
                hits.append(_shift(hit, lo) if lo else hit)
    return hits


def numeric_cell_view(text: str) -> str | None:
    """整格数字的 OCR 副本，只供表格金额语境判断，不放宽正文数字识别。"""
    if (not re.fullmatch(r'\s*[-−－(（]?[0-9OoIl|SB][0-9OoIl|SB,. \t]*[)）]?\s*', text)
            or not any(c.isdigit() for c in text) or sum(c in 'OoIl|SB' for c in text) > 2):
        return None
    return text.translate(str.maketrans('OoIl|SB', '0011158'))


def _find_amounts(text: str, column: str | None = None) -> list[Hit]:
    """第 5 步：金额与单价。只替换数字部分，币种文字保留（9.2“币种保留”）。

    column 为表格列提示：PRICE（单价列）或 AMOUNT（金额列）；该列中带币种的金额按列归类，
    整格只有一个数字时也按列归为单价或金额。
    """
    hits: list[Hit] = _english_amounts(text)

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
        if _prefix_of_next(text, m.end(), suffix, m.group(1)):
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
    # 前置币种优先；扩展后置语法提供量级，保留原有命中的类别和区间。
    extended = list(_EXT_SUFFIX.finditer(text))
    prefixes = {m.span(1) for m in _PREFIX.finditer(text)}
    for m in extended:
        if m.group('before') and m.group('after'):
            continue
        if not (m.group('scale') or m.group('before') or m.group('after')) and _prefix_of_next(
                text, m.end(), m.group('unit'), m.group('num')):
            continue
        start, end = m.span('num')
        unit = m.group('unit') or m.group('br_unit') or '块'
        scale = m.group('scale') or m.group('br_scale')
        value = _decimal(m.group('num')) * _SCALE.get(scale, Decimal(1))
        same = [h for h in hits if (h.start, h.end) == (start, end)]
        currency = same[0].currency if (start, end) in prefixes and same else _CURRENCY.get(unit, 'CNY')
        cat = same[0].category if same else category(m.end())
        hits = [h for h in hits if (h.start, h.end) != (start, end)]
        hits.append(Hit(start, end, cat, _amount_key(cat, currency, value, m.group('num')), currency, value))
    for m in _LOWER_CN.finditer(text):
        if not any(ch in m.group('num') for ch in '十百千万亿'):
            continue
        # “单位：千元”“百元大钞”只有量级、没有具体数字，不是金额（V2-B-11）。
        if not (m.group('num')[0] == '十' or any(ch in m.group('num') for ch in '一二三四五六七八九两')):
            continue
        # 后置语法内的“千元 / 万元”是阿拉伯金额的单位，不另切出小写金额。
        if any(e.start() <= m.start() and m.end() <= e.end() for e in extended):
            continue
        if text[:m.start()].rstrip(' \t（(').endswith(']'):
            continue  # 代号后的量级是保留单位，不是另一笔中文金额。
        if any(h.start < m.end() and m.start() < h.end for h in hits):
            continue
        value = chinese_upper_value(m.group('num').translate(_LOWER_TO_UPPER))
        if value is None:
            continue
        currency = _CURRENCY.get(m.group('unit'), 'CNY')
        cat = category(m.end())
        hits.append(Hit(m.start(), m.end(), cat, _amount_key(cat, currency, value, m.group()), currency, value))
    cn = r'[零〇一二三四五六七八九两十百千壹贰叁肆伍陆柒捌玖拾佰仟万亿]+'
    currencies = '|'.join(re.escape(c) for c in sorted(_CURRENCY, key=len, reverse=True))
    for pattern, prefix in ((rf'(?P<currency>{currencies})[ \t]*(?P<num>{cn})(?=$|[ \t,，。；;:/（）()元圆])', True),
                            (rf'(?P<num>{cn})[ \t]*(?P<currency>{currencies})(?![A-Za-z])', False)):
        for match in re.finditer(pattern, text):
            if not re.search(r'[零〇一二三四五六七八九两壹贰叁肆伍陆柒捌玖]', match.group('num')):
                continue  # 千/万等量级不能从相邻阿拉伯金额中单独截作另一笔钱。
            if text[match.start('num'):].startswith(('一元化', '二元结构')):
                continue
            if match.start('num') and text[match.start('num') - 1] in '第每':
                continue
            if match.group('currency') == '块' and (match.group('num') == '一' or text.startswith('儿', match.end())
                                                    or text[match.start('num') - 1:match.start('num')] in ('这', '那', '哪')):
                continue  # “这一块”“一块儿”“一块去”是量词或副词，不是一元；复检共用本函数（V31-M-08）。
            value = chinese_upper_value(match.group('num').translate(_LOWER_TO_UPPER))
            if value is not None and value > 0:
                currency = _CURRENCY.get(match.group('currency').upper(), _CURRENCY.get(match.group('currency')))
                cat = category(match.end())
                start, end = match.span('num')
                # 完整大写金额可能还含元、角、分；不能再把其中整数部分登记为较短金额。
                if not any(h.start <= start and end <= h.end for h in hits):
                    hits.append(Hit(start, end, cat, _amount_key(cat, currency, value, match.group('num')), currency, value))
    # V31Q-66：正文位权数字支持约数后缀、单位括号与填空括号；表格裸中文数字不猜金额。
    contexts = (
        rf'(?P<num>{cn}){_CLOSE}[ \t]*(?:[余多来]?元(?:左右)?|[（(][ \t]*元[ \t]*[）)])',
        rf'(?:人民币|￥|¥|RMB)[ \t]*{_OPEN}(?P<num>{cn}){_CLOSE}',
    )
    for pattern in contexts:
        for match in re.finditer(pattern, text, re.I):
            num = match.group('num')
            start, end = match.span('num')
            if (not re.search(r'[十百千万亿拾佰仟]', num)
                    or not (num.startswith(('十', '拾')) or re.search(r'[一二三四五六七八九两壹贰叁肆伍陆柒捌玖]', num))
                    or start and text[start - 1] in '第每0123456789'):
                continue
            value = chinese_upper_value(num.translate(_LOWER_TO_UPPER))
            if value is None or value <= 0 or any(h.start <= start and end <= h.end for h in hits):
                continue
            cat = category(match.end())
            hits.append(Hit(start, end, cat, _amount_key(cat, 'CNY', value, num), 'CNY', value))
    # 出口复检会把汉字数字归一为阿拉伯数字，普通词句在两份视图中都保留。
    unique = {(h.start, h.end): h for h in reversed(hits) if h.value != 0
              and not text[h.start:].startswith(('一元化', '二元结构', '1元化', '2元结构'))}
    return sorted(unique.values(), key=lambda h: (h.start, h.end))


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
# 无点写法只认 Mr / Mrs / Ms / Miss 与全大写 MR / MRS / MISS；全大写 MS（低碳钢、Microsoft）与小写 ms 须带点（V2Q-173）
_EN_TITLES = re.compile(r"(?:Mr|Mrs|Ms|Miss|MR|MRS|MISS)[ \t]+|(?i:Mr|Mrs|Ms|Miss|Dr|Prof)\.[ \t]*")
_FUNCTION_ROLES = ("采购", "销售", "会计", "出纳", "法务")
# 落款后的部门名称不是姓名（V2Q-173）
_EN_DEPARTMENT_WORDS = frozenset(("department", "dept", "control", "team", "office", "division", "center", "centre",
                                  "section", "unit", "group", "desk"))
# 首字母写法中常见的贸易、地名缩写，不是姓名首字母（V2Q-173）
_NOT_INITIALS = frozenset(("U.S.", "U.K.", "U.N.", "E.U.", "L.C.", "T.T.", "D.P.", "D.A.", "B.L.", "P.O.", "A.M.", "P.M.",
                           "N.B.", "I.E.", "E.G.", "C.O.", "O.K."))
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


@by_text
def find_persons(text: str) -> list[Hit]:
    """第 6 步：人名标签之后的人名。中文 2–4 个汉字（去掉末尾称谓后），英文 1–3 个首字母大写的单词。

    不能确定时不识别（不猜测）。标签后紧接公司名称的情况由 engine 的占位处理（公司名已先被替换）。
    """
    text = bilingual_label_text(text)
    hits: list[Hit] = []
    seen: set[tuple[int, int]] = set()
    terms = [t.casefold() for t in common_terms()]
    for m in _PERSON_LABEL.finditer(text):
        # Project Name 是项目标签，不能把其中 Name 当成人名标签抢先识别。
        if (re.search(r"(?i:Project|Product|Item|Commodity|Goods|Cargo|Brand|Model|Material|Part|Article|Grade|Chemical|Trade|Company)[ \t]+$",
                      text[:m.start()]) and m.group().lower().startswith("name")):
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
    pattern = re.compile("(?:" + cn_surname_pattern() + ")(?:"
                         + "|".join(sorted(titles, key=len, reverse=True)) + ")")
    left = ("请", "由", "与", "和", "及", "致", "向", "给", "找", "跟", "同", "经", "抄送", "联系", "通知", "委托", "授权", "尊敬的", "亲爱的")
    full_name = re.compile('(?:' + cn_surname_pattern() + r')[一-鿿]{1,2}?(?=先生|女士|小姐)')
    for m in full_name.finditer(text):
        if m.start() and re.match(r'[一-鿿]', text[m.start() - 1]) and not text[:m.start()].endswith(left):
            continue
        if m.group() in _CN_NOT_PERSON:
            continue
        if not any(h.start < m.end() and m.start() < h.end for h in hits):
            hits.append(Hit(m.start(), m.end(), 'PERSON', normalize(m.group())))
    for m in pattern.finditer(text):
        if m.start() and re.match(r"[一-鿿]", text[m.start() - 1]) and not text[:m.start()].endswith(left):
            continue
        if not any(h.start < m.end() and m.start() < h.end for h in hits):
            hits.append(Hit(m.start(), m.end(), "PERSON", normalize(m.group())))
    for m in _EN_TITLES.finditer(text):
        if m.start() and text[m.start() - 1].isascii() and text[m.start() - 1].isalpha():
            continue
        cur, words = m.end(), []
        while len(words) < 3:
            word = _EN_WORD.match(text, cur)
            if word is None or word.group().rstrip('.').casefold() in _EN_STOP_WORDS | _EN_COMPANY_WORDS:
                break
            words.append(word)
            gap = re.match(r'[ \t]+', text[word.end():])
            if gap is None:
                break
            cur = word.end() + gap.end()
        if words:
            start, end = words[0].start(), words[-1].end()
            if text[end - 1] == '.' and len(words[-1].group()) > 2:
                end -= 1
            if not any(h.start < end and start < h.end for h in hits):
                hits.append(Hit(start, end, 'PERSON', normalize(text[start:end])))
    # V2-B-16：职务前置只接受明确的人名终点，避免把谓语当姓名。
    roles = '副董事长 董事长 副总经理 总经理 副总裁 总裁 总监 经理 主任 主管 部长 处长 科长 厂长 总工 工程师 会计 出纳 法务 采购 销售 业务员 专员 助理 秘书 代表'.split()
    endings = '沟通 协商 确认 同意 说 称 表示 负责 签字 签署 联系 提出 认为 回复 反馈 处理 商定 已 于 在 对 将'.split()
    surname = '(?:' + cn_surname_pattern() + ')'
    role = '(?:' + '|'.join(sorted(roles, key=len, reverse=True)) + ')'
    rule = re.compile(role + '(' + surname + r'[一-鿿]{1,2}?)(?=先生|女士|小姐|[^一-鿿]|$|' + '|'.join(endings) + ')')
    for m in rule.finditer(text):
        name = m.group(1)
        # V2Q-166：只收窄本次职务入口；情态词姓氏须有敬称，谓语不能充当名字。
        family = next(s for s in cn_surnames() if name.startswith(s))
        given = name[len(family):]
        honorific = re.match(r'先生|女士|小姐', text[m.end(1):]) is not None
        if given.startswith(tuple(endings)) or (family in '应须需将可会均亦再另也都必' and not honorific):
            continue
        # V2Q-173：后接冒号的是字段标签（采购范围：）；职能词常修饰名词（采购文件、会计年度），无敬称时须接结束词。
        if not honorific and (text[m.end(1):m.end(1) + 1] in (':', '：') or (
                m.group()[:m.start(1) - m.start()] in _FUNCTION_ROLES and not text.startswith(tuple(endings), m.end(1)))):
            continue
        if (name in _CN_NOT_PERSON or name in common_terms() or name in endings or name in roles
                or name.endswith(('地', '的'))):
            continue
        start, end = m.span(1)
        if not any(h.start < end and start < h.end for h in hits):
            hits.append(Hit(start, end, 'PERSON', normalize(name)))
    short = re.compile(role + '(' + surname + r'(?:先生|女士|小姐))')
    for m in short.finditer(text):
        start, end = m.span(1)
        if not any(h.start < end and start < h.end for h in hits):
            hits.append(Hit(start, end, 'PERSON', normalize(m.group(1))))
    en_name = r"[A-Z][A-Za-z'’-]*(?:[ \t]+[A-Z][A-Za-z'’-]*){0,3}"
    name = r"[一-鿿]{2,4}|" + en_name
    # <邮箱> 前的中文姓名左侧不能是汉字，避免从“邮件发送至<…>”中截字（V2Q-173）
    mail_rules = [re.compile(rf'(?im)^(?:From|To|Cc|Bcc|Sender|Reply-To):[ \t]*["“]?(?P<name>{name})'),
                  re.compile(rf'(?<![A-Za-z])(?P<name>(?<![一-鿿])[一-鿿]{{2,4}}|{en_name})["”]?[ \t]*<' + _EMAIL.pattern + '>')]
    for rule in mail_rules:
        for m in rule.finditer(text):
            surface = m.group('name')
            if _valid_signature_name(surface) and not any(h.start < m.end('name') and m.start('name') < h.end for h in hits):
                hits.append(Hit(*m.span('name'), 'PERSON', normalize(surface)))
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    for index, start, end in find_signature_persons(lines):
        start, end = offsets[index] + start, offsets[index] + end
        if not any(h.start < end and start < h.end for h in hits):
            hits.append(Hit(start, end, 'PERSON', normalize(text[start:end])))
    # B-18：父称结尾限定俄文三词全名；首字母整体优先于原标签规则的局部命中。
    cyrillic_word = r'[А-ЯЁ][А-Яа-яЁё-]+'
    russian = re.compile(rf'(?<![А-Яа-яЁё]){cyrillic_word}[ \t]+{cyrillic_word}[ \t]+'
                         rf'[А-ЯЁ][А-Яа-яЁё-]*(?:ович|евич|ична|овна|евна|ич)(?![А-Яа-яЁё])')
    initials = re.compile(r"(?<![A-Za-zА-Яа-яЁё])([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’-]+)[ \t]+([A-ZА-ЯЁ]\.[ \t]*[A-ZА-ЯЁ]\.)"
                          r"(?![ \t]*[A-Za-zА-Яа-яЁё]\.)")
    for rule in (russian, initials):
        for m in rule.finditer(text):
            # V2Q-173：U.S.、L.C. 等缩写与 The 开头的不是首字母写法；F.O.B. 由上面的后视排除。
            if rule is initials and (re.sub(r'[ \t]', '', m.group(2)) in _NOT_INITIALS or m.group(1) == 'The'):
                continue
            hits = [h for h in hits if not (m.start() <= h.start and h.end <= m.end())]
            if not any(h.start < m.end() and m.start() < h.end for h in hits):
                hits.append(Hit(m.start(), m.end(), 'PERSON', normalize(m.group())))
    for label in _PERSON_LABEL.finditer(text):
        value = re.match(r'([一-鿿]{1,3})[ \t]([一-鿿]{1,3})(?![一-鿿])', text[label.end():])
        if value and 2 <= len(value.group(1) + value.group(2)) <= 4:
            start, end = label.end(), label.end() + value.end()
            # V2Q-173：后半段是职务（“张三 经理”）时不并入，保留原规则的姓名区间。
            if value.group(1) + value.group(2) not in _CN_NOT_PERSON and value.group(2) not in [*roles, *titles]:
                hits = [h for h in hits if not (start <= h.start and h.end <= end)]
                if not any(h.start < end and start < h.end for h in hits):
                    hits.append(Hit(start, end, 'PERSON', normalize(text[start:end])))
    return hits


def english_alias_allowed(text: str, start: int, end: int) -> bool:
    """仅 B-18 英文单词别名共用的语境判断，不遮盖让位的较长名称。"""
    from app.redaction.suspects import _capitalized_candidates
    protected = [(h.start, h.end) for h in [*find_formatted(text), *find_addresses(text)]]
    if any(a <= start and end <= b for a, b in protected):
        return False
    return not any(s.start <= start and end <= s.end and (s.start < start or end < s.end)
                   for s in _capitalized_candidates(text))


def _valid_signature_name(surface: str) -> bool:
    if re.fullmatch(r'[一-鿿]{2,4}', surface):
        return (surface not in _CN_NOT_PERSON and surface not in common_terms()
                and surface[-1] not in _CN_NOT_PERSON_END and not surface.endswith(('中心', '办公室')))
    words = surface.split()
    return bool(words) and words[-1].casefold() not in _EN_DEPARTMENT_WORDS and all(w.rstrip('.').casefold() not in _EN_STOP_WORDS | _EN_COMPANY_WORDS for w in words)


@by_text
def find_narrative_persons(text: str) -> list[Hit]:
    """叙述结构只补不确定项；既有标签与高置信人名优先。"""
    from app.redaction import suspects as S
    from app.redaction.dictionaries import zh_words, zh_legal_finance
    front = '由 请 经 与 交 向 给 联系 通知 委托 授权 安排 抄送'.split()
    back = '负责 确认 签字 签署 联系 签收 回复 审核 审批 处理 表示 提出 同意 验收 沟通 协商 办理 核对'.split()
    surname = '(?:' + cn_surname_pattern() + ')'
    ending = r'(?=$|[^一-鿿]|' + '|'.join(back) + ')'
    candidates = []
    for m in re.finditer('(?:' + '|'.join(front) + ')(' + surname + r'[一-鿿]{1,2}?)' + ending, text):
        if S._is_person_name(m[1]):
            candidates.append((m.start(1), m.end(1), m[1]))
    for m in re.finditer('(' + surname + r')(?:总|工|经理|主管|老师)' + ending, text):
        candidates.append((m.start(), m.end(), m[1]))
    labels = re.compile(r'(?:抄送|联系人|经办人|收件人|签字人)[ \t]*[:：][ \t]*([^\n；;。]+)')
    for label in labels.finditer(text):
        if not re.search('[、，/]', label[1]):
            continue
        for m in re.finditer(r'[^、，/]+', label[1]):
            value = m.group().strip()
            if S._is_person_name(value):
                start = label.start(1) + m.start() + len(m.group()) - len(m.group().lstrip())
                candidates.append((start, start + len(value), value))
    plain = set(common_terms()) | zh_words() | zh_legal_finance()
    return [Hit(a, b, 'PERSON', normalize(text[a:b])) for a, b, name in sorted(set(candidates))
            if name not in plain and not S.plain_candidate(text, S.Suspect(a, b, 'person_name'))]


def find_signature_persons(texts: list[str]) -> list[tuple[int, int, int]]:
    """落款后下一非空节点的独立姓名；正文、NOTE 与追问共用。"""
    signoff = re.compile(r'(?i)(?:Best regards|Kind regards|Regards|Sincerely|Yours faithfully|Yours sincerely|Thanks and regards|此致[ \t]*敬礼)[,，.!。 \t\r\n]*$')
    whole_name = re.compile(r"\s*([一-鿿]{2,4}|[A-Z][A-Za-z'’-]*(?:[ \t]+[A-Z][A-Za-z'’-]*){1,3})\s*\Z")
    out = []
    for index, text in enumerate(texts):
        if not signoff.fullmatch(text.strip()):
            continue
        following = next((j for j in range(index + 1, len(texts)) if texts[j].strip()), None)
        if following is not None and (m := whole_name.fullmatch(texts[following])) and _valid_signature_name(m.group(1)):
            out.append((following, *m.span(1)))
    return out


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


def _extended_address_end(text: str, start: int, end: int, country: str | None) -> int:
    cn_tail = re.compile(r'[一-鿿]{0,20}(?:产业园|科技园|工业园|大厦|广场|中心|园)'
        r'(?:[ \t]*\d+(?:号楼|单元|栋|幢|层|楼|室))+')
    if country == '中国' and (tail := cn_tail.match(text, end)):
        end = tail.end()
    if tail := re.match(r'[ \t]*#\d{2}-\d{2,4}(?!\d)', text[end:]):
        end += tail.end()
    if tail := re.match(r',[ \t]*([A-Z][A-Za-z]+(?:[ \t]+[A-Z][A-Za-z]+)*)[ \t]+\d{4,6}(?!\d)', text[end:]):
        if find_country(tail.group(1)):
            end += tail.end()
    elif find_country(text[start:end]) and (tail := re.match(r'[ \t]+\d{4,6}(?!\d)', text[end:])):
        end += tail.end()
    return end


@lru_cache(maxsize=1)
def _admin_cities():
    from app.redaction.dictionaries import RESOURCES_DIR
    return frozenset(name for line in (RESOURCES_DIR / 'cn_admin_divisions.txt').read_text('utf-8').splitlines()
                     for names, level, parent in [line.split('\t')] if int(level) <= 2
                     for name in names.split('|') if len(name) >= 2)


def is_admin_city(text: str) -> bool:
    return text.strip() in _admin_cities()


@lru_cache(maxsize=1)
def _admin_trie():
    from app.redaction.dictionaries import RESOURCES_DIR
    root = {}
    for line in (RESOURCES_DIR / 'cn_admin_divisions.txt').read_text('utf-8').splitlines():
        names, level, parent = line.split('\t')
        canonical = names.split('|')[0]
        record = (int(level), parent, parent + '/' + canonical, canonical)
        for name in names.split('|'):
            if len(name) < 2:
                continue
            node = root
            for char in name:
                node = node.setdefault(char, {})
            node.setdefault('', []).append(record)
    return root


def _admin_chains(text: str):
    matches = {}
    root = _admin_trie()
    for start in range(len(text)):
        node, end = root, start
        while end < len(text) and text[end] in node:
            node = node[text[end]]
            end += 1
            for record in node.get('', ()):
                matches.setdefault(start, []).append((end, record))
    chains = []
    def extend(start, end, record, count):
        if count >= 2:
            chains.append((start, end, record[0], record[3]))
        pos = end
        while pos < len(text) and text[pos] in ' \t':
            pos += 1
        for nxt, child in matches.get(pos, ()):
            if child[0] == record[0] + 1 and child[1] == record[2]:
                extend(start, nxt, child, count + 1)
    for start, values in matches.items():
        for end, record in values:
            extend(start, end, record, 1)
    return sorted(chains, key=lambda c: (c[0], -c[1]))


@by_text
def find_addresses(text: str) -> list[Hit]:
    """第 7 步：地址标签（须有冒号）之后到行尾；遇到下一个信息标签、地址标签或带值的邮编标签时提前结束。key 为国家。"""
    text = bilingual_label_text(text)
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
    chains = _admin_chains(text)
    for m in _CN_UNLABELED_ADDRESS.finditer(text):
        if not any(h.start < m.end() and m.start() < h.end for h in hits):
            # 不把叙述前缀算进行政区名称，如“货物运至江苏省”“乙方在东川市”。
            head = re.match(r".*?(?:省|自治区|市|自治州|地区|区|县|旗)", m.group()).group()
            boundary = list(re.finditer(r"运至|送至|交至|寄往|寄至|发往|送往|位于|坐落于|在|至", head))
            start = m.start() + boundary[-1].end() if boundary else m.start()
            connected = [c for c in chains if m.start() <= c[0] < c[1] <= m.end()]
            if connected:
                start = min(connected, key=lambda c: (-(c[1] - c[0]), c[0]))[0]
            hits.append(Hit(start, m.end(), "ADDR", "中国"))
    for start, end, level, name in chains:
        # 市辖区与城市名称保留；县、县级市、乡镇街道构成敏感地址。
        if level < 3 or level == 3 and name.endswith('区'):
            continue
        tail = re.match(r'(?!的)[一-鿿0-9]{1,15}?(?:社区|工业园|开发区|园区|村)(?![一-鿿]*(?:合同|公司))', text[end:])
        if tail:
            end += tail.end()
        road = re.match(r'(?!的)[一-鿿0-9]{1,15}?(?:大道|大街|路|街|巷|弄)[ \t]*\d{1,5}号(?:\d+(?:栋|楼|室))*', text[end:])
        if road:
            end += road.end()
        if not any(h.start < end and start < h.end for h in hits):
            hits.append(Hit(start, end, 'ADDR', '中国'))
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
    # 只扩展已有无标签地址的连续尾部，不单独把园区、楼栋或邮编猜作地址。
    return [Hit(h.start, _extended_address_end(text, h.start, h.end, h.key), h.category, h.key) for h in hits]


def address_extension_parts(text: str) -> list[tuple[int, int]]:
    """只返回新扩展且被完整地址覆盖的尾部，供同代号复检别名使用。"""
    hits = find_addresses(text)
    out = []
    for pattern in (_CN_UNLABELED_ADDRESS, _EN_UNLABELED_ADDRESS):
        for match in pattern.finditer(text):
            country = '中国' if pattern is _CN_UNLABELED_ADDRESS else find_country(match.group())
            end = _extended_address_end(text, match.start(), match.end(), country)
            if end > match.end() and any(h.start < match.end() and end <= h.end for h in hits):
                out.append((match.end(), end))
    return sorted(set(out))


_PROJECT_LABEL = re.compile(r"(?:项目名称|工程名称|项目全称|(?<![A-Za-z])(?i:Project\s+Name|Project\s+Title))[ \t]*[:：][ \t]*" + _OPEN)
_PROJECT_EMPTY = frozenset({"见附件", "另行约定", "待定", "同上"})


@by_text
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
    rf"(?<![\d.])(?<!\d,)({_NUM})[ \t]?((?i:{_QTY_UNIT_RE}))(?![A-Za-z])"
    r"(?!月|工作日|营业日|自然日|银行日|工作|星期|周|日|天|小时|季度|年|百分点)"
)


def _qty_key(unit: str, num: str) -> str:
    value = _decimal(num)
    shown = format(value.normalize(), "f") if value is not None else num
    return f"QTY|{unit.casefold()}|{shown}"


@by_text
def find_quantities(text: str, column: str | None = None) -> list[Hit]:
    """数字（可含千分位和小数）后紧跟计量单位；只替换数字，单位保留。column="QTY" 时整格只有一个数字也算数量。"""
    hits = [Hit(m.start(1), m.end(1), "QTY", _qty_key(m.group(2), m.group(1))) for m in _QTY.finditer(text)]
    if not hits and column == "QTY":
        bare = _BARE_NUMBER.match(text)
        if bare:
            hits.append(Hit(bare.start(1), bare.end(1), "QTY", _qty_key("", bare.group(1))))
    return hits
