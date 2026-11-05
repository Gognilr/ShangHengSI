"""出口复检（SPEC 9.8，open_questions Q-19，设计 docs/design/M2.md 第六节）。

模型网关在每次发送前，对最终请求的全部文本调用 recheck()：
1. 本任务对照表中的全部真实值、启用中的已记住名称和自定义敏感词，任何一个出现即拦截；
2. 用 9.3 第 4、5 步的正则重新扫描，命中未替换的电话、邮箱、账号、证件号、带币种的金额即拦截。

拦截原因只有类别和位置，不含真实值；本模块不写日志。

数字类真实值（Q-19）：前后都不是数字时才算出现；6 位以下的短数字还要紧挨着币种或“元”等单位才算，
避免“数量 100 MT”被金额 100 误拦。长数字（账号、电话等）去掉空格和短横线后再比较一次，防止换了写法漏过。
同一数值换了千分位写法（“USD 400000”对“400,000.00”）且带币种时，按数值相等拦截。
"""
import hashlib
import re
import uuid
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RedactionTerm
from app.redaction import detect
from app.redaction.normalize import normalize_with_map, normalize_value
from app.redaction.parties import name_pattern, single_english_word
from app.security import crypto

_RESCAN_CATEGORIES = {"PHONE", "EMAIL", "ACCOUNT", "IDNO", "SWIFT", "URL", "IM", "POSTCODE", "REFNO"}
_NUMERIC_VALUE = re.compile(r"^[+\d][\d\s,.\-]*$")
_AMOUNT_VALUE = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^\d+(?:\.\d+)?$")
# 数字前不能是下划线：代号“[金额_10]”的序号不是数字类真实值
_NUMBER_IN_TEXT = re.compile(r"(?<![\d._])(?<!\d,)(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d])")
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
_BIG_AMOUNT = Decimal(10000)  # 不小于此数值的金额真实值，不论是否带币种都按数值比对（Q-134 ③）


@dataclass(frozen=True)
class BlockReason:
    category: str  # MAP / TERM / PHONE / EMAIL / ACCOUNT / IDNO / AMOUNT
    position: int  # 在被检查文本中的字符位置，不记真实值
    end: int = field(default=-1, compare=False)  # 命中结束位置（约数），只供本机定位预览（V31-U5F-02）


@dataclass
class RecheckContext:
    real_values: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)  # 本任务对照表中的代号原文（Q-134 ②）
    kept: list[str] = field(default_factory=list)  # 本任务用户明确保留 / 撤销的原文，只豁免重扫（V31Q-51）

    def __repr__(self) -> str:  # 防止真实值被意外打印进日志
        return f"RecheckContext(real_values={len(self.real_values)}, terms={len(self.terms)})"


def _mask_placeholders(text: str, placeholders: Iterable[str], *, mask: str = '\x00') -> str:
    """把代号原文换成等长的不可匹配字符，位置不变。只用于对照表真实值和词条的比对：
    代号“[电话]”中的“电话”二字不是外发的真实值（B-04）。格式化信息与金额的重扫仍在原文上做。"""
    pattern = _codes_pattern(tuple(sorted({p for p in placeholders if p}, key=lambda p: (-len(p), p))))
    return pattern.sub(lambda m: mask * len(m.group()), text) if pattern else text


@lru_cache(maxsize=32)
def _codes_pattern(codes: tuple[str, ...]) -> re.Pattern | None:
    """一次遮盖全部代号（长的优先）。代号带方括号、互不重叠，与逐个替换结果相同；
    长文档逐段复检时不再按代号数逐个扫全文（V31-U5F-03）。"""
    return re.compile('|'.join(map(re.escape, codes))) if codes else None


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
        # 数值 ≥ 10000 的金额：换了千分位、小数位写法也拦截，不要求带币种（Q-134 ③，宁可误拦）。
        # 小于 10000 的（含带小数的单价）仍须带币种：条款编号“1.8”“2.3”与单价同值时会拦死且用户无法处理（R1-03 实测）
        any_form = target is not None and target >= _BIG_AMOUNT
        for start, end, number in _numbers(text):
            if number == target and (any_form or _has_currency(text, start, end)):
                out.append(start)
    return out


