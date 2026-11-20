"""招标文件条款清单：本地规则抽取与完整性计数（V34-B-02；V35-E-01 定位收缩）。

只抽三类：★▲ 标记、含否决字样的句子，以及正文（非合同、非技术参数章节）中“必须 / 不得”类句子（同文合并）。
技术参数不算要求；第五十一条各项是系统自己的检查，不再列为要求。
★ 与否决条款中的工期、交货期、质保期、缺陷责任期、类似业绩带参数（param_enc），由这些条款驱动本机比对。
“投标文件格式”章节整体记一行范围（作范本白名单），其中的句子不进清单。原文只在内存与加密列中出现。
"""
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException

from sqlalchemy import delete, func, select

from app.bid.compliance import normalize_term, requirement_kind, tender_performance_limits, tender_term_limits
from app.db.models import BidItem, BidProject, BidRequirement, DocNode, Task, TaskFile
from app.security import crypto

# substantive 为 ★ 条款，veto 为否决条款，两者是“严重”一档；其余为“必须 / 不得”类条款（general 等）
GROUPS = ["qualification", "substantive", "veto", "pricing", "validity", "signing", "general", "format"]
KEY_GROUPS = {"veto", "substantive"}
MARK = re.compile(r"[★▲]")
VETO = re.compile(r"否决|废标|无效投标|投标无效|作无效|视为无效|不予受理|拒收|拒绝接收")
# “必须 / 不得”类（V35-E-01）：“应当、需、请……提供”一类说明性写法不再进清单；“须知”不是要求
MUST = re.compile(r"必须|务必|不得|严禁|不允许|不接受|须(?!知)")
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
# 技术参数章节：只收 ★ 与否决句，“必须 / 不得”类的技术写法不算要求（问题 7）
_TECHNICAL = re.compile(r"技术(?:规格|参数|要求|规范|标准|条款|协议)|数据表|规格书|货物需求|采购需求|供货范围|设备清单")
_ROW = re.compile(r"^(.*R\d+)-?C\d+$")  # Word 表格单元格位置如 F1-T1-R2-C1
_KEYWORDS = [  # 顺序即优先级；否决、★ 在此之前判定
    ("pricing", re.compile(r"报价|投标价|限价|价格|总价|单价|含税|币种|控制价")),
    ("validity", re.compile(r"有效期|保证金|保函")),
    ("signing", re.compile(r"盖章|签字|签章|公章|授权委托|签名|骑缝")),
    ("qualification", re.compile(r"资质|资格|营业执照|业绩|许可证|认证|信誉|财务|社保|联合体|注册资本|失信")),
]

ARTICLE_PREFIX = "（参照《招标投标法实施条例》第五十一条）"  # V3.4 默认行的前缀；历史项目仍可能有这样的行


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
    """逐节点给出所在章节类别：format / contract / technical / body。章节标题本身归新章节。"""
    current, out = "body", []
    for n in nodes:
        title = n.text.strip()
        m = _CHAPTER.match(title) if len(title) <= 40 else None
        if m or (n.kind == "heading" and len(title) <= 40):
            name = m.group(1) if m else title
            current = ("format" if _FORMAT.search(name) else "contract" if _CONTRACT.search(name)
                       else "technical" if _TECHNICAL.search(name) else "body")
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
    return "substantive" if marked else "general"


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
    """本机能判的才记 local：多个报价、大小写；★ / 否决条款中带参数的工期、交货期、质保期、缺陷责任期、类似业绩。
    其余 ★ / 否决条款交模型补查（只做本地检查时人工核对），“必须 / 不得”类只列出、人工核对。"""
    if group == "format":
        return "local"
    if requirement_kind(text) in ("multi_total", "uppercase"):
        return "local"
    if group in KEY_GROUPS and param_kind(text):
        return "local"
    if group == "signing" or group not in KEY_GROUPS:
        return "manual"
    return policy_method("model", policy)


