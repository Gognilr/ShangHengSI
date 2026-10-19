"""疑似遗漏（SPEC 9.5，设计 docs/design/M4.md 2.3，Q-69、Q-74）。

只做识别，纯函数，不碰数据库：在单个节点的原文上找“看起来像敏感信息、但没有被替换”的文字，
返回位置和原因；是否替换由用户在第二步决定（SPEC 6.2 第 4 项）。

先把已替换的区间换成同样长度的 \\x00，再在其上识别，候选因此不会跨越已替换的文字。
"""
import re
from dataclasses import dataclass
from datetime import date

from app.redaction import parties as P
from app.redaction.detect import _QTY_UNIT_RE
from app.redaction.dictionaries import _country_index, common_terms

ORG_SUFFIX = "org_suffix"
BEFORE_COMPANY = "before_company"
CAPITALIZED = "capitalized"
LONG_DIGITS = "long_digits"
_PRIORITY = {ORG_SUFFIX: 0, BEFORE_COMPANY: 1, CAPITALIZED: 2, LONG_DIGITS: 3}
_MASK = "\x00"


@dataclass(frozen=True)
class Suspect:
    start: int
    end: int
    reason: str  # org_suffix / capitalized / long_digits / before_company（18.11）


# ---------------------------------------------------------------- 中文：企业后缀与“公司、厂”之前的名称

_HAN = re.compile(r"[一-鿿]")
# 9.3 第 2 步企业后缀表中的中文后缀，不含单独的“公司、厂”（这两个由 before_company 负责）
_CN_SUFFIX = re.compile("股份有限公司|有限责任公司|集团有限公司|有限公司|集团|研究院|研究所")
_CN_SUFFIX_MORE = re.compile("股份有限公司|有限责任公司|集团有限公司|有限公司|集团|研究院|研究所|公司")
_COMPANY_WORD = re.compile("公司|厂")
# 泛称：整段等于其中之一时不列为疑似遗漏（“本公司”“物流公司”）
_GENERIC_BEFORE_COMPANY = frozenset({
    "本", "贵", "我", "我方", "该", "对方", "双方", "各", "其", "任何", "子", "分", "母", "总", "关联", "船", "保险", "物流",
    "货运", "运输", "快递", "航空",
})
_MAX_NAME_HAN = 10
_SUFFIX_TAIL = ("有限", "责任", "集团")
_NAME_STOP_CHARS = "的并且或"  # 企业名称里几乎不会出现，说明前面是普通句子成分
# “出厂价”“厂家”“厂房”这类词里的“厂”不是企业名称的结尾（Q-74 之外的补充，记入 open_questions）
_FACTORY_NOT_AFTER = "家商房内价前后日期验检区门址长"


_PAREN_GROUP_END = re.compile(r"[（(][一-鿿A-Za-z]{1,6}[）)]\Z")  # 名称中间的地区括号：（深圳）（HK）


def _walk_back(text: str, pos: int) -> int:
    """从 pos 向前取汉字（可含一组短的地区括号），最多 10 个汉字；返回起点。"""
    start, han = pos, 0
    while start > 0:
        ch = text[start - 1]
        if _HAN.match(ch):
            if han >= _MAX_NAME_HAN or ch in _NAME_STOP_CHARS:
                break
            han += 1
            start -= 1
        elif ch in "）)":
            m = _PAREN_GROUP_END.search(text[max(0, start - 8):start])
            if m is None:
                break
            han += sum(1 for c in m.group() if _HAN.match(c))
            start -= len(m.group())
        else:
            break
    return start


def _cn_name_span(text: str, pos: int) -> tuple[int, int] | None:
    """pos 为企业后缀的起点；返回名称部分 (start, pos)，已按 2.2 的边界词截取；不足 2 个汉字或为泛称时 None。"""
    start = _walk_back(text, pos)
    start = P._name_start_after_boundary(text, start, pos, min_han=0)
    run = text[start:pos]
    han = "".join(c for c in run if _HAN.match(c))
    if len(han) < 2 or han in _GENERIC_BEFORE_COMPANY:
        return None
    return start, pos


def _chinese_suffix_candidates(text: str) -> list[Suspect]:
    out: list[Suspect] = []
    for m in _CN_SUFFIX.finditer(text):
        span = _cn_name_span(text, m.start())
        if span is None:
            continue
        end = m.end()
        while (more := _CN_SUFFIX_MORE.match(text, end)) and more.end() > end:  # “集团有限公司”“集团公司”
            end = more.end()
        out.append(Suspect(span[0], end, ORG_SUFFIX))
    for m in _COMPANY_WORD.finditer(text):
        if text[:m.start()].endswith(_SUFFIX_TAIL):  # “有限公司”“集团公司”等由 org_suffix 负责
            continue
        if m.group() == "厂":
            before = text[m.start() - 1:m.start()]
            after = text[m.end():m.end() + 1]
            if before == "出" or (after and after in _FACTORY_NOT_AFTER):
                continue
        span = _cn_name_span(text, m.start())
        if span is not None:
            out.append(Suspect(span[0], m.end(), BEFORE_COMPANY))
    return out