@lru_cache(maxsize=8)
def _numbers(text: str) -> tuple[tuple[int, int, Decimal | None], ...]:
    """文本中全部数字及其数值。每个金额真实值都要与全文数字比对，只解析一次（V31-U5F-01）。
    只在进程内按文本缓存最近几份，不写日志、不持久化。"""
    return tuple((m.start(1), m.end(1), _decimal(m.group(1))) for m in _NUMBER_IN_TEXT.finditer(text))


def _text_pattern(value: str) -> re.Pattern:
    # 全大写的短英文简称、单个英文单词（V2Q-173）区分大小写，与脱敏时一致；其余英文不区分大小写、忽略多余空格
    short_caps = value.isascii() and value.isupper() and len(value) <= 6
    return name_pattern(value, case_sensitive=short_caps or single_english_word(value))


def _value_positions(text: str, value: str) -> list[int]:
    value = value.strip()
    if len(value) < 2:
        return []
    if _NUMERIC_VALUE.match(value):
        return _numeric_positions(text, value)
    return [m.start() for m in _text_pattern(value).finditer(text)]


_INVISIBLE_TAG = re.compile('[\U000E0000-\U000E007F]')


def recheck(text: str, *, real_values: Iterable[str], terms: Iterable[str], kept: Iterable[str] = (),
            placeholders: Iterable[str] = ()) -> list[BlockReason]:
    """返回全部拦截原因（按位置排序、去重）；空列表表示可以发送。

    placeholders 为本任务对照表中的代号原文：比对真实值、词条前先遮盖（只遮盖这些确切的代号，默认不遮盖）。"""
    codes = list(placeholders)
    kept_keys = {k for k in map(_kept_key, kept) if k}
    normalized = _normalized(text, False)
    preserving = _normalized(text, True)
    reasons = _value_reasons(text, normalized, preserving, codes, [
        (c, v) for c, vs in (('MAP', real_values), ('TERM', terms)) for v in dict.fromkeys(vs)])
    reasons.update(_allowed(_rescan_hits(text, preserving, _contextual(text, preserving), codes), kept_keys))
    return sorted(reasons, key=lambda r: (r.position, r.category))


def _value_reasons(text, normalized, preserving, codes, values, normalized_codes=None) -> set[BlockReason]:
    """不可见字符，以及对照表真实值 / 词条（按给定顺序，先命中的决定 end）的命中。"""
    reasons = {BlockReason('INVISIBLE', m.start(), m.end()) for m in _INVISIBLE_TAG.finditer(text)}
    if values:
        lower, upper = normalized_codes or ([normalize_value(p) for p in codes],
                                            [normalize_value(p, preserve_case=True) for p in codes])
        masked = _mask_placeholders(normalized.text, lower)
        preserving_masked = _mask_placeholders(preserving.text, upper)
        original_masked = _mask_placeholders(text, codes)
        for category, value in values:
            reasons.update(BlockReason(category, p, p + len(value)) for p in
                           _match_positions(value, original_masked, normalized, masked, preserving, preserving_masked))
    return reasons


def _contextual(text: str, preserving) -> str:
    # ASCII 称谓 / 标签不能被 I→l 变成新的单词；完整归一化扫描仍保留，另补语境稳定的扫描。
    return ''.join(_context_char(text[preserving.index_map[i]], c) for i, c in enumerate(preserving.text))


