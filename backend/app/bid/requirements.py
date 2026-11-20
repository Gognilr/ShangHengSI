"""招标文件要求清单：本地规则抽取、关键参数预填与完整性计数（V34-B-02，bid.md 2.2）。

规则只抓“看得见的标记”：★▲ 标记、含否决字样的句子，以及正文中带“必须 / 须 / 应当 / 请……提供”等指令词的句子（同文合并）；
“投标文件格式”章节整体记一行范围（作范本白名单），其中的句子不进清单。原文只在内存与加密列中出现。
"""
import json
import re
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DecimalException

from sqlalchemy import delete, func, select

from app.bid.compliance import TERM_LABELS, tender_performance_limits, tender_term_limits
from app.db.models import BidItem, BidProject, BidRequirement, DocNode, Task, TaskFile
from app.redaction.detect import chinese_upper_value
from app.security import crypto

GROUPS = ["qualification", "substantive", "veto", "pricing", "validity", "signing", "format"]
MARK = re.compile(r"[★▲]")
VETO = re.compile(r"否决|废标|无效投标|投标无效|作无效|视为无效|不予受理|拒收|拒绝接收")
# 指令词（V34-B-08c）：“须 / 需 / 请……提供、注明 / 应在、应为”等；“须知、无需、所需、需求、需要量”等不是要求
MUST = re.compile(r"必须|务必|不得|应当|严禁|不允许|不接受|须(?!知)|(?<![所必急无])需(?!求|要量|要台数)"
                  r"|应(?=[在为按落符满提包具采选设填注附由向予])|请[^。；;]{0,10}?(?:提供|注明|明确|列明|提交|写明|附上)")
_SENTENCE = re.compile(r"[^。；;！!？?\n]+[。；;！!？?]?")
# 句中的编号条目（“……批准 2、卖方应……”）另起一句；“附件1、2”这类并列编号不拆
_ENUM = re.compile(r"(?<=[^\d\s])\s*(?=\d{1,2}、[^\d\s])|\s+(?=\d{1,2}[、．][^\d\s])")
_ENUM_HEAD = re.compile(r"^\d{1,2}[、．]")
_PAREN = re.compile(r"^[（(][^（）()]*[）)]$")
# 中标后的付款、结算、验收、质保条款不作投标要求
_AFTER_AWARD = re.compile(r"^\s*(?:付款方式|付款条件|结算方式|结算|验收|质保金|质量保证金)\s*[:：]")
_CHAPTER = re.compile(r"^\s*(?:第[一二三四五六七八九十百零\d]+[章部分篇卷]|[一二三四五六七八九十]+、)\s*(.{1,30})$")
_FORMAT = re.compile(r"投标文件(?:的)?(?:组成及)?格式|响应文件格式|投标文件格式")
_CONTRACT = re.compile(r"合同")
_ROW = re.compile(r"^(.*R\d+)-?C\d+$")  # Word 表格单元格位置如 F1-T1-R2-C1
_KEYWORDS = [  # 顺序即优先级；否决、★ 在此之前判定
    ("pricing", re.compile(r"报价|投标价|限价|价格|总价|单价|含税|币种|控制价")),
    ("validity", re.compile(r"有效期|保证金|保函")),
    ("signing", re.compile(r"盖章|签字|签章|公章|授权委托|签名|骑缝")),
    ("qualification", re.compile(r"资质|资格|营业执照|业绩|许可证|认证|信誉|财务|社保|联合体|注册资本|失信")),
]

# 第五十一条否决情形（参照依据；最终以招标文件约定为准）。method 中的 model 在“只做本地检查”时改人工核对。
ARTICLE_51 = [
    ("signing", "manual", "投标文件未经投标单位盖章和单位负责人签字"),
    ("qualification", "manual", "投标联合体没有提交共同投标协议"),
    ("qualification", "model", "投标人不符合国家或者招标文件规定的资格条件"),
    ("pricing", "local", "同一投标人提交两个以上不同的投标文件或者投标报价（招标文件要求提交备选投标的除外）"),
    ("pricing", "local", "投标报价低于成本或者高于招标文件设定的最高投标限价"),
    ("substantive", "model", "投标文件没有对招标文件的实质性要求和条件作出响应"),
    ("veto", "manual", "投标人有串通投标、弄虚作假、行贿等违法行为"),
]
ARTICLE_PREFIX = "（参照《招标投标法实施条例》第五十一条）"