# ---------------------------------------------------------------- 英文：企业后缀与连续的首字母大写单词

_EN_WORD = re.compile(r"(?<![A-Za-z0-9'’\-])[A-Z][A-Za-z'’\-]*\.?(?![A-Za-z0-9])")
_SPACES = re.compile(r"[ \t]+")
# 句首常见的虚词：从连续大写单词的开头去掉（“The Seller”“This Agreement”）
_FUNCTION_WORDS = frozenset(w.casefold() for w in (
    "The", "A", "An", "This", "That", "These", "Those", "Such", "Any", "All", "Each", "Every", "Our", "Your", "Their",
    "In", "On", "At", "By", "To", "For", "If", "Upon", "Unless", "Where", "When", "Both", "Either", "Neither", "No", "Not",
    "Of", "And", "Or", "With", "From", "As", "Under", "Per", "Without", "Within", "After", "Before",
))
# 名称前面的虚词、称呼（含 parties 的噪声词）：从名称开头去掉
_LEADING_WORDS = _FUNCTION_WORDS | P._EN_LEADING_NOISE | {"contact"}
_MONTHS_WEEKDAYS = frozenset(w.casefold() for w in (
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
    "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "Mon", "Tue", "Tues", "Wed", "Thu", "Thur", "Thurs", "Fri", "Sat", "Sun",
))


def _word_core(token: str) -> str:
    return token.rstrip(".")


def _is_en_word(token: str) -> bool:
    """首字母大写的英文单词；全大写的 2–5 字母缩写（FOB、CIF、HS、USD）和单个大写字母（Annex A）不算。"""
    core = _word_core(token)
    if core.isupper() and (2 <= len(core) <= 5 or (len(core) == 1 and not token.endswith("."))):
        return False
    return True


def _capitalized_runs(text: str) -> list[list[re.Match]]:
    """文本中由空格 / 制表符相连的、连续的“首字母大写单词”段。"""
    runs: list[list[re.Match]] = []
    cur: list[re.Match] = []
    for w in _EN_WORD.finditer(text):
        ok = _is_en_word(w.group())
        if cur and ok and _SPACES.fullmatch(text[cur[-1].end():w.start()]):
            cur.append(w)
            continue
        if cur:
            runs.append(cur)
        cur = [w] if ok else []
    if cur:
        runs.append(cur)
    return runs


def _end_without_period(w: re.Match) -> int:
    """单词的结束位置；句末的句号不属于名称，“J.”这样的缩写保留。"""
    token = w.group()
    return w.end() - 1 if token.endswith(".") and len(_word_core(token)) > 1 else w.end()


@dataclass(frozen=True)
class _Excluder:
    terms: frozenset[str]  # common_terms 的小写形式
    max_words: int

    def piece_ok(self, words: list[str]) -> bool:
        s = " ".join(words).casefold()
        return s in self.terms or s in _MONTHS_WEEKDAYS

    def covered(self, words: list[str]) -> bool:
        """words 能否完全分解为：常见合同用语（单词或短语）、月份、星期、国家 / 城市 / 港口名称。"""
        n = len(words)
        ok = [False] * (n + 1)
        ok[0] = True
        for i in range(n):
            if not ok[i]:
                continue
            for j in range(i + 1, min(n, i + self.max_words) + 1):
                piece = words[i:j]
                text = " ".join(piece)
                if self.piece_ok(piece) or _is_place(text):
                    ok[j] = True
        return ok[n]


def _is_place(text: str) -> bool:
    pattern, _mapping = _country_index()
    return pattern.fullmatch(text) is not None


def _excluder() -> _Excluder:
    terms = frozenset(t.casefold() for t in common_terms())
    longest = max((t.count(" ") + 1 for t in terms), default=1)
    return _Excluder(terms, max(longest, 4))


def _capitalized_candidates(text: str) -> list[Suspect]:
    excl = _excluder()
    out: list[Suspect] = []
    for words in _capitalized_runs(text):
        while words and _word_core(words[0].group()).casefold() in _LEADING_WORDS:
            words = words[1:]
        if len(words) < 2:
            continue
        tokens = [_word_core(w.group()) for w in words]
        if excl.covered(tokens):
            continue
        out.append(Suspect(words[0].start(), _end_without_period(words[-1]), CAPITALIZED))
    return out


