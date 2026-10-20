"""脱敏核心（SPEC 9.1–9.10，设计 docs/design/M2.md 第六节、docs/design/M4.md 第三、四节）。

9.3 第 1–7 步（已记住的名称、自定义敏感词与用户词条；签约方；简称；格式化信息；金额；人名；地址）、
数量脱敏、疑似遗漏（9.5）、金额比例（9.6）、报价明细表汇总（9.7 后半，构建外发文本时）、已记住的名称（9.10）。

结构：compute() 是不碰数据库的纯函数（输入正文节点原文，输出代号、替换位置、对照表、脱敏后文本和疑似遗漏）；
redact_task() 等函数负责读写数据库，不提交事务，由调用方提交。

对照表（redaction_maps.map_enc）是加密的 JSON {键: 真实值}：
  - 键为代号（我方、客户_A、人员_1、编号_1、[账号_1]、[金额_1] 等能唯一还原的代号）时，值是该对象的主要写法，用于还原；
  - 键为“代号#序号”时，值是同一对象的其他写法（简称、大小写不同的写法）或共用代号类别（[电话]、[地址：国家] 等）的真实值，
    只用于出口复检，不用于还原（open_questions Q-45）。
用户词条（手动补充、疑似遗漏选“替换”）不另建表：就是本任务 detected_by="user" 的替换项，每次重算前从原文中取回（Q-70 ②）。
真实值不写日志，parties 表不存名称。
"""
import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.db.base import utcnow
from app.db.models import (
    DocNode, Party, RedactionItem, RedactionMap, RedactionSuspect, RedactionTerm, Task, TaskFile,
)
from app.redaction import detect, outbound, parties as P, quotes, ratio, suspects as S, tables
from app.redaction.dictionaries import _country_index
from app.redaction.placeholders import (
    ADDR, BUYER, FIXED, OTHER, OUR_CODE, SELLER, Allocator, address_code, code_role,
)
from app.redaction.recheck import recheck
from app.security import crypto

COUNTRY_UNKNOWN = "国家未知"
USER_CATEGORIES = ("ORG", "PERSON", "CUSTOM")  # 手动补充、疑似遗漏“替换”可选的类别（Q-70 ①）
MANUAL_MIN, MANUAL_MAX = 2, 100  # 手动补充去掉首尾空白后的字符数（Q-70 ③）
CONTEXT_CHARS = 30  # 疑似遗漏前后各显示多少个字符的原文（Q-70 ⑤）
_BLOCK_MAX_LINES = 6  # 签约方所在行之后，最多再看几行找国家（9.4）
_INFO_LINE = re.compile(
    r"^\s*(注册地址|通讯地址|办公地址|地址|国家|国别|电话|手机|传真|邮箱|电子邮件|联系人|统一社会信用代码|税号|"
    r"(?i:registered\s+address|address|add|country|tel|telephone|phone|mobile|fax|e-?mail|contact|attn))\s*[:：]"
)
_ADDRESS_LABEL = re.compile(r"(?i:.*地址|国家|国别|.*address|add|country)$")
_ADDRESS_EDGE = " \t,，;；、.。:：\r\n"
_WORD_CHAR = re.compile(r"[A-Za-z0-9一-鿿]")
_ADDRESS_REPEAT_MIN = 4  # 地址片段至少几个字符（且含字母或汉字）时才在全文中一并替换
_LETTER_OR_HAN = re.compile(r"[A-Za-z一-鿿]")


# ---------------------------------------------------------------- 输入输出


@dataclass
class NodeIn:
    loc: str
    text: str
    clause_no: str | None = None


@dataclass(frozen=True)
class Term:
    """已记住的名称（learned）或自定义敏感词（custom），已解密。"""
    text: str
    kind: str  # learned / custom
    category: str  # ORG / PERSON / CUSTOM
    is_our_party: bool = False


@dataclass(frozen=True)
class UserTerm:
    """用户在本任务中选中或确认替换的文字（原文写法）。"""
    text: str
    category: str  # ORG / PERSON / CUSTOM


@dataclass
class PartyOverride:
    """用户在第二步对签约方的修改，按名称的规范形式对应到重新识别出的主体。"""
    is_our_party: bool
    country: str | None
    org_type: str | None
    confirmed: bool = True


@dataclass
class PartyChange:
    """PUT /parties 的一项修改（M2-13 使用）。字段为 None 表示不改。"""
    party_id: uuid.UUID
    is_our_party: bool | None = None
    country: str | None = None
    org_type: str | None = None


@dataclass
class PartyOut:
    key: str
    code: str
    role_label: str
    country: str
    org_type: str
    is_our_party: bool
    confirmed: bool


@dataclass
class ItemOut:
    loc: str
    char_start: int
    char_end: int
    category: str
    placeholder: str
    detected_by: str


@dataclass
class SuspectOut:
    loc: str
    char_start: int
    char_end: int
    reason: str


@dataclass
class RedactionResult:
    parties: list[PartyOut]
    items: list[ItemOut]
    mapping: dict[str, str]
    redacted: dict[str, str]  # loc → 脱敏后文本（含金额比例）
    suspects: list[SuspectOut] = field(default_factory=list)
    matched_terms: set[str] = field(default_factory=set)  # 命中的已记住名称、自定义敏感词（规范形式），用于命中计数