@dataclass
class Node:
    file_id: str
    loc: str
    kind: str
    text: str


@dataclass
class Hit:
    group: str
    text: str
    locs: list
    marked: bool
    veto: bool


def tender_nodes(db, task_id) -> list[Node]:
    rows = db.execute(select(DocNode.file_id, DocNode.loc, DocNode.kind, DocNode.text_enc)
                      .join(TaskFile, TaskFile.id == DocNode.file_id)
                      .where(DocNode.task_id == task_id, DocNode.part == "body", TaskFile.bidder_id.is_(None),
                             TaskFile.parser_version.is_not(None))
                      .order_by(TaskFile.seq, DocNode.ord))
    return [Node(str(f), loc, kind, crypto.decrypt_text(enc) if enc is not None else "") for f, loc, kind, enc in rows]


def _sections(nodes):
    """逐节点给出所在章节类别：format / contract / body。章节标题本身归新章节。"""
    current, out = "body", []
    for n in nodes:
        title = n.text.strip()
        m = _CHAPTER.match(title) if len(title) <= 40 else None
        if m or (n.kind == "heading" and len(title) <= 40):
            name = m.group(1) if m else title
            current = "format" if _FORMAT.search(name) else "contract" if _CONTRACT.search(name) else "body"
        out.append(current)
    return out


def _sentences(text):
    for m in _SENTENCE.finditer(text):
        cuts = [m.start()] + [m.start() + x.end() for x in _ENUM.finditer(m.group()) if x.end() > 0] + [m.end()]
        for start, end in zip(cuts, cuts[1:]):
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if end > start:
                yield start, end


def _dedupe_key(text):
    """同文去重的比较键：去编号、空白与标点。"""
    return re.sub(r"[\W_]+", "", _ENUM_HEAD.sub("", text.strip()))


def _group(sentence, marked, veto):
    if veto:
        return "veto"
    if not marked:
        for name, pattern in _KEYWORDS:
            if pattern.search(sentence):
                return name
    return "substantive"


def _hit_text(piece):
    text = MARK.sub('', piece).strip()
    note = re.search(r'注\s*[:：]\s*', text)
    if note:
        header = text[:note.start()]
        # 扫描数据表将编校/会签栏与注释粘成一块时，不把栏头日期变成要求。
        if (re.search(r'编\s*制|会签', header) and re.search(r'校\s*对|审\s*核', header)
                and not re.search(r'应|须|需|不得|不允许', header)):
            text = text[note.end():].strip()
    return text or piece


def markers(nodes, sections=None):
    """原文中的 ★ 句与否决句（格式章节除外）：[(kind, node, start, end)]，kind 为 star / veto。"""
    sections = sections or _sections(nodes)
    out = []
    for n, sec in zip(nodes, sections):
        if sec == "format":
            continue
        for s, e in _sentences(n.text):
            piece = n.text[s:e]
            if MARK.search(piece):
                out.append(("star", n, s, e))
            if VETO.search(piece):
                out.append(("veto", n, s, e))
    return out


