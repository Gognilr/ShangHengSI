"""签约方、简称与其他企业名称的识别（SPEC 9.3 第 2、3 步，9.4）。

全部基于规则：
- 签约方：在“甲方、买方、Buyer、Consignee”等标签后的冒号之后，取以企业后缀结尾的名称。
- 简称：紧跟在已识别名称之后的“（以下简称“X”）”“(the "X")”等结构；通用角色词不作为简称。
- 其他企业名称：正文中带“有限公司”等明确后缀的中文名称，以及以英文企业后缀结尾的连续大写开头单词。
  英文名称切词时，汉字与中文标点同空白一样是分隔符（“收函方：Aurora Fictive Energy Co., Ltd.”整体识别）；
  名称中间可以有一组括号括起的地区名（“Northwind Fictive (Hong Kong) Limited”整体识别）。
  “公司、厂、集团”等宽泛后缀只在签约方标签后使用，正文中不据此自动替换（留给 9.5 疑似遗漏，M4；紧跟简称结构的除外，见下）。
- 名称延伸到同一名称内的最后一个后缀（含分公司、支行等），不跨越分隔符和“以下简称”（R1-04）。
- 开户行标签后没有企业后缀、以“银行、支行”等结尾的中文值也作为银行主体（R1-12）。
- 正文中以宽泛后缀结尾、紧跟简称结构的名称也作为主体（R1-13），名称起点同疑似遗漏。
"""
import re
import unicodedata
from functools import lru_cache

from app.redaction.dictionaries import find_country, find_org_type, _country_index, common_terms, RESOURCES_DIR
from app.redaction.placeholders import BUYER, OTHER, SELLER

# ---------------------------------------------------------------- 标签

_LABELS: list[tuple[str, list[str]]] = [
    (BUYER, ["买方", "需方", "采购方", "购货方", "买受人", "Buyer", "Purchaser", "Importer",
             "采购人", "采购单位", "发包人", "发包方", "委托方", "委托人", "委托单位", "承租方", "承租人", "招标人", "招标单位",
             "建设单位", "业主", "业主单位", "客户", "Client", "Customer", "Employer", "Owner", "Lessee", "Licensee"]),
    (SELLER, ["卖方", "供方", "供应商", "供货方", "销售方", "出卖人", "Seller", "Supplier", "Vendor", "Exporter",
              "承包人", "承包方", "受托方", "受托人", "受托单位", "出租方", "出租人", "中标人", "中标单位", "投标人", "投标单位",
              "施工单位", "承建单位", "服务方", "服务提供方", "Contractor", "Lessor", "Licensor", "Service Provider"]),
    ("监理方", ["监理单位", "监理人", "监理方"]),
    ("代理机构", ["招标代理机构", "采购代理机构", "代理机构"]),
    ("甲方", ["甲方", "Party A"]),
    ("乙方", ["乙方", "Party B"]),
    ("丙方", ["丙方", "Party C"]),
    ("收货人", ["收货人", "Consignee"]),
    ("通知方", ["通知方", "通知人", "Notify Party"]),
    ("银行", ["开户行", "开户银行", "收款银行", "受益人银行", "Beneficiary Bank", "Bank Name", "Bank"]),
    ("受益人", ["受益人", "Beneficiary"]),
    (OTHER, ["Company Name"]),
]
_LABEL_CANON = {word.casefold(): canon for canon, words in _LABELS for word in words}
_LABEL_WORDS = sorted(_LABEL_CANON, key=len, reverse=True)
_LABEL_RE = re.compile(
    r"(?:^|(?<=[\s,，;；。、(（]))(?i:("
    + "|".join(re.escape(w) for w in _LABEL_WORDS)
    + r"))\s*(?:名称|全称|(?i:company\s+name|name))?\s*(?:[（(][^）)\n]{1,20}[）)])?\s*[:：]",
    re.MULTILINE,
)