def _rescan_hits(text: str, preserving, contextual: str, codes: list[str]) -> tuple[tuple, ...]:
    """原文、归一化文本分别重扫格式化信息、金额、人名与地址，按生成顺序返回
    (类别, 原文起点, 原文终点, 保留键)；保留键为 None 的（金额）不受保留项豁免。"""
    hits = []
    for scanned, indices, view in ((text, range(len(text)), text), (preserving.text, preserving.index_map, contextual)):
        for candidate in dict.fromkeys((scanned, view)):
            for hit in detect.find_formatted(candidate):
                if hit.category in _RESCAN_CATEGORIES:
                    hits.append((hit.category, indices[hit.start], indices[hit.end - 1] + 1,
                                 _kept_key(candidate[hit.start:hit.end])))
        # 金额扫语境视图：中文数字保持原样，与脱敏识别看到的一致（“一块”“一元化”不会被归一成“1块”“1元化”，V31-U5F-02）。
        for hit in detect.find_amounts(view):
            hits.append(('AMOUNT', indices[hit.start], indices[hit.end - 1] + 1, None))
        # 人名语境中的“第二张工作表”不能因“二→2”凭空产生“张工”；姓名中的“一、三”等仍是汉字。
        people_text = _mask_code_syntax(_mask_placeholders(view, codes, mask='\n'))
        for finder in (detect.find_persons, detect.find_addresses):
            for hit in finder(people_text):
                surface = people_text[hit.start:hit.end]
                if finder is detect.find_addresses and _place_only(surface):
                    continue  # 与脱敏相同：只有国家、城市或港口名称的地址保留（V31-U5F-02）
                hits.append((hit.category, indices[hit.start], indices[hit.end - 1] + 1, _kept_key(surface)))
    return tuple(hits)


def _allowed(hits, kept_keys) -> set[BlockReason]:
    """用户在本任务明确保留的整段原文不由重扫拦截；对照表与词条比对不豁免。同一位置同类先出现的决定 end。"""
    reasons: set[BlockReason] = set()
    for category, start, end, key in hits:
        if key is None or key not in kept_keys:
            reasons.add(BlockReason(category, start, end))
    return reasons


_SEP, _SEP2, _SEP3 = '\x00', '\x01', '\x02'
_SEGMENT_CACHE: 'OrderedDict[bytes | tuple, tuple]' = OrderedDict()
_SEGMENT_CACHE_MAX = 50000


@lru_cache(maxsize=32768)
def _segment_view(text: str):
    normalized, preserving = _normalized(text, False), _normalized(text, True)
    return normalized, preserving, _skeleton(text), _contextual(text, preserving)


def _digest(value: str) -> bytes:
    return hashlib.blake2b(value.encode(), digest_size=16).digest()


def _numeric_like(value: str) -> bool:
    return any(_NUMERIC_VALUE.match(v) for v in (value, normalize_value(value), normalize_value(value, preserve_case=True)))


class SegmentChecker:
    """脱敏自检闭环的逐段复检（V31-U5F-02、03）：每段结论与 recheck(段文字, …) 相同。

    一轮之内真实值、词条、代号、保留项只预处理一次。每段只比对可能命中本段的值：非数字的值骨架不在本段骨架中时
    任何通路都不会命中（同 present_values），数字类值一律比对。比对与重扫命中按“段文字 + 段内出现的代号 +
    可能命中的值”的摘要缓存在进程内，只存类别、位置与保留键摘要，不存原文；决定一个不确定项后整份重算时，
    没变的段直接复用（V31-U5F-03 续）。网关发送前的复检不走这里。"""

    def __init__(self, *, real_values: Iterable[str], terms: Iterable[str], placeholders: Iterable[str],
                 kept: Iterable[str]):
        self.codes = list(placeholders)
        self.lower = [normalize_value(p) for p in self.codes]
        self.upper = [normalize_value(p, preserve_case=True) for p in self.codes]
        self.kept = {_digest(k) for k in map(_kept_key, kept) if k}
        self.values = [(c, v, _skeleton(v.strip()), _numeric_like(v.strip()))
                       for c, vs in (('MAP', real_values), ('TERM', terms)) for v in dict.fromkeys(vs)]
        # 整轮指纹：同一组代号与值下段文字相同即可直接复用，不再逐段计算段内代号与可能命中的值
        self.fingerprint = hashlib.sha256(_SEP.join([_SEP2.join(self.codes), _SEP2.join(
            c + _SEP3 + v for c, v, _s, _n in self.values)]).encode()).digest()

    def __call__(self, text: str) -> list[BlockReason]:
        whole = (self.fingerprint, text)
        hit = _SEGMENT_CACHE.get(whole)
        if hit is None:
            hit = self._segment(text)
            _SEGMENT_CACHE[whole] = hit
            if len(_SEGMENT_CACHE) > _SEGMENT_CACHE_MAX:
                _SEGMENT_CACHE.popitem(last=False)
        reasons = set(hit[0])
        reasons.update(_allowed(hit[1], self.kept))
        return sorted(reasons, key=lambda r: (r.position, r.category))

    def _segment(self, text: str) -> tuple:
        normalized, preserving, skeleton, contextual = _segment_view(text)
        present = [i for i, (c, lo, up) in enumerate(zip(self.codes, self.lower, self.upper))
                   if c in text or c in contextual or lo in normalized.text or up in preserving.text]
        values = [(c, v) for c, v, skel, numeric in self.values if numeric or not skel or skel in skeleton]
        key = hashlib.sha256('\x00'.join([text, '\x01'.join(self.codes[i] for i in present),
                                           '\x01'.join(f'{c}\x02{v}' for c, v in values)]).encode()).digest()
        hit = _SEGMENT_CACHE.get(key)
        if hit is None:
            codes = [self.codes[i] for i in present]
            reasons = _value_reasons(text, normalized, preserving, codes, values,
                                     ([self.lower[i] for i in present], [self.upper[i] for i in present]))
            hits = tuple((c, s, e, None if k is None else _digest(k))
                         for c, s, e, k in _rescan_hits(text, preserving, contextual, codes))
            hit = (tuple(sorted(reasons, key=lambda r: (r.position, r.category))), hits)
            _SEGMENT_CACHE[key] = hit
            if len(_SEGMENT_CACHE) > _SEGMENT_CACHE_MAX:
                _SEGMENT_CACHE.popitem(last=False)
        else:
            _SEGMENT_CACHE.move_to_end(key)
        return hit


