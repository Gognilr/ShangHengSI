"""签约方、简称与其他企业名称的识别（SPEC 9.3 第 2、3 步，9.4）。

全部基于规则：
- 签约方：在“甲方、买方、Buyer、Consignee”等标签后的冒号之后，取以企业后缀结尾的名称。
- 简称：紧跟在已识别名称之后的“（以下简称“X”）”“(the "X")”等结构；通用角色词不作为简称。
- 其他企业名称：正文中带“有限公司”等明确后缀的中文名称，以及以英文企业后缀结尾的连续大写开头单词。
  英文名称切词时，汉字与中文标点同空白一样是分隔符（“收函方：Aurora Fictive Energy Co., Ltd.”整体识别）；
  名称中间可以有一组括号括起的地区名（“Northwind Fictive (Hong Kong) Limited”整体识别）。
  “公司、厂、集团”等宽泛后缀只在签约方标签后使用，正文中不据此自动替换（留给 9.5 疑似遗漏，M4）。
"""
import re
import unicodedata

from app.redaction.dictionaries import find_country, find_org_type
from app.redaction.placeholders import BUYER, OTHER, SELLER

# ---------------------------------------------------------------- 标签

_LABELS: list[tuple[str, list[str]]] = [
    (BUYER, ["买方", "需方", "采购方", "购货方", "买受人", "Buyer", "Purchaser", "Importer"]),
    (SELLER, ["卖方", "供方", "供应商", "供货方", "销售方", "出卖人", "Seller", "Supplier", "Vendor", "Exporter"]),
    ("甲方", ["甲方", "Party A"]),
    ("乙方", ["乙方", "Party B"]),
    ("丙方", ["丙方", "Party C"]),
    ("收货人", ["收货人", "Consignee"]),
    ("通知方", ["通知方", "通知人", "Notify Party"]),
    ("银行", ["开户行", "开户银行", "收款银行", "Beneficiary Bank", "Bank Name", "Bank"]),
    ("受益人", ["受益人", "Beneficiary"]),
]
_LABEL_CANON = {word.casefold(): canon for canon, words in _LABELS for word in words}
_LABEL_WORDS = sorted(_LABEL_CANON, key=len, reverse=True)
_LABEL_RE = re.compile(
    r"(?:^|(?<=[\s,，;；。、(（]))(?i:("
    + "|".join(re.escape(w) for w in _LABEL_WORDS)
    + r"))\s*(?:名称|全称|(?i:company\s+name|name))?\s*(?:[（(][^）)\n]{1,20}[）)])?\s*[:：]",
    re.MULTILINE,
)

# 通用角色词：作为简称出现时不替换（9.3 第 3 步）
GENERIC_WORDS = frozenset(w.casefold() for w in (
    *_LABEL_CANON, "双方", "各方", "一方", "本合同", "合同", "本协议", "协议", "公司", "本公司", "货物",
    "Party", "Parties", "Company", "Contract", "Agreement", "Goods", "Products", "Seller", "Buyer",
))

# ---------------------------------------------------------------- 企业后缀