@lru_cache(maxsize=1)
def _bilingual_label_patterns():
    pairs = [(cn, en) for _, words in _LABELS for cn in words if re.search(r'[一-鿿]', cn)
             for en in words if en.isascii()]
    pairs += [(cn, en) for chinese, english in (
        ('联系人 经办人 收件人', ['Contact', 'Contact Person', 'Contact Name', 'Name', 'Attn.']),
        ('授权代表 委托代理人', ['Authorized Representative', 'Representative']),
        ('法定代表人 代表人', ['Legal Representative', 'Representative']),
        ('签字 签名 签字人', ['Signature', 'Signed by']),
        ('合同编号 合同号', ['Contract No.', 'Contract Number']),
        ('订单号 订单编号', ['Order No.', 'Order Number']),
        ('发票号 发票编号', ['Invoice No.', 'Invoice Number']),
        ('提单号 提单编号', ['B/L No.', 'Bill of Lading No.']),
        ('信用证号 信用证编号', ['L/C No.', 'Credit No.']),
        ('报关单号 报关单编号', ['Customs Declaration No.']),
        ('招标编号 招标项目编号', ['Tender No.', 'Bid No.']),
        ('报价单号 报价编号', ['Quotation No.', 'Quote No.']),
        ('运单号', ['Waybill No.']),
        ('集装箱号', ['Container No.']),
        ('银行账号 收款账号 付款账号 开户行账号 账号 帐号 账户', ['Account No.', 'Account Number', 'Account', 'A/C']),
        ('地址 注册地址 通讯地址 办公地址 联系地址 收货地址', ['Address', 'Registered Address']),
        ('电话 联系电话 手机 传真', ['Tel.', 'Telephone', 'Phone', 'Mobile', 'Fax']),
        ('邮箱 电子邮箱 邮件', ['Email', 'E-mail']),
        ('护照号 护照号码', ['Passport No.']),
        ('税号 纳税人识别号', ['Tax ID', 'VAT No.', 'TIN']),
        ('注册号 工商注册号 营业执照注册号', ['Registration No.']),
        ('组织机构代码', ['Organization Code']),
        ('微信 微信号', ['WeChat', 'WeChat ID']),
        ('行号 联行号 开户行行号', ['CNAPS', 'Routing No.', 'Sort Code']),
        ('项目编号 项目号', ['Project No.']),
    ) for cn in chinese.split() for en in english]
    patterns = []
    for cn, en in sorted(set(pairs), key=lambda pair: -(len(pair[0]) + len(pair[1]))):
        latin = re.escape(en).replace(r'\ ', r'[ \t]+').replace(r'\.', r'\.?')
        patterns.append(re.compile(rf'(?P<cn>{re.escape(cn)})[ \t]+(?P<en>{latin})(?=[ \t]*[:：])'
            rf'|(?P<enfirst>{latin})[ \t]*(?P<cnlast>{re.escape(cn)})(?=[ \t]*[:：])', re.I))
    return tuple(patterns)


def bilingual_label_text(text: str) -> str:
    """识别时用等长空白遮盖双语标签的英文部分，原文、值及偏移不变。"""
    if not re.search(r'[:：]', text) or not re.search(r'[一-鿿]', text) or not re.search(r'[A-Za-z]', text):
        return text
    for pattern in _bilingual_label_patterns():
        matches = list(pattern.finditer(text))
        if matches:
            chars = list(text)
            for match in matches:
                start, end = match.span('en' if match.group('en') else 'enfirst')
                chars[start:end] = ' ' * (end - start)
            text = ''.join(chars)
    return text

# 通用角色词：作为简称出现时不替换（9.3 第 3 步）
GENERIC_WORDS = frozenset(w.casefold() for w in (
    *_LABEL_CANON, "双方", "各方", "一方", "本合同", "合同", "本协议", "协议", "公司", "本公司", "货物",
    "集团", "本集团", "总公司", "研究院", "研究所",  # Q-142：普通词作简称时不全文替换
    "Party", "Parties", "Company", "Contract", "Agreement", "Goods", "Products", "Seller", "Buyer",
))

# ---------------------------------------------------------------- 企业后缀