# 预筛用的“骨架”：归一化后去掉空白与 - . ,，再做 OCR 折叠。三条比对通路（原文、归一化的名称正则，去分隔符的紧凑比对）
# 命中时，值的骨架一定是文本骨架的子串，所以骨架不在全文中的值在任何一段都不会命中。
_SKELETON_DROP = re.compile(r"[\s\-.,]")
_OCR_FOLD = str.maketrans({'o': '0', 'l': '1', 'i': '1', '|': '1', 's': '5', 'b': '8'})


@lru_cache(maxsize=32768)
def _skeleton(text: str) -> str:
    return _SKELETON_DROP.sub('', _normalized(text, False).text).translate(_OCR_FOLD)


@lru_cache(maxsize=32768)
def _normalized(text: str, preserve_case: bool):
    """复检对象与脱敏等价写法传播所用段文字的归一化结果。逐段、逐轮重算时同一段文字反复出现，按文本缓存；
    只在进程内、有上限，不写日志、不持久化。返回值只读。"""
    return normalize_with_map(text, preserve_case=preserve_case)


def present_values(values: Iterable[str], texts: Iterable[str]) -> list[str]:
    """逐段复检前的全文预筛（V31-U5F-03）：去掉在全部文本中都不可能命中的真实值 / 词条，结果只少筛不多筛。
    数字类值有千分位、小数位与按数值比对的写法，一律保留；网关发送前的复检仍用完整列表。"""
    doc = '\x01'.join(_skeleton(t) for t in texts)
    out = []
    for value in dict.fromkeys(values):
        stripped = value.strip()
        skeleton = _skeleton(stripped)
        if _NUMERIC_VALUE.match(stripped) or not skeleton or skeleton in doc:
            out.append(value)
    return out


def _place_only(surface: str) -> bool:
    """地址命中去掉已遮盖的代号后，剩下的每段都只是国家、城市或港口名称（与 engine 识别地址时的判断相同）。"""
    from app.redaction.engine import _ADDRESS_EDGE, _WORD_CHAR, _is_place
    pieces = [piece.strip(_ADDRESS_EDGE) for piece in surface.splitlines()]
    return all(not _WORD_CHAR.search(piece) or _is_place(piece) for piece in pieces)


def _match_positions(value, original_masked, normalized, masked, preserving, preserving_masked) -> set[int]:
    """对照表真实值 / 词条在原文中的命中位置（原文、归一化、紧凑三条通路）。"""
    # Unicode 的 ASCII I→l 等映射会使原有英文大小写比较失配；保留原文通路，只增不减。
    out = set(_value_positions(original_masked, value))
    sensitive = single_english_word(value) or value.isascii() and value.isupper() and len(value) <= 6
    view, compared = (preserving, preserving_masked) if sensitive else (normalized, masked)
    out.update(view.index_map[p] for p in _value_positions(compared, normalize_value(value, preserve_case=sensitive)))
    out.update(preserving.index_map[p]
               for p in _compact_positions(preserving_masked, normalize_value(value, preserve_case=True)))
    return out


