"""脱敏核心（SPEC 9.1–9.3、9.9，设计 docs/design/M2.md 第六节）。

M2 实现 9.3 第 1–5 步：已记住的名称与自定义敏感词、签约方、简称、格式化信息、金额。
人名、地址（第 6、7 步）、疑似遗漏、金额比例、报价明细表汇总在 M4。

结构：compute() 是不碰数据库的纯函数（输入正文节点原文，输出代号、替换位置、对照表和脱敏后文本）；
redact_task() 等函数负责读写数据库，不提交事务，由调用方提交。

对照表（redaction_maps.map_enc）是加密的 JSON {键: 真实值}：
  - 键为代号（我方、客户_A、人员_1、编号_1、[金额_1] 等能唯一还原的代号）时，值是该对象的主要写法，用于还原；
  - 键为“代号#序号”时，值是同一对象的其他写法（简称、大小写不同的写法）或共用代号类别（[电话] 等）的真实值，
    只用于出口复检，不用于还原（open_questions Q-45）。
真实值不写日志，parties 表不存名称。
"""
import json
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import DocNode, Party, RedactionItem, RedactionMap, RedactionTerm, Task, TaskFile
from app.redaction import detect, outbound, parties as P
from app.redaction.placeholders import FIXED, OTHER, OUR_CODE, SELLER, BUYER, Allocator, code_role
from app.security import crypto

COUNTRY_UNKNOWN = "国家未知"
_TABLE_LOC = re.compile(r"^(?P<table>.+-T\d+)-R(?P<row>\d+)-C(?P<col>\d+)$")  # DOCX、PDF 表格单元格
_SHEET_LOC = re.compile(r"^(?P<table>F\d+-S\d+)-(?P<col>[A-Z]+)(?P<row>\d+)$")  # XLSX 单元格，整个工作表为一个表格
_HEADER_SCAN_ROWS = 20  # 表头行只在每个表格的前 20 行中找（open_questions Q-65）
_PRICE_HEADER = re.compile(r"单价|unit\s*price|price", re.IGNORECASE)
_AMOUNT_HEADER = re.compile(r"金额|总价|总额|amount|total", re.IGNORECASE)
_BLOCK_MAX_LINES = 6  # 签约方所在行之后，最多再看几行找国家（9.4）
_INFO_LINE = re.compile(
    r"^\s*(注册地址|通讯地址|办公地址|地址|国家|国别|电话|手机|传真|邮箱|电子邮件|联系人|统一社会信用代码|税号|"
    r"(?i:registered\s+address|address|add|country|tel|telephone|phone|mobile|fax|e-?mail|contact|attn))\s*[:：]"
)
_ADDRESS_LABEL = re.compile(r"(?i:.*地址|国家|国别|.*address|add|country)$")


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
class RedactionResult:
    parties: list[PartyOut]
    items: list[ItemOut]
    mapping: dict[str, str]
    redacted: dict[str, str]  # loc → 脱敏后文本


# ---------------------------------------------------------------- 识别


@dataclass
class _Entity:
    key: str
    name: str
    category: str  # ORG / PERSON / CUSTOM
    role_label: str = OTHER
    from_term: bool = False
    term_our: bool = False
    surfaces: list[str] = field(default_factory=list)  # 全称的写法
    aliases: list[str] = field(default_factory=list)
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


def _overlaps(occupied: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < e and s < end for s, e in occupied)


def _accept(cands: list[_Span], occupied: list[tuple[int, int]], out: list[_Span]) -> None:
    """同一步内长的优先，与已替换的文字重叠的不再采用（9.3：已替换的文字不再参与后续识别）。"""
    for c in sorted(cands, key=lambda c: (c.start, -(c.end - c.start))):
        if c.end > c.start and not _overlaps(occupied, c.start, c.end):
            occupied.append((c.start, c.end))
            out.append(c)


def _alias_pattern(alias: str) -> re.Pattern:
    # 全大写的短英文简称区分大小写，避免误伤普通单词
    short_caps = alias.isascii() and alias.isupper() and len(alias) <= 6
    return P.name_pattern(alias, case_sensitive=short_caps)


def _table_cell(loc: str) -> tuple[str, str, int] | None:
    """表格单元格的 (表格标识, 列, 行号)；不是表格单元格时 None。"""
    m = _TABLE_LOC.match(loc) or _SHEET_LOC.match(loc)
    return (m["table"], m["col"], int(m["row"])) if m else None


def _header_kind(text: str) -> str | None:
    if _PRICE_HEADER.search(text):
        return "PRICE"
    if _AMOUNT_HEADER.search(text):
        return "AMOUNT"
    return None