_CN_SUFFIX = "股份有限公司|有限责任公司|集团有限公司|有限公司|集团公司|集团|公司|研究院|研究所|厂"
_CN_STRONG_SUFFIX = re.compile("股份有限公司|有限责任公司|集团有限公司|有限公司")
_EN_SUFFIX = (
    r"Co\.\s?,\s?Ltd\.?|Co\.\s?Limited|Company\s+Limited|Pte\.?\s?Ltd\.?|Pvt\.?\s?Ltd\.?|Sdn\.?\s?Bhd\.?|"
    r"L\.L\.C\.?|FZ-LLC|LLC|LLP|PLC|Limited|Ltd\.?|Inc\.?|Corp\.?|Corporation|GmbH|S\.A\.|S\.r\.l\.|"
    r"B\.V\.|N\.V\.|FZCO|FZE|PJSC|JSC|OOO"
)
_EN_SUFFIX_RE = re.compile(rf"(?<![A-Za-z])(?:(?i:{_EN_SUFFIX})|AG)(?![A-Za-z])")
_SUFFIX_RE = re.compile(rf"(?:{_CN_SUFFIX})|(?<![A-Za-z])(?:(?i:{_EN_SUFFIX})|AG)(?![A-Za-z])")
_NAME_FORBIDDEN = set("：:；;。，、“”\"「」『』\n\t")
_CN_CHAR = re.compile(r"[一-鿿]")
_EN_WORD_OK = re.compile(r"^(?:[A-Z0-9][A-Za-z0-9&'.\-]*|&|of|and|de|du|la|for)$")
# 英文名称切词：空白、汉字、中文标点（含全角形式）和下列半角标点都是分隔符，使“收函方：Aurora Energy Co., Ltd.”中的
# 冒号前不会把“Aurora”并进无效单词；撇号、句点、逗号、连字符、&留在单词内
# 名称中间可以有一组括号括起的 1–3 个大写开头的单词（“Northwind (Hong Kong) Limited”“Example (HK) Co., Ltd.”），整体算一个单词
_EN_PAREN_GROUP = r"\([A-Z][A-Za-z.&\-]*(?: [A-Z][A-Za-z.&\-]*){0,2}\)"
_EN_TOKEN_RE = re.compile(rf"{_EN_PAREN_GROUP}|[^\s一-鿿　-〿＀-￯：:；;\"“”‘「」『』()（）]+")
_EN_PAREN_OK = re.compile(rf"^{_EN_PAREN_GROUP}$")
_EN_CONNECTORS = frozenset({"&", "of", "and", "de", "du", "la", "for"})
_EN_LEADING_NOISE = frozenset(w.casefold() for w in (
    "The", "To", "From", "By", "With", "Messrs", "Messrs.", "Dear", "Attn", "Seller", "Buyer", "Supplier",
    "Purchaser", "Vendor", "Consignee", "Beneficiary", "Party", "Bank", "Name", "Company",
))
# 中文名称前常见的虚词：在正文中识别企业名称时去掉（“与XX有限公司签订”）
_CN_LEADING_NOISE = set("与和及由向对将经在为给被把自从同跟即系是")
# 正文企业名称的起点边界词（Q-56，设计 M4.md 2.2）：向前取汉字时遇到即停止，名称从边界词之后开始；多字的优先
_CN_BOUNDARY_WORDS = (
    "经由", "委托", "通过", "根据", "按照", "依据", "代表", "联系",
    "由", "向", "与", "和", "及", "同", "跟", "经", "致", "自", "从", "为", "对", "将", "把", "被", "给", "在", "即", "系", "是",
)
# 企业名称中常见的、含边界字的词：这些词内部的字不当作边界（否则“新兴经济贸易有限公司”会被截成“济贸易有限公司”而漏掉前半）
_CN_BOUNDARY_PROTECTED = (
    "经贸", "经济", "经营", "经销", "经纪", "经理", "自由", "自贸", "自动", "自然", "自强", "同方", "同心", "同创", "同兴",
    "同济", "和平", "和谐", "和顺", "和合", "和兴", "为民", "从化", "在线", "对外", "给水", "即时",
)

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
    # 填空括号“乙方：【××有限公司】”：开括号不属于名称（Q-134 ①；与 detect.FILL_OPEN 相同）
    if window[lead:lead + 1] in ("【", "［", "[", "〖"):
        lead += 1
        lead += len(window[lead:]) - len(window[lead:].lstrip(" \t"))
    for m in _SUFFIX_RE.finditer(window, lead):
        if not window[lead:m.start()].strip(" ,"):
            continue
        if not _valid_prefix(window[lead:m.start()]):
            return None
        end = m.end()
        while (more := _SUFFIX_RE.match(window, end)) and more.end() > end:  # “集团有限公司”等连续后缀
            end = more.end()
        return pos + lead, pos + end
    return None


def find_labeled_parties(text: str) -> list[tuple[str, int, int]]:
    """节点原文中“标签：名称”形式的签约方：[(规范化标签, start, end)]。"""
    out = []
    for m in _LABEL_RE.finditer(text):
        span = extract_org_name(text, m.end())
        if span:
            out.append((_LABEL_CANON[m.group(1).casefold()], *span))
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


def _name_start_after_boundary(text: str, start: int, suffix_start: int, min_han: int = 2) -> int:
    """text[start:suffix_start] 是企业后缀前向前取到的汉字（含括号）。返回最后一个边界词之后的位置（Q-56）。

    括号内的字和受保护词内部的字不算边界；边界词之后不足 min_han 个汉字时返回 start（按 M2 的做法整段处理，宁多勿漏）。
    疑似遗漏识别（suspects.py）传 min_han=0：有边界词就从边界词之后算起，由调用方判断名称是否够长。
    """
    run = text[start:suffix_start]
    blocked = [False] * len(run)
    for word in _CN_BOUNDARY_PROTECTED:
        i = run.find(word)
        while i >= 0:
            for k in range(i, i + len(word)):
                blocked[k] = True
            i = run.find(word, i + 1)
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
    out = []
    last_end = 0
    for m in _CN_STRONG_SUFFIX.finditer(text):
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
            out.append((start, m.end()))
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
            out.append((offset + kept[0].start(), m.end()))
    return out


def detect_country(lines: list[str], names: list[str]) -> str | None:
    """在主体所在行及其后的信息块中查找国家（9.4）；先去掉名称本身，避免名称中的地名干扰。"""
    for line in lines:
        for name in sorted(names, key=len, reverse=True):
            line = name_pattern(name).sub(" ", line)
        country = find_country(line)
        if country:
            return country
    return None


def default_country(name: str) -> str | None:
    """块中找不到国家时：以中文企业后缀结尾的中文名称视为中国企业。"""
    return "中国" if is_chinese_name(name) else None


def org_type(name: str) -> str:
    return find_org_type(name)


__all__ = [
    "GENERIC_WORDS", "OTHER", "default_country", "detect_country", "extract_org_name", "find_aliases",
    "find_labeled_parties", "find_other_org_names", "is_chinese_name", "name_pattern", "normalize", "org_type",
]