def _english_suffix_candidates(text: str) -> list[Suspect]:
    out: list[Suspect] = []
    for m in P._EN_SUFFIX_RE.finditer(text):
        if not m.group()[:1].isupper():  # “limited liability”“Limited warranty”不是企业后缀
            continue
        head_start = max(0, m.start() - 160)
        head = text[head_start:m.start()]
        head = head[head.rfind("\n") + 1:]
        offset = m.start() - len(head)
        words: list[re.Match] = []
        gap_end = len(head)
        for w in reversed(list(P._EN_TOKEN_RE.finditer(head))):
            word = w.group()
            ok = (P._EN_WORD_OK.match(word) or P._EN_PAREN_OK.match(word)) and _MASK not in word
            if head[w.end():gap_end].strip() or len(words) >= 4 or not ok:
                break
            words.append(w)
            gap_end = w.start()
        words.reverse()
        while words and (words[0].group() in P._EN_CONNECTORS or words[0].group().casefold() in _LEADING_WORDS
                         or words[0].group().startswith("(")):
            words.pop(0)
        if words and any(c.isupper() for c in words[0].group()):
            out.append(Suspect(offset + words[0].start(), m.end(), ORG_SUFFIX))
    return out


# ---------------------------------------------------------------- 长串数字（Q-69、Q-74）

_DIGIT_CHAIN = re.compile(r"\d(?:[ \-]?\d)*")
_DATE_SEP = re.compile(r"(?<!\d)((?:19|20)\d{2})([-/.])(\d{1,2})\2(\d{1,2})(?!\d)")
_DATE_COMPACT = re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)")
_HS_BEFORE = re.compile(r"(?<![A-Za-z])(?i:HS|H\.S\.)(?![A-Za-z])|海关编码|商品编码|税则号")
_UNIT_AFTER = re.compile(rf"[ \t]?(?i:{_QTY_UNIT_RE})(?![A-Za-z])")
_MIN_DIGITS = 8


def _valid_date(y: str, m: str, d: str) -> bool:
    try:
        date(int(y), int(m), int(d))
    except ValueError:
        return False
    return True


def _mask_dates(text: str) -> str:
    """把合法日期换成分隔符，使日期不被当成长串数字，也不与相邻数字连成一串。"""
    chars = list(text)

    def blank(m: re.Match) -> None:
        for i in range(m.start(), m.end()):
            chars[i] = _MASK

    for m in _DATE_SEP.finditer(text):
        if _valid_date(m.group(1), m.group(3), m.group(4)):
            blank(m)
    for m in _DATE_COMPACT.finditer(text):
        if _valid_date(*m.groups()):
            blank(m)
    return "".join(chars)


def _long_digit_candidates(text: str) -> list[Suspect]:
    out: list[Suspect] = []
    scan = _mask_dates(text)
    for m in _DIGIT_CHAIN.finditer(scan):
        if sum(c.isdigit() for c in m.group()) < _MIN_DIGITS:
            continue
        start, end = m.start(), m.end()
        if _HS_BEFORE.search(scan[max(0, start - 12):start]):
            continue
        if _UNIT_AFTER.match(scan, end):
            continue
        # 含千分位逗号或小数点的数字（金额由第 5 步负责）
        if (re.match(r"[,.]\d", scan[end:end + 2]) or (start >= 2 and scan[start - 1] in ",." and scan[start - 2].isdigit())):
            continue
        out.append(Suspect(start, end, LONG_DIGITS))
    return out


# ---------------------------------------------------------------- 入口


def _mask(text: str, occupied: list[tuple[int, int]]) -> str:
    chars = list(text)
    for s, e in occupied:
        for i in range(max(0, s), min(len(chars), e)):
            chars[i] = _MASK
    return "".join(chars)


def find_suspects(text: str, occupied: list[tuple[int, int]]) -> list[Suspect]:
    """节点原文中未被替换的疑似遗漏，按位置排序。

    occupied 为该节点已被替换的区间 [(start, end)]。同一位置命中多条规则时只保留一条，
    优先级 org_suffix > before_company > capitalized > long_digits；互相重叠的候选取较长的。
    """
    masked = _mask(text, occupied)
    cands: list[Suspect] = []
    cands += _chinese_suffix_candidates(masked)
    cands += _english_suffix_candidates(masked)
    cands += _capitalized_candidates(masked)
    cands += _long_digit_candidates(masked)
    cands = [c for c in cands if c.end > c.start and _MASK not in masked[c.start:c.end]]
    chosen: list[Suspect] = []
    for c in sorted(cands, key=lambda c: (-(c.end - c.start), _PRIORITY[c.reason], c.start)):
        if not any(c.start < k.end and k.start < c.end for k in chosen):
            chosen.append(c)
    return sorted(chosen, key=lambda c: c.start)