def _table_hints(nodes: list[NodeIn]) -> dict[int, str]:
    """表格列提示（9.3 第 5 步，Q-65）：表头含“单价、Unit Price”的列为 PRICE，含“金额、总价、Amount”的列为 AMOUNT。

    表头行是每个表格前 20 行中第一个含单价或金额表头的行（XLSX 报价单的表头上方常有标题行）；
    只有表头行之下的单元格得到提示。没有这样的行时该表格没有列提示。
    """
    cells: list[tuple[int, str, str, int]] = []
    header_row: dict[str, int] = {}
    for i, n in enumerate(nodes):
        cell = _table_cell(n.loc)
        if cell is None:
            continue
        table, col, row = cell
        cells.append((i, table, col, row))
        if row <= _HEADER_SCAN_ROWS and _header_kind(n.text) and row < header_row.get(table, row + 1):
            header_row[table] = row
    headers: dict[tuple[str, str], str] = {}
    for i, table, col, row in cells:
        if header_row.get(table) == row:
            kind = _header_kind(nodes[i].text)
            if kind:
                headers[(table, col)] = kind
    return {i: headers[(t, c)] for i, t, c, r in cells if r > header_row.get(t, r) and (t, c) in headers}


def compute(
    nodes: list[NodeIn],
    stance: str,
    *,
    terms: list[Term] = (),
    overrides: dict[str, PartyOverride] | None = None,
) -> RedactionResult:
    """对正文节点执行 9.3 第 1–5 步。overrides 为 None 时按立场预选我方（销售选卖方、采购选买方，唯一时才选）。"""
    entities: dict[str, _Entity] = {}

    def entity(name: str, category: str) -> _Entity:
        key = P.normalize(name)
        if key not in entities:
            entities[key] = _Entity(key=key, name=name, category=category)
        ent = entities[key]
        if name not in ent.surfaces:
            ent.surfaces.append(name)
        return ent

    # 第 1 步的词条也作为对象登记（已记住的公司名同时是 ORG 主体）
    for t in terms:
        ent = entity(t.text, t.category)
        ent.from_term = True
        ent.term_our = ent.term_our or t.is_our_party

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

    # 第 2 步（续）：正文中其他带明确企业后缀的名称
    for i, n in enumerate(nodes):
        found = P.find_other_org_names(n.text)
        if not found:
            continue
        known = [(m.start(), m.end()) for _ent, pat in org_matchers() for m in pat.finditer(n.text)]
        for s, e in found:
            if not _overlaps(known, s, e):
                ent = entity(n.text[s:e], "ORG")
                if ent.block_node is None:
                    ent.block_node = i
                known.append((s, e))

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

    # 类型、国家、我方
    for ent in entities.values():
        if ent.category != "ORG":
            continue
        ent.org_type = P.org_type(ent.name)
    _apply_ours(entities, stance, overrides)

    # 逐节点按 9.3 的顺序取替换位置
    hints = _table_hints(nodes)
    term_pats = [(ent, P.name_pattern(s)) for ent in entities.values() if ent.from_term for s in ent.surfaces]
    party_pats = [(ent, P.name_pattern(s)) for ent in entities.values()
                  if ent.category == "ORG" and not ent.from_term for s in ent.surfaces]
    alias_pats = [(ent, _alias_pattern(a)) for ent in entities.values() if ent.category == "ORG" for a in ent.aliases]
    spans: list[_Span] = []
    for i, n in enumerate(nodes):
        text = n.text
        occupied: list[tuple[int, int]] = []
        for pats, by in ((term_pats, "term"), (party_pats, "party"), (alias_pats, "alias")):
            cands = [_Span(i, m.start(), m.end(), ent.category, ent.key, by)
                     for ent, pat in pats for m in pat.finditer(text)]
            _accept(cands, occupied, spans)
        for hit in detect.find_formatted(text):  # 第 4 步：按 find_formatted 的顺序（即优先级）逐个取舍
            _accept([_Span(i, hit.start, hit.end, hit.category, hit.key, "regex")], occupied, spans)
        amounts = [_Span(i, h.start, h.end, h.category, h.key, "amount") for h in detect.find_amounts(text, hints.get(i))]
        _accept(amounts, occupied, spans)

    spans.sort(key=lambda s: (s.node, s.start))
    _assign_countries(entities, nodes, spans)
    if overrides:
        for ent in entities.values():
            o = overrides.get(ent.key)
            if o is not None:
                ent.country = o.country or ent.country
                ent.org_type = o.org_type or ent.org_type
                ent.confirmed = o.confirmed
    return _assemble(nodes, stance, entities, spans)


def _apply_ours(entities: dict[str, _Entity], stance: str, overrides: dict[str, PartyOverride] | None) -> None:
    orgs = [e for e in entities.values() if e.category == "ORG"]
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