def matching_terms(text: str, terms: Iterable[str]) -> list[str]:
    """出口复检的词条比对规则，只返回命中的词条（管理员提醒用；不做格式化信息与人名重扫）。"""
    normalized = normalize_with_map(text)
    preserving = normalize_with_map(text, preserve_case=True)
    return [value for value in dict.fromkeys(terms)
            if _match_positions(value, text, normalized, normalized.text, preserving, preserving.text)]


def _kept_key(value: str) -> str:
    return re.sub(r'[\s\-]', '', normalize_value(value, preserve_case=True)).upper()


def _context_char(original: str, normalized: str) -> str:
    ch = unicodedata.normalize('NFKC', original)
    return ch if len(ch) == 1 and (ch in '〇零一二三四五六七八九' or ch.isascii() and ch.isalpha()) else normalized


def _mask_code_syntax(text: str) -> str:
    """只遮盖合法代号，普通方括号内的真实值仍接受重扫。"""
    from app.redaction.restore import _identity
    from app.redaction.placeholders import NEUTRAL
    def mask(match):
        raw = match.group()
        identity = _identity(raw)
        if identity and identity[0] == 'shared' and identity[1].startswith('地址:'):
            country = raw[1:-1].split('：' if '：' in raw else ':', 1)[-1]
            if detect.find_country(country) != country and country != '国家未知':
                identity = None
        return '\n' * len(raw) if NEUTRAL.fullmatch(raw) or identity else raw
    return re.sub(r'\[\[(?:OUR|[A-Z]+_\d+)\]\]|[\[［【][^\[\]［］【】\r\n]+[\]］】]', mask, text)


def _compact_positions(text: str, value: str) -> list[int]:
    target = re.sub(r'[\s-]', '', value).upper()
    if (len(target) < 3 or not target.isascii() or not target.isalnum()
            or not (any(c.isdigit() for c in target) or detect.validators.bic(target))):
        return []
    # 短纯数字仍按原币种 / 数字边界规则处理，不能把代号序号或期限拦下。
    if target.isdigit() and len(target) < _LONG_DIGITS:
        return []
    out = []
    ocr = str.maketrans({'O': '0', 'L': '1', 'I': '1', '|': '1', 'S': '5', 'B': '8'})
    for match in re.finditer(r'[A-Za-z0-9|]+(?:[\s-]+[A-Za-z0-9|]+)*', text):
        positions = [i for i in range(match.start(), match.end()) if not text[i].isspace() and text[i] != '-']
        compact = ''.join(text[i] for i in positions).upper()
        variants = [compact]
        if target.isdigit() and sum(c.isdigit() for c in compact) > len(compact) / 2:
            variants.append(compact.translate(ocr))
        for variant in variants:
            start = variant.find(target)
            if start >= 0:
                out.append(positions[start])
    return out


def recheck_context(db: Session, task_id: uuid.UUID | None, extra_task_ids: Iterable[uuid.UUID] = ()) -> RecheckContext:
    """汇集对照表真实值（有任务时）与启用中的已记住名称、自定义敏感词。"""
    from app.redaction.engine import load_kept_values, load_placeholders, load_real_values  # 避免循环导入

    values = load_real_values(db, task_id) if task_id is not None else []
    codes = load_placeholders(db, task_id) if task_id is not None else []
    for extra_id in dict.fromkeys(extra_task_ids):
        if extra_id != task_id:
            values.extend(load_real_values(db, extra_id))
            codes.extend(load_placeholders(db, extra_id))
    rows = db.scalars(select(RedactionTerm.term_enc).where(RedactionTerm.enabled.is_(True)))
    return RecheckContext(real_values=values, terms=[crypto.decrypt_text(blob) for blob in rows], placeholders=codes,
                          kept=load_kept_values(db, task_id) if task_id is not None else [])