def rule_hits(nodes) -> list[Hit]:
    sections = _sections(nodes)
    hits, rows_done = [], set()
    by_row = {}
    for n in nodes:
        m = _ROW.match(n.loc) if n.kind == "table_cell" else None
        if m:
            by_row.setdefault(m.group(1), []).append(n)
    star_rows = {k for k, cells in by_row.items() if any(MARK.search(c.text) for c in cells)}
    fmt = [n for n, sec in zip(nodes, sections) if sec == "format"]
    for n, sec in zip(nodes, sections):
        if sec == "format":
            continue
        row = _ROW.match(n.loc) if n.kind == "table_cell" else None
        if row and row.group(1) in star_rows:
            # 表格中的 ★：整行作一条要求（★ 常单独占一格）
            key = row.group(1)
            if key in rows_done:
                continue
            rows_done.add(key)
            cells = by_row[key]
            text = " ｜ ".join(c.text.strip() for c in cells if c.text.strip())
            hits.append(Hit(_group(text, True, bool(VETO.search(text))), _hit_text(text).strip(" ｜"),
                            [{"file_id": c.file_id, "loc": c.loc, "start": 0, "end": len(c.text)} for c in cells],
                            True, bool(VETO.search(text))))
            continue
        spans = list(_sentences(n.text))
        after_award = bool(_AFTER_AWARD.match(n.text))
        flags = []
        for s, e in spans:
            piece = n.text[s:e]
            marked, veto = bool(MARK.search(piece)), bool(VETO.search(piece))
            must = sec == "body" and not after_award and len(piece) >= 8 and bool(MUST.search(piece))
            flags.append([marked, veto, must])
        # 编号注释中已有两条以上指令句时，其余编号条目（“法兰连接标准为……”）也是要求
        if sum(f[2] for f in flags) >= 2:
            for (s, e), f in zip(spans, flags):
                if _ENUM_HEAD.match(n.text[s:e]) and e - s >= 8:
                    f[2] = True
        node_hits = []
        for (s, e), (marked, veto, must) in zip(spans, flags):
            if not (marked or veto or must):
                # 紧随要求句的括号补充（“（注明发票税率）”）并入上一条
                if node_hits and _PAREN.match(n.text[s:e]) and not n.text[node_hits[-1][1]:s].strip():
                    node_hits[-1][1] = e
                continue
            node_hits.append([s, e, marked, veto])
        for s, e, marked, veto in node_hits:
            piece = n.text[s:e]
            hits.append(Hit(_group(piece, marked, veto), _hit_text(piece), [{"file_id": n.file_id, "loc": n.loc, "start": s, "end": e}],
                            marked, veto))
    hits = _merge_same(hits)
    if fmt:
        hits.append(Hit("format", "“投标文件格式”章节：各家照此填写的格式页文字作为范本，不计入相似比对",
                        [{"file_id": fmt[0].file_id, "loc": fmt[0].loc, "start": 0, "end": len(fmt[0].text)},
                         {"file_id": fmt[-1].file_id, "loc": fmt[-1].loc, "start": 0, "end": len(fmt[-1].text)}],
                        False, False))
    return hits


def _merge_same(hits):
    """文字相同的要求只留一条，出处合并（V34-B-08c：4 份数据表的同一注释曾成 8 条）。"""
    out, seen = [], {}
    for h in hits:
        key = _dedupe_key(h.text)
        if key and key in seen:
            first = seen[key]
            first.locs += [loc for loc in h.locs if loc not in first.locs]
            continue
        seen[key] = h
        out.append(h)
    return out


def default_method(group, text, policy):
    if group == "format":
        return "local"
    if group == "signing":
        return "manual"
    if group in ("pricing", "veto") and re.search(r"限价|控制价|大写|小写|两个以上", text):
        return "local"  # 超限价、大小写、多个报价都能本地算
    if group == "validity" and re.search(r"有效期|保证金", text) and re.search(r"\d|金额|天|日", text):
        return "local"
    terms = list(tender_term_limits(text))
    if len(terms) == 1 and not re.search(r"[，,；;\n]|[。](?!\s*$)", text):
        return "local"
    return policy_method("model", policy)


def policy_method(method, policy):
    return "manual" if method == "model" and policy == "local_only" else method


# ---------------------------------------------------------------- 关键参数