def policy_method(method, policy):
    return "manual" if method == "model" and policy == "local_only" else method


# ---------------------------------------------------------------- 报价方式与条款参数

_RATE_TERMS = {
    "rate": r"以费率报价|采用费率报价|报价方式[：:]?\s*费率|最高(?:投标|报价)?费率|(?:投标|报价)?费率\s*(?:不得|不)(?:高于|超过)|费率上限",
    "discount": r"以下浮率报价|采用下浮率报价|报价方式[：:]?\s*下浮率|最低(?:投标|报价)?下浮率|(?:投标|报价)?下浮率\s*(?:不得|不)低于|下浮率下限",
}
TERM_NAMES = {"construction_max": "工期", "delivery_max": "交货期", "warranty_min": "质保期", "defect_min": "缺陷责任期"}
PERFORMANCE_KEYS = ("performance_count", "performance_amount", "performance_from", "performance_until")


def quote_mode(nodes) -> str:
    """招标文件正文只写了一种费率 / 下浮率报价方式时按它，否则按金额报价（C-04）。"""
    found = {mode for n, section in zip(nodes, _sections(nodes)) if section == "body"
             for mode, pattern in _RATE_TERMS.items() if re.search(pattern, n.text)}
    return found.pop() if len(found) == 1 else "fixed"


def param_kind(text):
    """条款对应的可比参数：工期类四项之一或 performance（类似业绩）；对不上返回 None。"""
    kind = requirement_kind(text)
    return kind if kind in TERM_NAMES or kind == "performance" else None


def default_param(text):
    """从条款文字预填参数；认不出的值留空，由用户在条款抽屉中填写。"""
    kind = param_kind(text)
    if kind is None:
        return None
    if kind == "performance":
        found = dict(tender_performance_limits(text))
        return {key: found.get(key) for key in PERFORMANCE_KEYS}
    values = [value for key, value in tender_term_limits(text) if key == kind]
    return {kind: values[0] if len(set(values)) == 1 else None}


def requirement_param(row, text):
    """★ / 否决条款的参数：用户改过的（param_enc）优先，否则按文字预填。"""
    if row.group not in KEY_GROUPS:
        return None
    if row.param_enc is not None:
        return json.loads(crypto.decrypt_text(row.param_enc))
    return default_param(text)


def clean_param(kind, raw):
    """校验并规范用户填写的参数；不合法时抛 422。"""
    from app.security.errors import AppError
    bad = AppError(422, "INVALID_PARAM", "参数格式不正确")
    if kind in TERM_NAMES:
        value = (raw or {}).get(kind)
        if value in (None, ""):
            return {kind: None}
        term = normalize_term(str(value))
        if term is None:
            raise AppError(422, "INVALID_PARAM", f"{TERM_NAMES[kind]}请写成“90天”“12个月”这样的形式")
        return {kind: f"{term[0]}{term[1]}"}
    out = {}
    for key in PERFORMANCE_KEYS:
        value = (raw or {}).get(key)
        if value in (None, ""):
            out[key] = None
            continue
        try:
            if key == "performance_count":
                out[key] = int(value)
                if not 1 <= out[key] <= 999:
                    raise ValueError
            elif key == "performance_amount":
                amount = Decimal(str(value).replace(",", ""))
                if amount <= 0:
                    raise ValueError
                out[key] = str(amount.quantize(Decimal("0.01")))
            else:
                out[key] = date.fromisoformat(str(value)).isoformat()
        except (ValueError, DecimalException):
            raise bad from None
    if out["performance_from"] and out["performance_until"] and out["performance_from"] > out["performance_until"]:
        raise AppError(422, "INVALID_PARAM", "签订日期的起点不能晚于终点")
    return out