_CN_SUFFIX = "股份有限公司|有限责任公司|集团有限公司|有限公司|集团公司|集团|公司|研究院|研究所|厂"
_CN_STRONG_SUFFIX = re.compile("股份有限公司|有限责任公司|集团有限公司|有限公司")
# R4：正文只提示窄表中的机构；标签值另允许单字后缀等，不能把普通句子登记成已记住名称。
INSTITUTION_TEXT_SUFFIXES = (
    "卫生健康委员会", "管理委员会", "人民政府", "卫生院", "医院", "幼儿园", "小学", "中学", "大学", "学院", "学校",
    "研究中心", "委员会", "管委会", "事务所", "合作社", "协会", "学会", "商会", "基金会", "联合会",
)
_INSTITUTION_SUFFIXES = tuple(sorted((*INSTITUTION_TEXT_SUFFIXES, "诊所", "中心", "出版社", "电视台", "局", "厅", "署", "院"), key=len, reverse=True))
_INSTITUTION_RE = re.compile("|".join(_INSTITUTION_SUFFIXES))
_EN_SUFFIX = (
    r"GmbH\s+&\s+Co\.\s+KG|Co\.\s+W\.L\.L\.|Ltd\.\s+[ŞS]ti\.|Sp\.\s+z\s+o\.\s*o\.|"
    r"S\.A\.\s+de\s+C\.V\.|A\.Ş\.|A\.S\.|s\.r\.o\.|a\.s\.|Kft\.|Zrt\.|Nyrt\.|S\.R\.L\.|"
    r"S\.A\.R\.L\.|S\.A\.S\.|S\.p\.A\.|Ltda\.|W\.L\.L\.|ApS|A/S|Oy|AB|ASA|K\.K\.|Bhd\.|Est\.|SpA|WLL|SRL|SARL|"
    r"Co\.\s?,\s?Ltd\.?|Co\.\s?Limited|Company\s+Limited|Pte\.?\s?Ltd\.?|Pvt\.?\s?Ltd\.?|Sdn\.?\s?Bhd\.?|"
    r"L\.L\.C\.?|FZ-LLC|LLC|LLP|PLC|Limited|Ltd\.?|Inc\.?|Corp\.?|Corporation|GmbH|S\.A\.|S\.r\.l\.|"
    r"B\.V\.|N\.V\.|FZCO|FZE|PJSC|JSC|OOO"
)
_EN_SUFFIX_RE = re.compile(rf"(?<![A-Za-z])(?:(?i:{_EN_SUFFIX})|AG)(?![A-Za-z])")
_SUFFIX_RE = re.compile(rf"(?:{_CN_SUFFIX})|(?<![A-Za-z])(?:(?i:{_EN_SUFFIX})|AG)(?![A-Za-z])")
_NAME_FORBIDDEN = set("：:；;。，、“”\"「」『』\n\t")
_CN_CHAR = re.compile(r"[一-鿿]")
_EN_WORD_OK = re.compile(r"^(?:[A-ZÀ-ÖØ-Þ0-9][A-Za-zÀ-ÖØ-öø-ÿĀ-ſ0-9&'.\-]*|&|of|and|de|du|la|for|ve|z)$")
# 英文名称切词：空白、汉字、中文标点（含全角形式）和下列半角标点都是分隔符，使“收函方：Aurora Energy Co., Ltd.”中的
# 冒号前不会把“Aurora”并进无效单词；撇号、句点、逗号、连字符、&留在单词内
# 名称中间可以有一组括号括起的 1–3 个大写开头的单词（“Northwind (Hong Kong) Limited”“Example (HK) Co., Ltd.”），整体算一个单词
_EN_PAREN_GROUP = r"\([A-Z][A-Za-z.&\-]*(?: [A-Z][A-Za-z.&\-]*){0,2}\)"
_EN_TOKEN_RE = re.compile(rf"{_EN_PAREN_GROUP}|[^\s一-鿿　-〿＀-￯：:；;\"“”‘「」『』()（）]+")
_EN_PAREN_OK = re.compile(rf"^{_EN_PAREN_GROUP}$")
_EN_CONNECTORS = frozenset({"&", "of", "and", "de", "du", "la", "for", "ve", "z"})
_EN_LEADING_NOISE = frozenset(w.casefold() for w in (
    "The", "To", "From", "By", "With", "Messrs", "Messrs.", "M/s", "Dear", "Attn", "Seller", "Buyer", "Supplier",
    "Purchaser", "Vendor", "Consignee", "Beneficiary", "Party", "Bank", "Name", "Company",
))
# 中文名称前常见的虚词：在正文中识别企业名称时去掉（“与XX有限公司签订”）
_CN_LEADING_NOISE = set("与和及由向对将经在为给被把自从同跟即系是")
# 正文企业名称的起点边界词（Q-56，设计 M4.md 2.2）：向前取汉字时遇到即停止，名称从边界词之后开始；多字的优先
_CN_BOUNDARY_WORDS = (
    "我方", "我司", "本司", "我公司", "本公司", "贵方", "贵司", "贵公司", "对方",
    "现指定", "现委托", "兹委托", "特委托", "指定", "授权", "聘请", "选定",
    "经由", "委托", "通过", "根据", "按照", "依据", "代表", "联系",
    "由", "向", "与", "和", "及", "同", "跟", "经", "致", "自", "从", "为", "对", "将", "把", "被", "给", "在", "即", "系", "是",
)
# 企业名称中常见的、含边界字的词：这些词内部的字不当作边界（否则“新兴经济贸易有限公司”会被截成“济贸易有限公司”而漏掉前半）
_CN_BOUNDARY_PROTECTED = (
    "经贸", "经济", "经营", "经销", "经纪", "经理", "自由", "自贸", "自动", "自然", "自强", "同方", "同心", "同创", "同兴",
    "同济", "和平", "和谐", "和顺", "和合", "和兴", "为民", "从化", "在线", "对外", "给水", "即时",
)