_UNITS = {"亿元": Decimal("100000000"), "万元": Decimal("10000"), "元": Decimal("1")}
_CURRENCY = {"人民币": "CNY", "美元": "USD", "欧元": "EUR", "港币": "HKD", "港元": "HKD"}
_AMOUNT = r"(人民币|美元|欧元|港币|港元)?\s*[（(]?(?:小写)?[)）]?\s*[:：]?\s*[¥￥]?\s*([0-9][0-9,，]*(?:\.\d+)?)\s*(亿元|万元|元)"
_CEILING = re.compile(r"(?:最高投标限价|最高限价|招标控制价|拦标价|最高报价)[^。；\n]{0,24}?" + _AMOUNT)
_RATE_TERMS = {
    "rate": (r"以费率报价|采用费率报价|报价方式[：:]?\s*费率", r"最高(?:投标|报价)?费率|(?:投标|报价)?费率\s*(?:不得|不)(?:高于|超过)|费率上限"),
    "discount": (r"以下浮率报价|采用下浮率报价|报价方式[：:]?\s*下浮率", r"最低(?:投标|报价)?下浮率|(?:投标|报价)?下浮率\s*(?:不得|不)低于|下浮率下限"),
}
_VALIDITY = re.compile(r"投标有效期[^。；\n]{0,24}?(\d{1,4})\s*(?:个)?(?:日历天|天|日)")
_DEPOSIT = re.compile(r"(?:投标|招标)保证金[^。；\n]{0,30}?" + _AMOUNT)
_DEPOSIT_UPPER = re.compile(r"(?:投标|招标)保证金[^。；\n\d]{0,12}?(?:人民币)?[:：]?\s*"
                            r"([零壹贰叁肆伍陆柒捌玖拾佰仟万亿]+[元圆][零壹贰叁肆伍陆柒捌玖角分整正]*)")
_FORMS = ["银行保函", "保险保函", "担保保函", "电汇", "转账", "银行汇票", "支票", "现金"]
_EMPTY = {"ceiling": None, "quote_mode": "fixed", "rate_limit": None, "currency": "CNY", "validity_days": None, "deposit": None, "deposit_forms": "",
          "consortium": None, "tax_included": None, **dict.fromkeys(TERM_LABELS),
          "performance_from": None, "performance_until": None, "performance_amount": None, "performance_count": None}


def _money(number, unit):
    try:
        value = Decimal(number.replace(",", "").replace("，", "")) * _UNITS[unit]
        return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except DecimalException:
        return None