def key_params(db, task_id):
    """检查用：启用的、本机判断的 ★ / 否决条款中带参数的 → [(条款, 参数类别, 参数)]。"""
    out = []
    for row in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task_id, BidRequirement.enabled.is_(True),
                                                      BidRequirement.method == "local", BidRequirement.group.in_(KEY_GROUPS))
                          .order_by(BidRequirement.seq)):
        text = crypto.decrypt_text(row.text_enc)
        kind = param_kind(text)
        if kind:
            out.append((row, kind, requirement_param(row, text) or {}))
    return out


# ---------------------------------------------------------------- 存取

def load_state(project) -> dict:
    """parameters_enc：{"quote_mode", "confirmed", "extracted_from", "invitees", "invitees_edited"}。
    V3.4 的关键参数（values、sources）已撤下（V35-E-01），旧项目读到时只取其中的报价方式。"""
    if project.parameters_enc is None:
        return {"quote_mode": "fixed", "confirmed": False, "extracted_from": None}
    state = json.loads(crypto.decrypt_text(project.parameters_enc))
    old = state.pop("values", None) or {}
    state.pop("sources", None)
    state.setdefault("quote_mode", old.get("quote_mode", "fixed"))
    return state


def save_state(project, state):
    project.parameters_enc = crypto.encrypt_text(json.dumps(state, ensure_ascii=False))


def parsed_tender_ids(db, task_id):
    return sorted(str(i) for i in db.scalars(select(TaskFile.id).where(
        TaskFile.task_id == task_id, TaskFile.bidder_id.is_(None), TaskFile.parser_version.is_not(None))))


def _add(db, task_id, seq, group, text, locs, method, source, enabled=True, redacted=None):
    db.add(BidRequirement(task_id=task_id, group=group, text_enc=crypto.encrypt_text(text), redacted_text=redacted,
                          locs=locs, method=method, enabled=enabled, source=source, seq=seq))


def save_param(row, value):
    row.param_enc = crypto.encrypt_text(json.dumps(value, ensure_ascii=False)) if value is not None else None


def next_seq(db, task_id):
    return (db.scalar(select(func.max(BidRequirement.seq)).where(BidRequirement.task_id == task_id)) or 0) + 1


def delete_rows(db, *conditions):
    ids = select(BidRequirement.id).where(*conditions)
    db.execute(delete(BidItem).where(BidItem.requirement_id.in_(ids)))
    db.execute(delete(BidRequirement).where(*conditions))


def extract(db, task: Task) -> None:
    """（重新）规则抽取：保留手工加入的行，其余重建；报价方式按招标文件重新判断。不提交。"""
    project = db.get(BidProject, task.id)
    nodes = tender_nodes(db, task.id)
    delete_rows(db, BidRequirement.task_id == task.id, BidRequirement.source != "manual")
    db.flush()
    seq = 1
    for hit in rule_hits(nodes):
        _add(db, task.id, seq, hit.group, hit.text, hit.locs, default_method(hit.group, hit.text, task.policy), "marked")
        seq += 1
    state = load_state(project)
    state["quote_mode"] = quote_mode(nodes)
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
    """清单页数据：按顺序的条款（★ / 否决条款带参数）、完整性计数与差额、模型补充状态。"""
    project = db.get(BidProject, task.id)
    nodes = tender_nodes(db, task.id)
    names = {str(f.id): f.original_name for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id,
                                                                                    TaskFile.bidder_id.is_(None)))}
    rows = []
    for r in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id).order_by(BidRequirement.seq)):
        text = crypto.decrypt_text(r.text_enc)
        kind = param_kind(text) if r.group in KEY_GROUPS else None
        rows.append({"id": r.id, "group": r.group, "text": text, "locs": r.locs, "method": r.method,
                     "enabled": r.enabled, "source": r.source, "seq": r.seq,
                     "param_kind": kind, "param": requirement_param(r, text) if kind else None,
                     "file": names.get(r.locs[0]["file_id"]) if r.locs else None})
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
            "quote_mode": state.get("quote_mode", "fixed"),
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