# 名称延伸（Q-134 ⑤，设计 R1.md 第五节）：取到第一个后缀后，同一名称内后面还有企业后缀时延伸到最后一个，最长 60 字。
# 延伸时后缀表增加分支机构后缀；正文（未带标签）只认明确后缀和分支机构后缀，宽泛的“公司、厂、集团”只在签约方标签后认（Q-137 ①）
_NAME_MAX = 60
_CN_BRANCH_SUFFIX = "分公司|支行|分行|营业部|办事处|分厂"
_EXTEND_LABELED_RE = re.compile(rf"{_CN_BRANCH_SUFFIX}|{_SUFFIX_RE.pattern}")
_EXTEND_TEXT_RE = re.compile(rf"{_CN_BRANCH_SUFFIX}|{_CN_STRONG_SUFFIX.pattern}")
# 延伸不跨越：分隔符、引号、空白、填空括号、“（以下简称”等简称结构、不成对的右括号
_EXTEND_STOP_CHARS = set("：:；;。，,、“”\"'‘’「」『』【】［］[]〖〗")
_ALIAS_OPEN_RE = re.compile(r"[（(]\s*(?:以下|下称|简称|(?i:hereinafter|referred|the\s))")
# 也不跨越边界词（“A有限公司与B有限公司”）和表示从属、泛指的词（“XX有限公司下属分公司”不延伸，Q-137 ②）
_EXTEND_STOP_WORDS = (*_CN_BOUNDARY_WORDS, "或", "并", "且", "的", "等", "各", "其", "下属", "所属", "旗下", "负责", "成立", "设立")

# 开户行（R1-12，Q-139 ②）：这些标签后的中文值以银行类结尾时，没有企业后缀也作为银行主体；单独的“Bank”不在此列
_BANK_VALUE_LABELS = frozenset(w.casefold() for w in ("开户行", "开户银行", "收款银行", "受益人银行", "Bank Name", "Beneficiary Bank"))
_BANK_VALUE_SUFFIXES = ("银行", "支行", "分行", "分理处", "营业部", "储蓄所")
_BANK_VALUE_MIN_HAN = 4

# ---------------------------------------------------------------- 简称

_QUOTE_OPEN = "“\"'‘「『"
_QUOTE_CLOSE = "”\"'’」』"
_ALIAS_RE = re.compile(
    r"[（(]\s*(?:以下简称|以下称为|以下称|下称|简称|"
    r"(?i:hereinafter\s+(?:referred\s+to\s+as|called)|hereinafter|the|referred\s+to\s+as))?\s*[:：,]?\s*"
    rf"[{_QUOTE_OPEN}]([^{_QUOTE_CLOSE}（）()\n]{{1,30}})[{_QUOTE_CLOSE}]\s*[）)]"
)