def _assemble(nodes: list[NodeIn], stance: str, entities: dict[str, _Entity], spans: list[_Span]) -> RedactionResult:
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
        else:
            code = alloc.numbered(category, s.key)
            primary = entities[s.key].name if s.key in entities and category in ("PERSON", "CUSTOM") else surface
            remember(code, primary, surface)
        items.append(ItemOut(node.loc, s.start, s.end, category, code, s.detected_by))

    for code, values in extra.items():
        for n, value in enumerate(values, start=1):
            mapping[f"{code}#{n}"] = value

    redacted: dict[str, str] = {}
    by_node: dict[str, list[ItemOut]] = {}
    for it in items:
        by_node.setdefault(it.loc, []).append(it)
    for n in nodes:
        text = n.text
        for it in sorted(by_node.get(n.loc, []), key=lambda x: x.char_start, reverse=True):
            text = text[:it.char_start] + it.placeholder + text[it.char_end:]
        redacted[n.loc] = text

    parties = [
        PartyOut(key=k, code=codes[k], role_label=entities[k].role_label, country=entities[k].country,
                 org_type=entities[k].org_type, is_our_party=entities[k].is_our, confirmed=entities[k].confirmed)
        for k in party_order
    ]
    return RedactionResult(parties=parties, items=items, mapping=mapping, redacted=redacted)


# ---------------------------------------------------------------- 数据库


def _body_rows(db: Session, task_id: uuid.UUID) -> list[DocNode]:
    return list(db.scalars(
        select(DocNode)
        .join(TaskFile, TaskFile.id == DocNode.file_id)
        .where(DocNode.task_id == task_id, DocNode.part == "body")
        .order_by(TaskFile.seq, DocNode.ord)
    ))


def _load_terms(db: Session) -> list[Term]:
    """启用中的已记住名称与自定义敏感词，解密到内存（18.12），不写日志。"""
    rows = db.scalars(select(RedactionTerm).where(RedactionTerm.enabled.is_(True)).order_by(RedactionTerm.created_at))
    return [Term(crypto.decrypt_text(r.term_enc), r.kind, r.category, r.is_our_party) for r in rows]


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


def _run(db: Session, task: Task, overrides: dict[str, PartyOverride] | None,
         existing: dict[str, Party]) -> RedactionResult:
    rows = _body_rows(db, task.id)
    nodes = [NodeIn(r.loc, crypto.decrypt_text(r.text_enc) if r.text_enc is not None else "", r.clause_no)
             for r in rows]
    result = compute(nodes, task.stance, terms=_load_terms(db), overrides=overrides)

    for r in rows:
        r.redacted_text = result.redacted[r.loc]
    db.execute(delete(RedactionItem).where(RedactionItem.task_id == task.id))
    db.add_all(RedactionItem(task_id=task.id, loc=i.loc, char_start=i.char_start, char_end=i.char_end,
                             category=i.category, placeholder=i.placeholder, detected_by=i.detected_by)
               for i in result.items)
    _write_map(db, task.id, result.mapping)

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


def redact_task(db: Session, task: Task) -> None:
    """parse 阶段的脱敏：写 parties、redaction_items、redaction_maps 和正文节点的 redacted_text。不提交。"""
    db.execute(delete(Party).where(Party.task_id == task.id))
    db.flush()
    _run(db, task, None, {})


def _existing_parties(db: Session, task: Task) -> dict[str, Party]:
    mapping = load_redaction_map(db, task.id)
    out = {}
    for row in db.scalars(select(Party).where(Party.task_id == task.id)):
        name = mapping.get(row.code)
        if name is not None:
            out[P.normalize(name)] = row
    return out


def apply_party_changes(db: Session, task: Task, changes: list[PartyChange]) -> None:
    """修改我方、国家、类型后重算代号、对照表和 redacted_text（PUT /parties、重新审核使用）。不提交。

    设置某一方为我方时，其他各方自动取消；有修改时要求结果恰好一方为我方，否则抛出 ValueError。
    changes 为空时只按当前设置重算（立场或来源改变后）。
    """
    existing = _existing_parties(db, task)
    by_id = {row.id: key for key, row in existing.items()}
    overrides = {key: PartyOverride(row.is_our_party, row.country, row.org_type, row.confirmed)
                 for key, row in existing.items()}
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


def build_outbound_text(db: Session, task: Task) -> str:
    """9.7 外发文本：【主体说明】+ 各正文节点一行。"""
    parties = list(db.scalars(select(Party).where(Party.task_id == task.id)))
    rows = _body_rows(db, task.id)
    return outbound.build(
        [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in parties],
        [(r.loc, r.clause_no, r.redacted_text or "") for r in rows],
    )


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