def prefill(nodes) -> tuple[dict, dict]:
    """返回 (参数, 出处)；出处为 {字段: {file_id, loc}}。只取第一处明确写法，认不出的留空由用户填写。"""
    values, sources = dict(_EMPTY), {}
    forms = []
    from app.bid.pricing import rate_value
    terms, quote_modes, rate_limits = {}, {}, {}
    for n, section in zip(nodes, _sections(nodes)):
        t = n.text
        here = {"file_id": n.file_id, "loc": n.loc}
        if section == "body":
            for key, value in (*tender_term_limits(t), *tender_performance_limits(t)):
                terms.setdefault(key, {}).setdefault(value, here)
            for mode, (mode_pattern, limit_pattern) in _RATE_TERMS.items():
                if re.search(mode_pattern, t):
                    quote_modes.setdefault(mode, here)
                for match in re.finditer(rf"(?:{limit_pattern})\s*[:：为]?\s*(\d{{1,3}}(?:\.\d{{1,8}})?\s*[%％])", t):
                    if (limit := rate_value(match[1])) is not None:
                        quote_modes.setdefault(mode, here)
                        rate_limits.setdefault(mode, {}).setdefault(limit, here)
        if values["ceiling"] is None and (m := _CEILING.search(t)):
            values["ceiling"] = _money(m.group(2), m.group(3))
            values["currency"] = _CURRENCY.get(m.group(1) or "人民币", "CNY")
            sources["ceiling"] = here
        if values["validity_days"] is None and (m := _VALIDITY.search(t)):
            values["validity_days"] = int(m.group(1))
            sources["validity_days"] = here
        if values["deposit"] is None and (m := _DEPOSIT.search(t)):
            values["deposit"] = _money(m.group(2), m.group(3))
            sources["deposit"] = here
        elif values["deposit"] is None and (m := _DEPOSIT_UPPER.search(t)) and (v := chinese_upper_value(m.group(1))):
            values["deposit"] = str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))  # “贰万元整”
            sources["deposit"] = here
        if "保证金" in t:
            forms += [f for f in _FORMS if f in t and f not in forms]
        if values["consortium"] is None:
            if re.search(r"(?:不接受|不允许|不得以)联合体", t):
                values["consortium"], sources["consortium"] = False, here
            elif re.search(r"(?:接受|允许)联合体", t):
                values["consortium"], sources["consortium"] = True, here
        if values["tax_included"] is None:
            net, gross = re.search(r"不含(?:增值)?税", t), re.search(r"(?<!不)含(?:增值)?税", t)
            if net and not gross:
                values["tax_included"], sources["tax_included"] = False, here
            elif gross and not net:
                values["tax_included"], sources["tax_included"] = True, here
    values["deposit_forms"] = "、".join(forms)
    for key, candidates in terms.items():
        if len(candidates) == 1:
            values[key], sources[key] = next(iter(candidates.items()))
    if len(quote_modes) == 1:
        mode, source = next(iter(quote_modes.items()))
        values["quote_mode"], sources["quote_mode"] = mode, source
        limits = rate_limits.get(mode, {})
        if len(limits) == 1:
            limit, source = next(iter(limits.items()))
            values["rate_limit"], sources["rate_limit"] = format(limit, "f"), source
    start, end = values["performance_from"], values["performance_until"]
    if start and end and start > end:
        for key in ("performance_from", "performance_until"):
            values[key] = None
            sources.pop(key)
    return values, sources


# ---------------------------------------------------------------- 存取

def load_state(project) -> dict:
    """parameters_enc：{"values", "sources", "confirmed", "extracted_from", "invitees", "invitees_edited"}。"""
    if project.parameters_enc is None:
        return {"values": dict(_EMPTY), "sources": {}, "confirmed": False, "extracted_from": None}
    state = json.loads(crypto.decrypt_text(project.parameters_enc))
    state["values"] = {"quote_mode": "fixed", "rate_limit": None, **state["values"]}
    return state


def save_state(project, state):
    project.parameters_enc = crypto.encrypt_text(json.dumps(state, ensure_ascii=False))


def parsed_tender_ids(db, task_id):
    return sorted(str(i) for i in db.scalars(select(TaskFile.id).where(
        TaskFile.task_id == task_id, TaskFile.bidder_id.is_(None), TaskFile.parser_version.is_not(None))))


def _add(db, task_id, seq, group, text, locs, method, source, enabled=True, redacted=None):
    db.add(BidRequirement(task_id=task_id, group=group, text_enc=crypto.encrypt_text(text), redacted_text=redacted,
                          locs=locs, method=method, enabled=enabled, source=source, seq=seq))


def next_seq(db, task_id):
    return (db.scalar(select(func.max(BidRequirement.seq)).where(BidRequirement.task_id == task_id)) or 0) + 1


def delete_rows(db, *conditions):
    ids = select(BidRequirement.id).where(*conditions)
    db.execute(delete(BidItem).where(BidItem.requirement_id.in_(ids)))
    db.execute(delete(BidRequirement).where(*conditions))