class RedactionInputError(ValueError):
    """手动补充、疑似遗漏决定的输入错误。message 为中文提示，不含原文。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 识别


@dataclass
class _Entity:
    key: str
    name: str
    category: str  # ORG / PERSON / CUSTOM
    role_label: str = OTHER
    from_term: bool = False
    from_user: bool = False
    term_our: bool = False
    surfaces: list[str] = field(default_factory=list)  # 全称的写法
    aliases: list[str] = field(default_factory=list)
    # 简称的来源：第 3 步识别的简称为 ("alias", None)；并入的已记住名称、用户词条为 ("term", 词条规范形式) / ("user", None)
    alias_src: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    block_node: int | None = None
    country: str = COUNTRY_UNKNOWN
    org_type: str = "类型未知"
    is_our: bool = False
    confirmed: bool = False


@dataclass
class _Span:
    node: int
    start: int
    end: int
    category: str
    key: str
    detected_by: str
    currency: str | None = None  # 金额的币种和数值（金额比例用）
    value: Decimal | None = None
    term: str | None = None  # 由已记住名称、自定义敏感词命中时，为该词条的规范形式（命中计数用）


def _overlaps(occupied: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < e and s < end for s, e in occupied)


def _accept(cands: list[_Span], occupied: list[tuple[int, int]], out: list[_Span], *,
            longest_first: bool = False) -> None:
    """与已替换的文字重叠的不再采用（9.3：已替换的文字不再参与后续识别）。

    默认按位置从左到右、同一位置长的优先；longest_first=True 时整体长的优先（第 1–3 步，不留半个名称）。
    """
    key = (lambda c: (-(c.end - c.start), c.start)) if longest_first else (lambda c: (c.start, -(c.end - c.start)))
    for c in sorted(cands, key=key):
        if c.end > c.start and not _overlaps(occupied, c.start, c.end):
            occupied.append((c.start, c.end))
            out.append(c)


def _alias_pattern(alias: str) -> re.Pattern:
    # 全大写的短英文简称区分大小写，避免误伤普通单词
    short_caps = alias.isascii() and alias.isupper() and len(alias) <= 6
    return P.name_pattern(alias, case_sensitive=short_caps)


def _user_pattern(text: str) -> re.Pattern:
    """用户词条的匹配正则：同 parties.name_pattern；但以数字开头或结尾时，那一侧只要求不紧挨数字
    （OCR 文字中账号常与字母连写，如“A/C6222…”，用户选中的数字串也要能替换）。"""
    pat = P.name_pattern(text)
    body = pat.pattern
    if text.strip()[:1].isdigit() and body.startswith(r"(?<![A-Za-z0-9])"):
        body = r"(?<!\d)" + body[len(r"(?<![A-Za-z0-9])"):]
    if text.strip()[-1:].isdigit() and body.endswith(r"(?![A-Za-z0-9])"):
        body = body[:-len(r"(?![A-Za-z0-9])")] + r"(?!\d)"
    return re.compile(body, pat.flags)


def _is_place(text: str) -> bool:
    """整段只是国家、城市或港口名称（countries.yaml）。"""
    pattern, _mapping = _country_index()
    return pattern.fullmatch(text.strip()) is not None


def _free_segments(occupied: list[tuple[int, int]], start: int, end: int) -> list[tuple[int, int]]:
    """[start, end) 去掉已替换区间后剩下的各段。"""
    segs = [(start, end)]
    for s, e in sorted(occupied):
        nxt = []
        for a, b in segs:
            if e <= a or b <= s:
                nxt.append((a, b))
                continue
            if a < s:
                nxt.append((a, s))
            if e < b:
                nxt.append((e, b))
        segs = nxt
    return segs


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in _ADDRESS_EDGE:
        start += 1
    while end > start and text[end - 1] in _ADDRESS_EDGE:
        end -= 1
    return start, end


def compute(
    nodes: list[NodeIn],
    stance: str,
    *,
    terms: Sequence[Term] = (),
    overrides: dict[str, PartyOverride] | None = None,
    user_terms: Sequence[UserTerm] = (),
    mask_quantity: bool = False,
    main_prefix: str | None = None,
) -> RedactionResult:
    """对正文节点执行 9.3 第 1–7 步、数量脱敏、疑似遗漏和金额比例。

    overrides 为 None 时按立场预选我方（销售选卖方、采购选买方，唯一时才选）。
    main_prefix 为主文件节点位置编号的前缀（如 "F1-"），确定合同总价时先在主文件中找（9.6）。
    """
    entities: dict[str, _Entity] = {}

    def entity(name: str, category: str) -> _Entity:
        key = P.normalize(name)
        if key not in entities:
            entities[key] = _Entity(key=key, name=name, category=category)
        ent = entities[key]
        if name not in ent.surfaces:
            ent.surfaces.append(name)
        return ent

    # 第 1 步的词条也作为对象登记（已记住的公司名、用户补充的公司名同时是 ORG 主体，Q-54）
    for t in terms:
        ent = entity(t.text, t.category)
        ent.from_term = True
        ent.term_our = ent.term_our or t.is_our_party
    for u in user_terms:
        ent = entity(u.text, u.category)
        ent.from_user = not ent.from_term

    # 第 2 步：签约方信息块
    for i, n in enumerate(nodes):
        for label, s, e in P.find_labeled_parties(n.text):
            ent = entity(n.text[s:e], "ORG")
            if ent.role_label == OTHER:
                ent.role_label = label
            if ent.block_node is None:
                ent.block_node = i

    def org_matchers() -> list[tuple[_Entity, re.Pattern]]:
        out = [(ent, P.name_pattern(s)) for ent in entities.values() if ent.category == "ORG" for s in ent.surfaces]
        out += [(ent, _alias_pattern(a)) for ent in entities.values() if ent.category == "ORG" for a in ent.aliases]
        return out

    # 第 2 步（续）：正文中其他带明确企业后缀的名称。与已知名称重叠的不再登记；但已知的是词条（已记住名称、用户词条）、
    # 且完全落在新名称内、没有到达名称结尾时，说明词条只是这个名称的一部分（“Aurora Fictive”之于
    # “Aurora Fictive Energy Co., Ltd.”），仍登记完整名称，词条在下面并为它的简称
    for i, n in enumerate(nodes):
        found = P.find_other_org_names(n.text)
        if not found:
            continue
        known = [(m.start(), m.end(), ent.from_term or ent.from_user)
                 for ent, pat in org_matchers() for m in pat.finditer(n.text)]
        for s, e in found:
            if any(ks < e and s < ke and not (frag and s <= ks and ke < e) for ks, ke, frag in known):
                continue
            ent = entity(n.text[s:e], "ORG")
            if ent.block_node is None:
                ent.block_node = i
            known.append((s, e, False))

    # 不留半个名称：已记住名称或用户词条（公司）是另一主体名称的一部分时，并为该主体的简称（同一代号）
    for frag in sorted((e for e in entities.values() if e.category == "ORG" and (e.from_term or e.from_user)),
                       key=lambda e: -len(e.key)):
        pat = _user_pattern(frag.name) if frag.from_user else P.name_pattern(frag.name)
        owner = next((o for o in entities.values() if o is not frag and o.category == "ORG"
                      and any(P.normalize(s) != frag.key and pat.search(s) for s in o.surfaces)), None)
        if owner is None:
            continue
        src = ("term", frag.key) if frag.from_term else ("user", None)
        for s in frag.surfaces:
            if s not in owner.aliases:
                owner.aliases.append(s)
                owner.alias_src[s] = src
        for a in frag.aliases:
            if a not in owner.aliases:
                owner.aliases.append(a)
                owner.alias_src[a] = frag.alias_src.get(a, ("alias", None))
        owner.term_our = owner.term_our or frag.term_our
        del entities[frag.key]

    # 第 3 步：简称，归入紧挨在结构前面的名称
    for n in nodes:
        found = P.find_aliases(n.text)
        if not found:
            continue
        occurrences = [(ent, m.end()) for ent, pat in org_matchers() for m in pat.finditer(n.text)]
        for struct_start, s, e in found:
            alias = n.text[s:e]
            owner = next((ent for ent, end in occurrences
                          if 0 <= struct_start - end <= 3 and not n.text[end:struct_start].strip()), None)
            if owner is None or P.normalize(alias) in entities:
                continue
            if all(P.normalize(alias) != P.normalize(a) for a in owner.aliases):
                owner.aliases.append(alias)

    # 类型
    for ent in entities.values():
        if ent.category != "ORG":
            continue
        ent.org_type = P.org_type(ent.name)

    # 第 1–3 步的候选放在一起，整体长的优先（不留半个名称）
    pool: list[tuple[_Entity, re.Pattern, str, str | None]] = []
    for ent in entities.values():
        by = "term" if ent.from_term else "user" if ent.from_user else "party"
        pattern = _user_pattern if by == "user" else P.name_pattern
        pool += [(ent, pattern(s), by, ent.key if ent.from_term else None) for s in ent.surfaces]
        for a in ent.aliases:
            a_by, a_term = ent.alias_src.get(a, ("alias", None))
            a_pat = _alias_pattern(a) if a_by == "alias" else _user_pattern(a) if a_by == "user" else P.name_pattern(a)
            pool.append((ent, a_pat, a_by, a_term))

    hints = tables.table_hints(nodes)
    qty_hints = tables.table_hints(nodes, with_qty=True) if mask_quantity else {}
    spans: list[_Span] = []
    occupied: list[list[tuple[int, int]]] = []
    for i, n in enumerate(nodes):
        text = n.text
        occ: list[tuple[int, int]] = []
        cands = [_Span(i, m.start(), m.end(), ent.category, ent.key, by, term=term)
                 for ent, pat, by, term in pool for m in pat.finditer(text)]
        _accept(cands, occ, spans, longest_first=True)
        for hit in detect.find_formatted(text):  # 第 4 步：按 find_formatted 的顺序（即优先级）逐个取舍
            _accept([_Span(i, hit.start, hit.end, hit.category, hit.key, "regex")], occ, spans)
        amounts = [_Span(i, h.start, h.end, h.category, h.key, "amount", h.currency, h.value)
                   for h in detect.find_amounts(text, hints.get(i))]
        _accept(amounts, occ, spans)
        if mask_quantity:  # 数量：第 5 步之后、第 6 步之前（设计 M4.md 第三节）
            column = "QTY" if qty_hints.get(i) == "QTY" else None
            _accept([_Span(i, h.start, h.end, "QTY", h.key, "amount") for h in detect.find_quantities(text, column)],
                    occ, spans)
        occupied.append(occ)

    # 第 6 步：人名。先在全部节点中按标签收集并登记为对象，再全文匹配（中文精确；英文区分大小写、要求单词边界）
    persons: list[_Entity] = []
    for i, n in enumerate(nodes):
        for h in detect.find_persons(n.text):
            if _overlaps(occupied[i], h.start, h.end):  # 标签后是公司名称等已替换的文字
                continue
            name = n.text[h.start:h.end]
            existing = entities.get(P.normalize(name))
            if existing is not None and existing.category != "PERSON":
                continue
            ent = entity(name, "PERSON")
            if ent not in persons:
                persons.append(ent)
    person_pats = [(ent, P.name_pattern(s, case_sensitive=True)) for ent in persons for s in ent.surfaces]
    if person_pats:
        for i, n in enumerate(nodes):
            _accept([_Span(i, m.start(), m.end(), "PERSON", ent.key, "person")
                     for ent, pat in person_pats for m in pat.finditer(n.text)], occupied[i], spans)

    # 第 7 步：地址。与已替换的文字重叠时只替换剩下的各段；只是国家、城市、港口名称的段保留（9.2 保留国家和港口）
    addresses: list[tuple[str, str]] = []  # (地址片段, 国家)
    for i, n in enumerate(nodes):
        for h in detect.find_addresses(n.text):
            for a, b in _free_segments(occupied[i], h.start, h.end):
                a, b = _trim(n.text, a, b)
                piece = n.text[a:b]
                if b <= a or not _WORD_CHAR.search(piece) or _is_place(piece):
                    continue
                before = len(spans)
                _accept([_Span(i, a, b, ADDR, h.key, "address")], occupied[i], spans)
                if len(spans) > before:
                    addresses.append((piece, h.key))
    # 同一地址在别处（无标签）再次出现时一并替换，避免出口复检因对照表中的地址而拦截（open_questions Q-76）
    repeat = [(P.name_pattern(piece), country) for piece, country in dict.fromkeys(addresses)
              if len(piece) >= _ADDRESS_REPEAT_MIN and _LETTER_OR_HAN.search(piece)]
    if repeat:
        for i, n in enumerate(nodes):
            _accept([_Span(i, m.start(), m.end(), ADDR, country, "address")
                     for pat, country in repeat for m in pat.finditer(n.text)], occupied[i], spans)

    spans.sort(key=lambda s: (s.node, s.start))
    _apply_ours(entities, stance, overrides, {s.key for s in spans})
    _assign_countries(entities, nodes, spans)
    if overrides:
        for ent in entities.values():
            o = overrides.get(ent.key)
            if o is not None:
                ent.country = o.country or ent.country
                ent.org_type = o.org_type or ent.org_type
                ent.confirmed = o.confirmed
    result = _assemble(nodes, stance, entities, spans, main_prefix)

    # 疑似遗漏：全部替换项确定之后，在每个节点未替换的文字上识别（9.5）
    for i, n in enumerate(nodes):
        result.suspects += [SuspectOut(n.loc, s.start, s.end, s.reason) for s in S.find_suspects(n.text, occupied[i])]
    result.matched_terms = {s.term for s in spans if s.term}
    return result


def _apply_ours(entities: dict[str, _Entity], stance: str, overrides: dict[str, PartyOverride] | None,
                present: set[str]) -> None:
    """我方：有 overrides 时按用户的设置；否则已记住为我方、且在本文件中出现的名称优先，再按立场预选。"""
    orgs = [e for e in entities.values() if e.category == "ORG" and e.key in present]
    if overrides is not None:
        for ent in orgs:
            o = overrides.get(ent.key)
            ent.is_our = bool(o and o.is_our_party)
        return
    remembered = [e for e in orgs if e.term_our]
    if len(remembered) == 1:
        remembered[0].is_our = True
        return
    wanted = SELLER if stance == "sales" else BUYER if stance == "purchase" else None
    candidates = [e for e in orgs if wanted and e.role_label == wanted]
    if len(candidates) == 1:
        candidates[0].is_our = True


def _assign_countries(entities: dict[str, _Entity], nodes: list[NodeIn], spans: list[_Span]) -> None:
    """9.4：在主体所在行及其后的信息块中找国家；找不到时中文企业名视为中国，否则“国家未知”。"""
    first_node = {}
    for s in spans:
        first_node.setdefault(s.key, s.node)
    names = [x for e in entities.values() if e.category == "ORG" for x in (*e.surfaces, *e.aliases)]
    for ent in entities.values():
        if ent.category != "ORG":
            continue
        start = ent.block_node if ent.block_node is not None else first_node.get(ent.key)
        lines: list[str] = []
        if start is not None:
            lines.append(nodes[start].text)
            # 之后紧接着的信息行（电话、联系人等）继续往下看，只在地址、国家行中找；遇到其他内容即停止
            for n in nodes[start + 1:start + 1 + _BLOCK_MAX_LINES]:
                m = _INFO_LINE.match(n.text)
                if not m:
                    break
                if _ADDRESS_LABEL.match(m.group(1)):
                    lines.append(n.text)
        ent.country = P.detect_country(lines, names) or P.default_country(ent.name) or COUNTRY_UNKNOWN


def _ratio_inserts(nodes: list[NodeIn], spans: list[_Span], main_prefix: str | None) -> dict[int, list[tuple[int, str]]]:
    """金额比例（9.6）：{节点下标: [(在原文中的插入位置, 比例文字)]}。比例只进入 redacted，不进入对照表和替换项。"""
    amounts = [ratio.AmountAt(s.node, detect.Hit(s.start, s.end, s.category, s.key, s.currency, s.value))
               for s in spans if s.category == "AMOUNT"]
    total = ratio.contract_total(nodes, amounts, main_prefix)
    out: dict[int, list[tuple[int, str]]] = {}
    if total is None:
        return out
    for a in amounts:
        if ratio.applies(a.hit, total):
            pos = ratio.insert_position(nodes[a.node].text, a.hit.end)
            out.setdefault(a.node, []).append((pos, ratio.ratio_text(a.hit.value, total[1])))  # type: ignore[arg-type]
    return out


def _render(text: str, items: list[ItemOut], inserts: list[tuple[int, str]]) -> str:
    """按原文位置换上代号并插入比例文字。插入位置落在某个替换项内部时移到该项之后。"""
    edits = [(it.char_start, it.char_end, it.placeholder, 1) for it in items]
    for pos, extra in inserts:
        inside = next((it for it in items if it.char_start < pos < it.char_end), None)
        pos = inside.char_end if inside else pos
        edits.append((pos, pos, extra, 0))
    out: list[str] = []
    cur = 0
    for start, end, new, _order in sorted(edits, key=lambda e: (e[0], e[3])):
        start = max(start, cur)
        out.append(text[cur:start])
        out.append(new)
        cur = max(cur, end)
    out.append(text[cur:])
    return "".join(out)


def _assemble(nodes: list[NodeIn], stance: str, entities: dict[str, _Entity], spans: list[_Span],
              main_prefix: str | None) -> RedactionResult:
    ours = [e for e in entities.values() if e.is_our]
    if len(ours) > 1:
        raise ValueError("我方只能有一个")
    alloc = Allocator()
    mapping: dict[str, str] = {}
    extra: dict[str, list[str]] = {}
    items: list[ItemOut] = []
    party_order: list[str] = []
    codes: dict[str, str] = {}

    def remember(code: str, primary: str | None, surface: str) -> None:
        if primary is not None and code not in mapping:
            mapping[code] = primary
        if surface != mapping.get(code) and surface not in extra.setdefault(code, []):
            extra[code].append(surface)

    for s in spans:
        node = nodes[s.node]
        surface = node.text[s.start:s.end]
        category = s.category
        if category == "ORG":
            ent = entities[s.key]
            if ent.is_our:
                category, code = "OUR", OUR_CODE
            else:
                code = alloc.org(ent.key, code_role(ent.role_label, stance, ent.org_type))
            if ent.key not in codes:
                codes[ent.key] = code
                party_order.append(ent.key)
            remember(code, ent.name, surface)
        elif category in FIXED:
            code = FIXED[category]
            remember(code, None, surface)
        elif category == ADDR:
            code = address_code(s.key)
            remember(code, None, surface)
        else:
            code = alloc.numbered(category, s.key)
            primary = entities[s.key].name if s.key in entities and category in ("PERSON", "CUSTOM") else surface
            remember(code, primary, surface)
        items.append(ItemOut(node.loc, s.start, s.end, category, code, s.detected_by))

    for code, values in extra.items():
        for n, value in enumerate(values, start=1):
            mapping[f"{code}#{n}"] = value

    inserts = _ratio_inserts(nodes, spans, main_prefix)
    by_node: dict[str, list[ItemOut]] = {}
    for it in items:
        by_node.setdefault(it.loc, []).append(it)
    redacted = {n.loc: _render(n.text, by_node.get(n.loc, []), inserts.get(i, [])) for i, n in enumerate(nodes)}

    parties = [
        PartyOut(key=k, code=codes[k], role_label=entities[k].role_label, country=entities[k].country,
                 org_type=entities[k].org_type, is_our_party=entities[k].is_our, confirmed=entities[k].confirmed)
        for k in party_order
    ]
    return RedactionResult(parties=parties, items=items, mapping=mapping, redacted=redacted)


def outbound_text(nodes: list[NodeIn], parties: list[outbound.PartyInfo], redacted: dict[str, str]) -> str:
    """9.7 外发文本：【主体说明】+ 各正文节点一行，报价明细表整表换成一行汇总。nodes 为原文。"""
    return outbound.build(
        parties,
        [(n.loc, n.clause_no, redacted.get(n.loc, "")) for n in nodes],
        quotes.find_quote_tables(nodes, redacted),
    )


# ---------------------------------------------------------------- 数据库


def _body_rows(db: Session, task_id: uuid.UUID) -> list[DocNode]:
    return list(db.scalars(
        select(DocNode)
        .join(TaskFile, TaskFile.id == DocNode.file_id)
        .where(DocNode.task_id == task_id, DocNode.part == "body")
        .order_by(TaskFile.seq, DocNode.ord)
    ))


def _node_texts(rows: list[DocNode]) -> dict[str, str]:
    return {r.loc: crypto.decrypt_text(r.text_enc) if r.text_enc is not None else "" for r in rows}


def _load_terms(db: Session) -> list[Term]:
    """启用中的已记住名称与自定义敏感词，解密到内存（18.12），不写日志。"""
    rows = db.scalars(select(RedactionTerm).where(RedactionTerm.enabled.is_(True)).order_by(RedactionTerm.created_at))
    return [Term(crypto.decrypt_text(r.term_enc), r.kind, r.category, r.is_our_party) for r in rows]


def _user_terms(db: Session, task_id: uuid.UUID, texts: dict[str, str]) -> list[UserTerm]:
    """本任务的用户词条：detected_by="user" 的替换项在原文中的文字，按（规范形式, 类别）去重（Q-70 ②）。"""
    out: list[UserTerm] = []
    seen: set[tuple[str, str]] = set()
    rows = db.scalars(select(RedactionItem).where(RedactionItem.task_id == task_id, RedactionItem.detected_by == "user")
                      .order_by(RedactionItem.id))
    for r in rows:
        text = texts.get(r.loc, "")[r.char_start:r.char_end]
        category = "ORG" if r.category == "OUR" else r.category
        if not text.strip() or category not in USER_CATEGORIES:
            continue
        key = (P.normalize(text), category)
        if key not in seen:
            seen.add(key)
            out.append(UserTerm(text, category))
    return out


def _main_prefix(db: Session, task_id: uuid.UUID) -> str | None:
    seq = db.scalar(select(TaskFile.seq).where(TaskFile.task_id == task_id, TaskFile.is_main.is_(True)))
    return f"F{seq}-" if seq is not None else None


def _write_map(db: Session, task_id: uuid.UUID, mapping: dict[str, str]) -> None:
    blob = crypto.encrypt_text(json.dumps(mapping, ensure_ascii=False, sort_keys=True))
    row = db.get(RedactionMap, task_id)
    if row is None:
        db.add(RedactionMap(task_id=task_id, map_enc=blob))
    else:
        row.map_enc = blob


def _load_full_map(db: Session, task_id: uuid.UUID) -> dict[str, str]:
    row = db.get(RedactionMap, task_id)
    if row is None:
        return {}
    return json.loads(crypto.decrypt_text(row.map_enc))


def _write_suspects(db: Session, task_id: uuid.UUID, found: list[SuspectOut]) -> None:
    """与上次相同位置、相同原因的疑似遗漏保留原 id 和决定；不再出现的删除；新出现的为 pending（设计 M4.md 第四节）。"""
    old = {(r.loc, r.char_start, r.char_end, r.reason): r
           for r in db.scalars(select(RedactionSuspect).where(RedactionSuspect.task_id == task_id))}
    seen: set[tuple[str, int, int, str]] = set()
    for s in found:
        key = (s.loc, s.char_start, s.char_end, s.reason)
        if key in seen:
            continue
        seen.add(key)
        row = old.get(key)
        if row is None:
            db.add(RedactionSuspect(task_id=task_id, loc=s.loc, char_start=s.char_start, char_end=s.char_end,
                                    reason=s.reason, decision="pending"))
        elif row.decision == "replace":
            # 选了“替换”却仍被列出，说明文字没有被替换掉：退回待处理，不能当作已处理而外发
            row.decision, row.category, row.decided_by, row.decided_at = "pending", None, None, None
    for key, row in old.items():
        if key not in seen:
            db.delete(row)
    db.flush()


def _run(db: Session, task: Task, overrides: dict[str, PartyOverride] | None,
         existing: dict[str, Party], extra_terms: Sequence[UserTerm] = ()) -> RedactionResult:
    rows = _body_rows(db, task.id)
    texts = _node_texts(rows)
    nodes = [NodeIn(r.loc, texts[r.loc], r.clause_no) for r in rows]
    user_terms = _user_terms(db, task.id, texts)
    seen = {(P.normalize(u.text), u.category) for u in user_terms}
    user_terms += [u for u in extra_terms if (P.normalize(u.text), u.category) not in seen]
    result = compute(nodes, task.stance, terms=_load_terms(db), overrides=overrides, user_terms=user_terms,
                     mask_quantity=bool(get_setting(db, "redaction.mask_quantity")),
                     main_prefix=_main_prefix(db, task.id))

    for r in rows:
        r.redacted_text = result.redacted[r.loc]
    db.execute(delete(RedactionItem).where(RedactionItem.task_id == task.id))
    db.add_all(RedactionItem(task_id=task.id, loc=i.loc, char_start=i.char_start, char_end=i.char_end,
                             category=i.category, placeholder=i.placeholder, detected_by=i.detected_by)
               for i in result.items)
    _write_map(db, task.id, result.mapping)
    _write_suspects(db, task.id, result.suspects)

    # 签约方：沿用原来的行（id 不变），先把代号改成临时值，避免交换代号时触发唯一约束
    for row in existing.values():
        row.code = f"~{row.id.hex[:24]}"
    db.flush()
    kept = set()
    for p in result.parties:
        row = existing.get(p.key)
        if row is None:
            row = Party(task_id=task.id)
            db.add(row)
        kept.add(p.key)
        row.code, row.role_label, row.country, row.org_type = p.code, p.role_label, p.country, p.org_type
        row.is_our_party, row.confirmed = p.is_our_party, p.confirmed
    for key, row in existing.items():
        if key not in kept:
            db.delete(row)
    db.flush()
    return result


def _count_hits(db: Session, matched: set[str]) -> None:
    """命中计数（9.10、18.12、Q-73 ④）：每个命中的已记住名称、自定义敏感词 hit_count + 1，记下命中时间。"""
    if not matched:
        return
    rows = db.execute(select(RedactionTerm.id, RedactionTerm.term_enc).where(RedactionTerm.enabled.is_(True))).all()
    ids = [rid for rid, blob in rows if P.normalize(crypto.decrypt_text(blob)) in matched]
    if ids:
        db.execute(update(RedactionTerm).where(RedactionTerm.id.in_(ids))
                   .values(hit_count=RedactionTerm.hit_count + 1, last_hit_at=utcnow()))


def redact_task(db: Session, task: Task) -> None:
    """parse 阶段的脱敏：写 parties、redaction_items、redaction_maps、redaction_suspects 和正文节点的 redacted_text，
    并统计已记住名称、自定义敏感词的命中次数（只在这里统计，第二步重算不计数）。不提交。"""
    db.execute(delete(Party).where(Party.task_id == task.id))
    db.execute(delete(RedactionSuspect).where(RedactionSuspect.task_id == task.id))
    db.flush()
    result = _run(db, task, None, {})
    _count_hits(db, result.matched_terms)


def _existing_parties(db: Session, task: Task) -> dict[str, Party]:
    mapping = load_redaction_map(db, task.id)
    out = {}
    for row in db.scalars(select(Party).where(Party.task_id == task.id)):
        name = mapping.get(row.code)
        if name is not None:
            out[P.normalize(name)] = row
    return out


def _current_overrides(existing: dict[str, Party]) -> dict[str, PartyOverride]:
    return {key: PartyOverride(row.is_our_party, row.country, row.org_type, row.confirmed)
            for key, row in existing.items()}


def apply_party_changes(db: Session, task: Task, changes: list[PartyChange]) -> None:
    """修改我方、国家、类型后重算代号、对照表和 redacted_text（PUT /parties、重新审核使用）。不提交。

    设置某一方为我方时，其他各方自动取消；有修改时要求结果恰好一方为我方，否则抛出 ValueError。
    changes 为空时只按当前设置重算（立场或来源改变后）。用户词条和疑似遗漏的“忽略”决定在重算中保留。
    """
    existing = _existing_parties(db, task)
    by_id = {row.id: key for key, row in existing.items()}
    overrides = _current_overrides(existing)
    for ch in changes:
        key = by_id.get(ch.party_id)
        if key is None:
            raise ValueError("签约方不存在")
        o = overrides[key]
        if ch.is_our_party is not None:
            if ch.is_our_party:
                for other in overrides.values():
                    other.is_our_party = False
            o.is_our_party = ch.is_our_party
        if ch.country is not None:
            o.country = ch.country
        if ch.org_type is not None:
            o.org_type = ch.org_type
    if changes:
        if sum(o.is_our_party for o in overrides.values()) != 1:
            raise ValueError("必须恰好一方为我方")
        for o in overrides.values():
            o.confirmed = True
    _run(db, task, overrides, existing)


# ---------------------------------------------------------------- 用户词条、疑似遗漏、已记住的名称（设计 M4.md 第四节）


def _body_node(db: Session, task_id: uuid.UUID, loc: str) -> DocNode | None:
    return db.scalar(select(DocNode).where(DocNode.task_id == task_id, DocNode.loc == loc, DocNode.part == "body"))


def _strip_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _add_term(db: Session, task: Task, text: str, loc: str, start: int, end: int, category: str) -> None:
    """把 text[start:end] 作为用户词条加入并整体重算；重算后该位置必须已被替换。

    所选文字若按出口复检规则出现在固定提示文字中（Q-77 ③），设为敏感信息后将无法发送，先拒绝。
    """
    term = text[start:end]
    from app.review.prompt import fixed_prompt_text  # 避免循环导入
    if any(r.category == "MAP" for r in recheck(fixed_prompt_text(db), real_values=[term], terms=[])):
        raise RedactionInputError(422, "TERM_IN_PROMPT",
                                  "所选文字在审核提示中也会出现，设为敏感信息后将无法发送，请只选择具体的名称或编号")
    m = _user_pattern(term).match(text, start)
    if m is None or m.end() != end:  # 选在单词中间（“ora Fic”）时全文匹配不到这里
        raise RedactionInputError(422, "INVALID_SELECTION", "所选位置不正确，请选择完整的词")
    existing = _existing_parties(db, task)
    _run(db, task, _current_overrides(existing), existing, [UserTerm(term, category)])
    covered = [(i.char_start, i.char_end) for i in db.scalars(
        select(RedactionItem).where(RedactionItem.task_id == task.id, RedactionItem.loc == loc))]
    if any(not text[k].isspace() and not any(s <= k < e for s, e in covered) for k in range(start, end)):
        # 防御性检查：不应出现；调用方不提交，本次修改随事务回滚
        raise RedactionInputError(422, "INVALID_SELECTION", "所选位置不正确，请选择完整的词")


def add_user_term(db: Session, task: Task, *, loc: str, char_start: int, char_end: int, category: str) -> None:
    """手动补充（19.4）：校验位置 → 取原文片段（去掉首尾空白后 2–100 个字符）→ 加入用户词条 → 整体重算。不提交。

    同文字全文替换（英文不区分大小写、忽略多余空格）。失败时抛出 RedactionInputError，消息不含原文。
    """
    if category not in USER_CATEGORIES:
        raise RedactionInputError(422, "VALIDATION_ERROR", "类别不正确")
    node = _body_node(db, task.id, loc)
    text = crypto.decrypt_text(node.text_enc) if node is not None and node.text_enc is not None else None
    if text is None or not (0 <= char_start < char_end <= len(text)):
        raise RedactionInputError(422, "INVALID_SELECTION", "所选位置不正确")
    start, end = _strip_span(text, char_start, char_end)
    if not MANUAL_MIN <= end - start <= MANUAL_MAX:
        raise RedactionInputError(422, "SELECTION_LENGTH", "请选择 2–100 个字符")
    taken = db.scalar(select(func.count()).select_from(RedactionItem).where(
        RedactionItem.task_id == task.id, RedactionItem.loc == loc,
        RedactionItem.char_start < end, RedactionItem.char_end > start))
    if taken:
        raise RedactionInputError(409, "ALREADY_REDACTED", "所选文字已被替换，请选择未替换的文字")
    _add_term(db, task, text, loc, start, end, category)


def decide_suspect(db: Session, task: Task, suspect_id: int, *, decision: str, category: str | None,
                   user_id: uuid.UUID | None) -> None:
    """疑似遗漏的决定（19.4）。不提交。

    replace：把疑似遗漏的文字作为用户词条（类别 ORG / PERSON / CUSTOM），全文替换后整体重算；
    ignore：本条及本任务中文字相同（规范形式）、原因相同的其他 pending 疑似遗漏都置 ignore（Q-70 ④）。
    """
    row = db.get(RedactionSuspect, suspect_id)
    if row is None or row.task_id != task.id:
        raise RedactionInputError(404, "SUSPECT_NOT_FOUND", "疑似遗漏不存在")
    if decision not in ("replace", "ignore") or (decision == "replace" and category not in USER_CATEGORIES):
        raise RedactionInputError(422, "VALIDATION_ERROR", "处理方式或类别不正确")
    texts = _node_texts(_body_rows(db, task.id))
    text = texts.get(row.loc, "")
    now = utcnow()
    if decision == "ignore":
        key = P.normalize(text[row.char_start:row.char_end])
        same = db.scalars(select(RedactionSuspect).where(
            RedactionSuspect.task_id == task.id, RedactionSuspect.reason == row.reason,
            RedactionSuspect.decision == "pending", RedactionSuspect.id != row.id))
        for r in [row, *(r for r in same if P.normalize(texts.get(r.loc, "")[r.char_start:r.char_end]) == key)]:
            r.decision, r.category, r.decided_by, r.decided_at = "ignore", None, user_id, now
        db.flush()
        return
    row.decision, row.category, row.decided_by, row.decided_at = "replace", category, user_id, now
    db.flush()
    start, end = _strip_span(text, row.char_start, row.char_end)
    if end <= start:
        raise RedactionInputError(422, "INVALID_SELECTION", "所选位置不正确")
    _add_term(db, task, text, row.loc, start, end, category)  # type: ignore[arg-type]


def _flat(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ")


def suspect_views(db: Session, task: Task) -> list[dict]:
    """疑似遗漏列表：[{id, loc, reason, decision, category, text, before, after}]，pending 在前，其余按文档顺序。

    text 为疑似遗漏的原文，before / after 为同一节点中前后各最多 30 个字符的原文（换行换成空格），只用于第二步显示。
    """
    rows = _body_rows(db, task.id)
    order = {r.loc: k for k, r in enumerate(rows)}
    texts = _node_texts(rows)
    suspects = sorted(db.scalars(select(RedactionSuspect).where(RedactionSuspect.task_id == task.id)),
                      key=lambda r: (r.decision != "pending", order.get(r.loc, len(order)), r.char_start, r.id))
    out = []
    for r in suspects:
        t = texts.get(r.loc, "")
        out.append({
            "id": r.id, "loc": r.loc, "reason": r.reason, "decision": r.decision, "category": r.category,
            "text": t[r.char_start:r.char_end],
            "before": _flat(t[max(0, r.char_start - CONTEXT_CHARS):r.char_start]),
            "after": _flat(t[r.char_end:r.char_end + CONTEXT_CHARS]),
        })
    return out


def term_hash(text: str) -> str:
    """redaction_terms.term_hash：规范形式（全角转半角、去空白、不区分大小写）的 SHA-256。"""
    return hashlib.sha256(P.normalize(text).encode("utf-8")).hexdigest()


def learn_confirmed_names(db: Session, task: Task, user_id: uuid.UUID | None) -> None:
    """确认发送时写入已记住的名称（9.10、Q-73）。不提交。

    签约方列表中每一方的主要名称（对照表中该代号的值）+ 用户词条中类别为公司、人名的文字，写入 redaction_terms
    （kind=learned）；已存在时不重复插入，被勾选为我方的名称把 is_our_party 置 true（只升不降）。
    """
    mapping = load_redaction_map(db, task.id)
    names: dict[str, list] = {}  # term_hash → [文字, 类别, 是否我方]

    def add(text: str, category: str, our: bool) -> None:
        if not text.strip():
            return
        entry = names.setdefault(term_hash(text), [text, category, False])
        entry[2] = entry[2] or our

    for p in db.scalars(select(Party).where(Party.task_id == task.id).order_by(Party.code)):
        name = mapping.get(p.code)
        if name is not None:
            add(name, "ORG", p.is_our_party)
    items = list(db.scalars(select(RedactionItem).where(
        RedactionItem.task_id == task.id, RedactionItem.detected_by == "user",
        RedactionItem.category.in_(("ORG", "OUR", "PERSON"))).order_by(RedactionItem.id)))
    if items:
        texts = _node_texts(_body_rows(db, task.id))
        for it in items:
            add(texts.get(it.loc, "")[it.char_start:it.char_end].strip(),
                "PERSON" if it.category == "PERSON" else "ORG", it.category == "OUR")
    for h, (text, category, our) in names.items():
        # 并发确认的两个任务写入同一名称时不报错（唯一约束 kind + term_hash）
        db.execute(pg_insert(RedactionTerm).values(
            kind="learned", term_enc=crypto.encrypt_text(text), term_hash=h, category=category,
            is_our_party=our, created_by=user_id, created_at=utcnow(),
        ).on_conflict_do_nothing(index_elements=["kind", "term_hash"]))
        if our:
            db.execute(update(RedactionTerm).where(RedactionTerm.kind == "learned", RedactionTerm.term_hash == h,
                                                   RedactionTerm.is_our_party.is_(False)).values(is_our_party=True))
    db.flush()


# ---------------------------------------------------------------- 外发文本与查询


@dataclass
class OutboundBuild:
    text: str  # 与 build_outbound_text 相同
    quote_tables: list[quotes.QuoteTable]  # 本次识别出的报价明细表（M5：分段、覆盖说明、汇总行位置映射）


def build_outbound(db: Session, task: Task) -> OutboundBuild:
    """9.7 外发文本：【主体说明】+ 各正文节点一行；报价明细表整表换成一行汇总（需要解密原文识别表格）。"""
    from app.checks.service import load_inputs, table_notes

    parties = list(db.scalars(select(Party).where(Party.task_id == task.id)))
    rows = _body_rows(db, task.id)
    texts = _node_texts(rows)
    nodes = [NodeIn(r.loc, texts[r.loc], r.clause_no) for r in rows]
    redacted = {r.loc: r.redacted_text or "" for r in rows}
    tables = quotes.find_quote_tables(nodes, redacted)
    text = outbound.build(
        [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in parties],
        [(n.loc, n.clause_no, redacted.get(n.loc, "")) for n in nodes],
        tables,
        table_notes=table_notes(*load_inputs(db, task.id)),
    )
    return OutboundBuild(text, tables)


def build_outbound_text(db: Session, task: Task) -> str:
    return build_outbound(db, task).text


def redaction_summary(db: Session, task: Task) -> dict[str, int]:
    """{类别代码: 替换次数}。"""
    rows = db.execute(
        select(RedactionItem.category, func.count()).where(RedactionItem.task_id == task.id)
        .group_by(RedactionItem.category)
    ).all()
    return {category: count for category, count in rows}


def load_redaction_map(db: Session, task_id: uuid.UUID) -> dict[str, str]:
    """{代号: 真实值}，只含能唯一还原的代号（用于还原）。"""
    return {k: v for k, v in _load_full_map(db, task_id).items() if "#" not in k}


def load_real_values(db: Session, task_id: uuid.UUID) -> list[str]:
    """对照表中的全部真实值，含简称和共用代号类别的值（用于出口复检，M2-09）。"""
    return list(dict.fromkeys(_load_full_map(db, task_id).values()))