def normalize(name: str) -> str:
    """名称的规范形式：全角转半角、去掉空白、不区分大小写。用于判断是否同一对象。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).casefold()


@lru_cache(maxsize=1)
def industry_words() -> tuple[str, ...]:
    return tuple(sorted((s.strip() for s in (RESOURCES_DIR / "industry_words.txt").read_text(encoding="utf-8").splitlines()
                         if s.strip() and not s.startswith("#")), key=len, reverse=True))


def derived_names(name: str) -> tuple[list[str], list[str]]:
    """主体部分、字号：返回（自动别名，二字疑似别名），不登记为独立主体。"""
    if not is_chinese_name(name):
        return [], []
    body = re.sub(rf"(?:{_CN_SUFFIX}|{_CN_BRANCH_SUFFIX})+$", "", name)
    brand = body
    _, countries = _country_index()
    places = sorted([p for p, country in countries.items() if country == "中国" and re.fullmatch(r"[一-鿿]+", p)]
                    + ["华东", "华南", "华北", "华中", "西南", "西北", "东北"], key=len, reverse=True)
    while (place := next((p for p in places if brand.startswith(p)), None)):
        brand = brand[len(place):]
    while (word := next((w for w in industry_words() if brand.endswith(w)), None)):
        brand = brand[:-len(word)]
    blocked = set(industry_words()) | set(common_terms()) | {"中国", "国际", "东方", "中华"}
    auto = [body] if len(body) >= 4 and body not in blocked else []
    if brand not in blocked and len(brand) >= 3:
        auto.append(brand)
    short = [brand] if brand not in blocked and len(brand) == 2 else []
    return list(dict.fromkeys(auto)), short


def bilingual_links(names: list[str]) -> dict[str, str]:
    """英文规范名称 → 中文规范名称；只关联双向唯一的拼音前缀。"""
    from app.redaction.dictionaries import hanzi_pinyin
    chinese = {normalize(n): n for n in names if is_chinese_name(n)}
    english = {normalize(n): n for n in names if not is_chinese_name(n) and _EN_SUFFIX_RE.search(n)}
    if not chinese or not english:
        return {}
    readings = hanzi_pinyin()
    syllables = {p for values in readings.values() for p in values}
    places = _country_index()[1]

    def split_count(word: str) -> int:
        counts = {0: 0}
        for end in range(1, len(word) + 1):
            options = [count + 1 for start, count in list(counts.items())
                       if start < end and word[start:end] in syllables]
            if options:
                counts[end] = max(options)
        return counts.get(len(word), 0)

    def english_prefixes(name: str) -> set[str]:
        head = name[:_EN_SUFFIX_RE.search(name).start()]
        words = re.findall(r'[A-Za-z]+', head)
        variants = [words]
        # 地名可有可无：同时容纳英文保留地名、中文省略地名的写法（T-53 #18）。
        for end in range(1, len(words)):
            if ' '.join(words[:end]).casefold() in places:
                variants.append(words[end:])
        out = set()
        for variant in variants:
            prefix, count = '', 0
            for word in variant:
                n = split_count(word.casefold())
                if not n:
                    break
                prefix += word.casefold()
                count += n
            if count >= 2:
                out.add(prefix)
        return out

    def chinese_bodies(name: str) -> set[str]:
        body = re.sub(rf'(?:{_CN_SUFFIX}|{_CN_BRANCH_SUFFIX})+$', '', name)
        bodies = {body}
        while (place := next((p for p in sorted(places, key=len, reverse=True)
                              if re.fullmatch(r'[一-鿿]+', p) and body.startswith(p)), None)):
            body = body[len(place):]
            bodies.add(body)
        return bodies

    def matches(body: str, joined: str) -> bool:
        positions = {0}
        for count, ch in enumerate(body, start=1):
            positions = {pos + len(p) for pos in positions for p in readings.get(ch, ())
                         if joined.startswith(p, pos)}
            if count >= 2 and len(joined) in positions:
                return True
            if not positions:
                break
        return False

    bodies = {key: chinese_bodies(name) for key, name in chinese.items()}
    prefixes = {key: english_prefixes(name) for key, name in english.items()}
    pairs = [(en, cn) for en, joined_variants in prefixes.items() for cn, variants in bodies.items()
             if any(matches(body, joined) for body in variants for joined in joined_variants)]
    return {en: cn for en, cn in pairs if sum(e == en for e, c in pairs) == 1
            and sum(c == cn for e, c in pairs) == 1}


def name_pattern(surface: str, *, case_sensitive: bool = False) -> re.Pattern:
    """名称的匹配正则：英文不区分大小写、忽略多余空格（句点、逗号后的空格可有可无）；首尾是英文字母或数字时要求单词边界。"""
    s = re.sub(r"([.,])\s+", r"\1", surface.strip())
    parts: list[str] = []
    for ch in s:
        if ch.isspace():
            if not parts or parts[-1] != r"\s+":
                parts.append(r"\s+")
        elif ch in ".,":
            parts.append(re.escape(ch) + r"\s*")
        else:
            parts.append(re.escape(ch))
    body = "".join(parts)
    if body.endswith(r"\s*"):
        body = body[:-3]
    if s[:1].isascii() and s[:1].isalnum():
        body = r"(?<![A-Za-z0-9])" + body
    if s[-1:].isascii() and s[-1:].isalnum():
        body += r"(?![A-Za-z0-9])"
    has_latin = any(c.isascii() and c.isalpha() for c in s)
    flags = re.IGNORECASE if has_latin and not case_sensitive else 0
    return re.compile(body, flags)


def is_chinese_name(name: str) -> bool:
    return bool(_CN_CHAR.search(name)) and bool(re.search(f"(?:{_CN_SUFFIX})$", name))


# ---------------------------------------------------------------- 识别


def _valid_prefix(pre: str) -> bool:
    pre = pre.rstrip(" ,")
    if not pre or len(pre) > 80 or any(c in _NAME_FORBIDDEN for c in pre) or "," in pre:
        return False
    if "简称" in pre or "hereinafter" in pre.casefold():
        return False
    if _CN_CHAR.search(pre) and len(pre) < 2:
        return False
    return True


def extract_org_name(text: str, pos: int) -> tuple[int, int] | None:
    """从 pos 起（标签冒号之后）取以企业后缀结尾的名称，返回 (start, end)；不是企业名称时返回 None。"""
    line_end = text.find("\n", pos)
    limit = min(len(text) if line_end < 0 else line_end, pos + 200)
    window = text[pos:limit]
    lead = len(window) - len(window.lstrip())
    prefixed = _PREFIXED_ORG.match(window, lead)
    if prefixed:
        return pos + prefixed.start(), pos + prefixed.end()
    # 填空括号“乙方：【××有限公司】”：开括号不属于名称（Q-134 ①；与 detect.FILL_OPEN 相同）
    if window[lead:lead + 1] in ("【", "［", "[", "〖"):
        lead += 1
        lead += len(window[lead:]) - len(window[lead:].lstrip(" \t"))
    for m in _SUFFIX_RE.finditer(window, lead):
        if not _foreign_suffix_allowed(window, lead, m):
            continue
        if not window[lead:m.start()].strip(" ,"):
            continue
        if not _valid_prefix(window[lead:m.start()]):
            return None
        end = m.end()
        while (more := _SUFFIX_RE.match(window, end)) and more.end() > end:  # “集团有限公司”等连续后缀
            end = more.end()
        end = _extend_to_last_suffix(window, lead, end, _EXTEND_LABELED_RE)  # “××集团山东有限公司德州分公司”
        return pos + lead, pos + end
    for m in _INSTITUTION_RE.finditer(window, lead):
        prefix = window[lead:m.start()]
        if not _valid_prefix(prefix) or len(_CN_CHAR.findall(prefix)) < 2:
            continue
        end = _extend_to_last_suffix(window, lead, m.end(), _EXTEND_LABELED_RE)
        name = window[lead:end]
        if len(name) > _NAME_MAX or any(w in name for w in (*_EXTEND_STOP_WORDS, "应", "须", "提供", "于")):
            continue
        tail = window[end:]
        if tail and not (tail[0].isspace() or tail[0] in "，,。；;、）)】］]〗" or _ALIAS_OPEN_RE.match(tail)):
            continue
        return pos + lead, pos + end
    return None


def _protected_mask(seg: str) -> list[bool]:
    """seg 中落在受保护词（经贸、和平等）内部的字。"""
    blocked = [False] * len(seg)
    for word in _CN_BOUNDARY_PROTECTED:
        i = seg.find(word)
        while i >= 0:
            for k in range(i, i + len(word)):
                blocked[k] = True
            i = seg.find(word, i + 1)
    return blocked


def _extend_to_last_suffix(text: str, start: int, end: int, suffix_re: re.Pattern) -> int:
    """名称 text[start:end] 已以企业后缀结尾。同一名称内后面还有企业后缀时，返回最后一个后缀的结尾；否则返回 end。"""
    seg = text[end:min(len(text), start + _NAME_MAX)]
    blocked = _protected_mask(seg)
    depth = 0
    closed = [True] * (len(seg) + 1)  # closed[i]：seg[:i] 的括号是否成对（名称不能停在括号中间）
    stop = len(seg)
    for i, ch in enumerate(seg):
        if ch in _EXTEND_STOP_CHARS or ch.isspace():
            stop = i
            break
        if ch in "（(":
            if _ALIAS_OPEN_RE.match(seg, i):
                stop = i
                break
            depth += 1
        elif ch in "）)":
            if depth == 0:
                stop = i
                break
            depth -= 1
        elif depth == 0 and not blocked[i] and seg.startswith(_EXTEND_STOP_WORDS, i):
            stop = i
            break
        closed[i + 1] = depth == 0
    best = end
    for m in suffix_re.finditer(seg, 0, stop):
        if closed[m.end()]:
            best = end + m.end()
    return best


def _bank_value(text: str, pos: int) -> tuple[int, int] | None:
    """开户行标签冒号之后（可跨一个填空括号）的中文银行名称，没有企业后缀：取到同一段文字中最后一个
    “银行、支行、分行、分理处、营业部、储蓄所”为止。不跨越分隔符、空白、换行和简称结构，最长 60 字，至少 4 个汉字。"""
    i = pos
    while i < len(text) and text[i] in " \t　":
        i += 1
    if text[i:i + 1] in ("【", "［", "[", "〖"):  # 与 extract_org_name 相同
        i += 1
        while i < len(text) and text[i] in " \t　":
            i += 1
    start = i
    depth = 0
    best = None
    while i < len(text) and i - start < _NAME_MAX:
        ch = text[i]
        if ch in _EXTEND_STOP_CHARS or ch.isspace() or _ALIAS_OPEN_RE.match(text, i):
            break
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            if depth == 0:
                break
            depth -= 1
        i += 1
        if depth == 0 and text.endswith(_BANK_VALUE_SUFFIXES, start, i):
            best = i
    if best is None or len(_CN_CHAR.findall(text[start:best])) < _BANK_VALUE_MIN_HAN:
        return None
    return start, best


def find_labeled_parties(text: str) -> list[tuple[str, int, int]]:
    """节点原文中“标签：名称”形式的签约方：[(规范化标签, start, end)]。
    开户行标签取不到带企业后缀的名称时，再按银行类结尾取值（R1-12）。"""
    text = bilingual_label_text(text)
    out = []
    for m in _LABEL_RE.finditer(text):
        span = extract_org_name(text, m.end())
        if span is None and m.group(1).casefold() in _BANK_VALUE_LABELS:
            span = _bank_value(text, m.end())
        if span is None and m.group(1).casefold() == 'company name':
            value = re.match(r'[ \t]*([A-Z][A-Za-z\'-]*(?:[ \t]+[A-Z][A-Za-z\'-]*){0,7})', text[m.end():])
            if value:
                span = (m.end() + value.start(1), m.end() + value.end(1))
        if span:
            label = _LABEL_CANON[m.group(1).casefold()]
            if label in ("甲方", "乙方"):
                note = re.search(r"[（(]([^）)]+)[）)]", m.group())
                if note and any(w in note.group(1) for w in ("采购方", "买方", "需方", "发包人", "委托方", "承租方")):
                    label = BUYER
                elif note and any(w in note.group(1) for w in ("供方", "卖方", "供应商", "承包人", "受托方", "出租方")):
                    label = SELLER
            out.append((label, *span))
    return out


def find_aliases(text: str) -> list[tuple[int, int, int]]:
    """简称结构：[(结构起点, 简称 start, 简称 end)]。由调用方判断结构前面紧挨着哪个名称。"""
    out = []
    for m in _ALIAS_RE.finditer(text):
        alias = m.group(1).strip()
        if not alias or alias.casefold() in GENERIC_WORDS:
            continue
        start = m.start(1) + (len(m.group(1)) - len(m.group(1).lstrip()))
        out.append((m.start(), start, start + len(alias)))
    return out


# 带简称定义的宽泛后缀名称（R1-13，Q-139 ③）：长的在前
_BROAD_SUFFIXES = tuple(sorted(("集团公司", "研究院", "研究所", "集团", "公司", "厂", *INSTITUTION_TEXT_SUFFIXES), key=len, reverse=True))


def find_broad_suffix_before_alias(text: str) -> list[tuple[int, int]]:
    """正文中以宽泛后缀（公司、集团、厂、研究院、研究所）结尾、后面紧跟简称结构（中间只允许空白）的位置：
    [(后缀 start, 后缀 end)]。以“有限公司”等明确后缀结尾的由 find_other_org_names 负责，不在此列；
    名称起点由调用方按疑似遗漏的边界确定（engine 第 2 步）。简称是否为通用角色词不影响这里的结果。"""
    out = []
    for m in _ALIAS_RE.finditer(text):
        end = len(text[:m.start()].rstrip(" \t　"))
        suffix = next((s for s in _BROAD_SUFFIXES if text.endswith(s, 0, end)), None)
        if suffix is None or text.endswith(("有限公司", "有限责任公司"), 0, end):
            continue
        out.append((end - len(suffix), end))
    return out


def _name_start_after_boundary(text: str, start: int, suffix_start: int, min_han: int = 2) -> int:
    """text[start:suffix_start] 是企业后缀前向前取到的汉字（含括号）。返回最后一个边界词之后的位置（Q-56）。

    括号内的字和受保护词内部的字不算边界；边界词之后不足 min_han 个汉字时返回 start（按 M2 的做法整段处理，宁多勿漏）。
    疑似遗漏识别（suspects.py）传 min_han=0：有边界词就从边界词之后算起，由调用方判断名称是否够长。
    """
    run = text[start:suffix_start]
    blocked = _protected_mask(run)
    depth = 0
    best = 0
    for i, ch in enumerate(run):
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        elif depth == 0 and not blocked[i]:
            for word in _CN_BOUNDARY_WORDS:
                if run.startswith(word, i):
                    best = i + len(word)
                    break
    if best and sum(1 for c in run[best:] if _CN_CHAR.match(c)) >= min_han:
        return start + best
    return start


def find_other_org_names(text: str) -> list[tuple[int, int]]:
    """正文中未带标签的企业名称：[(start, end)]。"""
    out = [m.span() for m in _PREFIXED_ORG.finditer(text)]
    last_end = 0
    for m in _CN_STRONG_SUFFIX.finditer(text):
        if m.start() < last_end:  # 已被上一个名称的延伸覆盖（“××银行股份有限公司××支行”）
            continue
        # 向前取连续的汉字（含括号），不越过上一个名称。无法确定名称起点时宁可多取（如“本合同由XX有限公司”整体替换），
        # 也不在名称中间截断而漏掉一部分真实名称
        start = m.start()
        while (start > last_end and start > m.start() - 30
               and (_CN_CHAR.match(text[start - 1]) or text[start - 1] in "（）()")):
            start -= 1
        last_end = m.end()
        cut = _name_start_after_boundary(text, start, m.start())
        if cut > start:
            start = cut
        else:  # 没有可用的边界词：沿用 M2 的做法，只去掉开头的虚词
            while start < m.start() - 2 and text[start] in _CN_LEADING_NOISE:
                start += 1
        while start < m.start() and text[start] in "）)":
            start += 1
        if m.start() - start >= 2:
            last_end = _extend_to_last_suffix(text, start, m.end(), _EXTEND_TEXT_RE)
            out.append((start, last_end))
    for m in _EN_SUFFIX_RE.finditer(text):
        head = text[max(0, m.start() - 160):m.start()]
        head = head[head.rfind("\n") + 1:]  # 不跨行
        offset = m.start() - len(head)
        tokens = list(_EN_TOKEN_RE.finditer(head))
        kept: list[re.Match] = []
        gap_end = len(head)  # 单词之间只能隔空白；隔着汉字或标点（“收函方：Aurora”）说明名称在此处起头
        for i, tok in enumerate(reversed(tokens)):
            word = tok.group().rstrip(",") if i == 0 else tok.group()  # “ABC, Inc.”
            if (head[tok.end():gap_end].strip() or len(kept) >= 8
                    or not (_EN_WORD_OK.match(word) or _EN_PAREN_OK.match(word))):
                break
            # 括号组后面紧跟连接词时是正文里的说明（“the Contract (Annex A) and XX LLC”），不是名称的一部分
            if word.startswith("(") and kept and kept[-1].group() in _EN_CONNECTORS:
                break
            kept.append(tok)
            gap_end = tok.start()
        kept.reverse()
        while kept and (kept[0].group() in _EN_CONNECTORS or kept[0].group().casefold() in _EN_LEADING_NOISE
                        or kept[0].group().startswith("(")):
            kept.pop(0)
        if kept and any(c.isupper() for c in kept[0].group()):
            start = offset + kept[0].start()
            if _foreign_suffix_allowed(text, start, m):
                out.append((start, m.end()))
    for m in re.finditer(r"(?<![A-Za-z])(?:Messrs\.|M/s)[ \t]+([A-Z][A-Za-z'’-]*(?:[ \t]+[A-Z][A-Za-z'’-]*){0,7})", text):
        if not any(a < m.end(1) and m.start(1) < b for a, b in out):
            out.append(m.span(1))
    return out


# V2-B-02：前置企业形式只认明确引号或 PT/CV 后的 2～8 个首字母大写词。
_LATIN_NAME_WORD = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿĀ-ſ'\-]*"
_PREFIXED_ORG = re.compile(
    r'(?<![\w])(?:ООО|АО|ПАО|ЗАО|ОАО|ИП|ТОО|OOO|ZAO|OAO|TOO)[ \t]+(?:«[^»\n]{1,60}»|"[^"\n]{1,60}"|“[^”\n]{1,60}”)'
    rf"|(?<![\w])(?:PT|CV)[ \t]+{_LATIN_NAME_WORD}(?:[ \t]+(?!Tbk\b){_LATIN_NAME_WORD}){{1,7}}(?:[ \t]+Tbk)?"
)
_SHORT_SUFFIXES = frozenset({"AB", "ASA", "Oy", "SpA", "WLL", "SRL", "SARL"})


def _foreign_suffix_allowed(text: str, start: int, match: re.Match) -> bool:
    suffix = match.group()
    before = text[start:match.start()].strip()
    if suffix.casefold() in {s.casefold() for s in _SHORT_SUFFIXES}:
        if suffix not in _SHORT_SUFFIXES:
            return False
        words = re.findall(_LATIN_NAME_WORD, before)
        if len(words) < 2 or re.match(r"[ \t]+[\w]", text[match.end():]):
            return False
    if suffix == "A.S.":
        return bool(re.search(r"(?:Sanayi|Ticaret|Tic\.|San\.)$", before))
    if suffix.casefold() == "est.":
        return bool(re.search(r"(?:Trading|General|Commercial)$", before, re.IGNORECASE))
    return True


def detect_country(lines: list[str], names: list[str]) -> str | None:
    """在主体所在行及其后的信息块中查找国家（9.4）；先去掉名称本身，避免名称中的地名干扰。"""
    for line in lines:
        for name in sorted(names, key=len, reverse=True):
            line = name_pattern(name).sub(" ", line)
        country = find_country(line)
        if country:
            return country
    return None


def _is_chinese_bank_name(name: str) -> bool:
    """R1-12 开户行取到的中文银行名称：以银行类结尾且含“行”（“示例行虚构园区支行”）。"""
    return bool(_CN_CHAR.search(name)) and name.endswith(_BANK_VALUE_SUFFIXES) and "行" in name


def default_country(name: str) -> str | None:
    """块中找不到国家时：以中文企业后缀结尾的中文名称、中文银行名称视为中国企业。"""
    if re.search(r"(?:A\.Ş\.|Ltd\.\s+Şti\.)$", name, re.IGNORECASE):
        return "土耳其"
    if re.search(r"Sp\.\s+z\s+o\.\s*o\.$", name, re.IGNORECASE):
        return "波兰"
    if re.search(r"(?:Kft\.|Zrt\.)$", name, re.IGNORECASE):
        return "匈牙利"
    if name.startswith("PT "):
        return "印度尼西亚"
    return "中国" if (is_chinese_name(name) or _is_chinese_bank_name(name)
                    or (_CN_CHAR.search(name) and name.endswith(_INSTITUTION_SUFFIXES))) else None


def org_type(name: str) -> str:
    """按 org_types.yaml 判断；词典不命中的中文银行名称（“××行××支行”）固定为银行（R1-12）。"""
    found = find_org_type(name)
    if found == "类型未知" and _is_chinese_bank_name(name):
        return "银行"
    return found


__all__ = [
    "GENERIC_WORDS", "OTHER", "default_country", "detect_country", "extract_org_name", "find_aliases",
    "find_broad_suffix_before_alias", "find_labeled_parties", "find_other_org_names", "is_chinese_name", "name_pattern", "normalize", "org_type",
]