def extract(db, task: Task) -> None:
    """（重新）规则抽取：保留手工加入的行，其余重建；关键参数只填用户尚未填写的项。不提交。"""
    project = db.get(BidProject, task.id)
    nodes = tender_nodes(db, task.id)
    delete_rows(db, BidRequirement.task_id == task.id, BidRequirement.source != "manual")
    db.flush()
    seq = 1
    for hit in rule_hits(nodes):
        _add(db, task.id, seq, hit.group, hit.text, hit.locs, default_method(hit.group, hit.text, task.policy), "marked")
        seq += 1
    state = load_state(project)
    values, sources = prefill(nodes)
    if not state["confirmed"]:
        for key in ("quote_mode", "rate_limit"):
            state["values"][key] = values[key]
            if key in sources:
                state["sources"][key] = sources[key]
            else:
                state["sources"].pop(key, None)
    for key, value in values.items():
        if key not in ("quote_mode", "rate_limit") and state["values"].get(key) in (None, "") and value not in (None, ""):
            state["values"][key] = value
            if key in sources:
                state["sources"][key] = sources[key]
    for group, method, text in ARTICLE_51:
        method = policy_method(method, task.policy)
        enabled = not (text.startswith("投标联合体") and state["values"].get("consortium") is False)
        _add(db, task.id, seq, group, ARTICLE_PREFIX + text, [], method, "default", enabled)
        seq += 1
    # 手工行排到最后，保持原有相对顺序
    for row in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id, BidRequirement.source == "manual")
                          .order_by(BidRequirement.seq)):
        row.seq = seq
        row.method = policy_method(row.method, task.policy)
        seq += 1
    from app.bid import invitees
    invitees.refresh(db, task.id, state)
    state["extracted_from"] = parsed_tender_ids(db, task.id)
    save_state(project, state)
    project.requirements_version += 1
    if project.extract_state not in ("redacting", "extracting"):
        project.extract_state, project.extract_error = None, None


def _covered(marker, rows):
    _, node, s, e = marker
    for r in rows:
        for loc in r["locs"]:
            if loc.get("loc") == node.loc and loc.get("start", 0) < e and loc.get("end", len(node.text)) > s:
                return True
    return False


def view(db, task: Task) -> dict:
    """清单页数据：参数、按顺序的要求、完整性计数与差额、模型补充状态。"""
    project = db.get(BidProject, task.id)
    nodes = tender_nodes(db, task.id)
    names = {str(f.id): f.original_name for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id,
                                                                                    TaskFile.bidder_id.is_(None)))}
    rows = [{"id": r.id, "group": r.group, "text": crypto.decrypt_text(r.text_enc), "locs": r.locs, "method": r.method,
             "enabled": r.enabled, "source": r.source, "seq": r.seq,
             "file": names.get(r.locs[0]["file_id"]) if r.locs else None}
            for r in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id).order_by(BidRequirement.seq))]
    enabled = [r for r in rows if r["enabled"]]
    counts = {}
    for kind in ("star", "veto"):
        found = [m for m in markers(nodes) if m[0] == kind]
        missing = [m for m in found if not _covered(m, enabled)]
        counts[kind] = {"total": len(found), "covered": len(found) - len(missing),
                        "missing": [{"file_id": m[1].file_id, "loc": m[1].loc, "start": m[2], "end": m[3],
                                     "text": m[1].text[m[2]:m[3]]} for m in missing[:50]]}
    from app.bid.results import sent_count
    state = load_state(project)
    from app.bid import invitees
    return {"invitees": invitees.view(state), "invitees_edited": bool(state.get("invitees_edited")),
            "parameters": state["values"], "parameter_sources": state["sources"], "parameters_confirmed": state["confirmed"],
            "extracted": state["extracted_from"] is not None,
            "tender_changed": state["extracted_from"] is not None and state["extracted_from"] != parsed_tender_ids(db, task.id),
            "items": rows, "counts": counts, "version": project.requirements_version,
            "extract_state": project.extract_state, "extract_error": project.extract_error,
            "sent_count": sent_count(db, task.id)}


def requirement(db, task, raw):
    from app.security.errors import NotFoundError
    try:
        row = db.get(BidRequirement, uuid.UUID(str(raw)))
    except ValueError:
        row = None
    if row is None or row.task_id != task.id:
        raise NotFoundError("REQUIREMENT_NOT_FOUND", "要求不存在")
    return row
