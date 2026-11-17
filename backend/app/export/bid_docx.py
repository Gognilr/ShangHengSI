"""标书初审 Word 报告（bid.md 2.5）：只读已落库的结果，在内存中生成，服务器不留存。

结论一律写“疑似”“线索”“未找到”；“已排除”的条目不进正文；疑似否决项只有人工点过“已核实”才去掉“疑似”口吻。
投标人用真实名称（库里本来就是本机原文）。不读节点原文、不写密码。
"""
import io
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Cm, Pt
from sqlalchemy import select

from app.bid import requirements as R, results
from app.db.models import BidBidder, BidProject, BidRequirement, LlmCall, Task, TaskFile, User
from app.export.report_docx import _clean, _font, _heading, _new_document, _table
from app.security import crypto

DECLARATION = "本报告为初审线索，不构成对串通投标或否决投标的认定，由评标委员会核实后决定。"
ART_CLUE = "《招标投标法实施条例》第四十条"
ART_VETO = "《招标投标法实施条例》第五十一条"
LEVEL = {"veto": "疑似否决项", "general": "一般问题", "hint": "提示", "strong": "强", "medium": "中", "weak": "弱"}
STATUS = {"pending": "待核对", "verified": "已核实", "excluded": "已排除"}
VERDICT = {"yes": "响应", "no": "疑似不响应", "missing": "未找到", "unclear": "无法判断", "manual": "人工核对"}
GROUP = {"qualification": "资格条件", "substantive": "实质性条款（★）", "veto": "否决条款", "pricing": "报价要求",
         "validity": "有效期与保证金", "signing": "签章与格式页", "format": "投标文件格式（范本）"}
GROUP_ORDER = {key: i for i, key in enumerate(("veto", "substantive", "qualification", "pricing", "validity", "signing", "format"))}
METHOD = {"local": "本地规则", "model": "模型判断", "manual": "人工核对"}
SOURCE = {"marked": "原文标记", "model": "模型提取", "default": "默认（第五十一条）", "manual": "手工"}
SIGN = {"stamped": "已盖章", "missing": "缺章", "unclear": "看不清", None: "未标注"}
TYPE = {"mixed": "混装", "contact": "联系信息相同", "props": "文件属性相同", "image": "相同图片", "similar": "相似段落", "pricing": "报价规律",
        "multi_total": "多个总价", "sum_total": "合计与总价", "ceiling": "超最高限价", "low_price": "异常低价", "uppercase": "大小写", "row_calc": "分项计算",
        "sum_rows": "合计计算", "validity": "投标有效期", "deposit": "保证金", "cert_expired": "证书有效期",
        "ai_instruction": "写给 AI 的指令", "model_response": "模型判断", "deviation_conflict": "偏离表与正文"}
SECTION = {"tech": "技术参数章节，已降一档", "price": "报价章节，已升一档"}
MAX_TEXT = 600       # 并排对照每侧最多摘录的字数
MAX_PRICE_ROWS = 60  # 分项对齐表最多列出的行数
TOTAL_W = 26.7       # 横向 A4 可用宽度（cm）


# ---------------------------------------------------------------- 小工具

def _plain(value) -> str:
    return _clean(value) if value not in (None, "") else "—"


def _para(doc, text, bold=False, size=10.5, indent=None):
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.left_indent = Pt(indent)
    _font(p.add_run(_clean(text)), size, bold)
    return p


def _sub(doc, title):
    p = doc.add_paragraph()
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_before = Pt(6)
    _font(p.add_run(_clean(title)), 10.5, True)


def _widths(first, count, rest=TOTAL_W):
    """首列固定，其余列平分剩余宽度。"""
    each = round((rest - first) / max(count, 1), 2)
    return [first] + [each] * count


def _lines(*lines):
    """多行文字：每行是 [(文字, 是否高亮)]，行间换行。"""
    out = []
    for line in lines:
        if not line:
            continue
        if out:
            out.append(("\n", False))
        out.extend(line if isinstance(line, list) else [(_clean(line), False)])
    return out or [("—", False)]


def same_spans(text: str, other: str, n: int = 8) -> list[tuple[str, bool]]:
    """两段文字中相同的部分（连续 n 字以上）；与界面并排对照的高亮口径一致。"""
    if len(text) < n or len(other) < n:
        return [(text, False)]
    grams = {other[i:i + n] for i in range(len(other) - n + 1)}
    hit = [False] * len(text)
    for i in range(len(text) - n + 1):
        if text[i:i + n] in grams:
            for j in range(i, i + n):
                hit[j] = True
    out, i = [], 0
    while i < len(text):
        j = i
        while j < len(text) and hit[j] == hit[i]:
            j += 1
        out.append((text[i:j], hit[i]))
        i = j
    return out


def _clip(text, n=MAX_TEXT):
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


def _article(item) -> str:
    a = item.get("article") or ""
    if not a:
        return ""
    if item["kind"] == "clue":
        return f"{ART_CLUE}{a}"
    return f"参照{ART_VETO}{a}" if not a.startswith("第") else f"参照《招标投标法实施条例》{a}"


def _headline(item) -> str:
    """【类型】标题；标题已以类型名开头时不重复。"""
    label, title = TYPE.get(item["type"], item["type"]), _title(item)
    return title if title.startswith(label) else f"【{label}】{title}"


def _level_text(item) -> str:
    if item["kind"] == "clue":
        return f"线索·{LEVEL.get(item['level'], item['level'])}"
    return LEVEL.get(item["level"], item["level"])


def _title(item) -> str:
    """疑似否决项在人工核实前保持“疑似”口吻；核实后写明“经人工核实”。"""
    title = item["title"]
    if item["level"] != "veto" or item["kind"] != "issue":
        return title
    if item["review_status"] == "verified":
        return f"经人工核实：{title}"
    return title if title.startswith("疑似") else f"疑似：{title}"


# ---------------------------------------------------------------- 数据

class _Data:
    def __init__(self, db, task: Task, user: User):
        self.db, self.task, self.user = db, task, user
        self.project = db.get(BidProject, task.id)
        self.bidders = list(db.scalars(select(BidBidder).where(BidBidder.task_id == task.id).order_by(BidBidder.seq)))
        self.names = {str(b.id): crypto.decrypt_text(b.name_enc) for b in self.bidders}
        self.files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
        self.file_names = {str(f.id): f.original_name for f in self.files}
        self.view = results.check_view(db, task)
        self.items = [i for i in self.view["items"] if i["review_status"] != "excluded"]
        self.excluded = sum(1 for i in self.view["items"] if i["review_status"] == "excluded" and i["kind"] != "normal_dup")
        self.issues = [i for i in self.items if i["kind"] == "issue"]
        self.clues = [i for i in self.items if i["kind"] == "clue"]
        self.normal = [i for i in self.items if i["kind"] == "normal_dup"]
        self.cells = {(str(c["requirement_id"]), str(c["bidder_id"])): c for c in self.view["cells"]}
        self.reqs = sorted(self.view["requirements"], key=lambda r: (GROUP_ORDER.get(r["group"], 9), r["seq"]))
        self.params = R.load_state(self.project)["values"]
        self.models = sorted(db.scalars(select(LlmCall.model_name).where(
            LlmCall.task_id == task.id, LlmCall.purpose == "bid", LlmCall.status == "sent").distinct()))
        self.model_rows = any(r["method"] == "model" for r in self.reqs)
        self.now = datetime.now(ZoneInfo("Asia/Shanghai"))

    def name(self, bidder_id) -> str:
        return self.names.get(str(bidder_id), "已移除的投标人")

    def who(self, item) -> str:
        return "、".join(self.name(i) for i in item["bidder_ids"])

    def place(self, p) -> str:
        return self.file_names.get(str(p.get("file_id")), "文件") + (f" 第 {p['page']} 页" if p.get("page") else "")

    def pending_for(self, bidder_id) -> int:
        """待人工核对：与结果页总览同一口径。"""
        bid = str(bidder_id)
        cells = [c for (rid, b), c in self.cells.items() if b == bid and c["review_status"] != "excluded"]
        reviewed = {str(c["requirement_id"]) for c in cells if c["review_status"] != "pending"}
        manual = [r for r in self.reqs if r["method"] == "manual" and str(r["id"]) not in reviewed]
        return (sum(1 for i in self.items if i["kind"] != "normal_dup" and i["review_status"] == "pending" and bid in map(str, i["bidder_ids"]))
                + sum(1 for c in cells if c["review_status"] == "pending" and c["verdict"] != "manual"
                      and (c["verdict"] == "unclear" or c["cite"] == "unverified"))
                + len(manual) + sum(1 for s in self.view["signing"] if str(s["bidder_id"]) == bid and not s["mark"]))

    def pending_total(self) -> int:
        shared = sum(len(i["bidder_ids"]) - 1 for i in self.items
                     if i["kind"] != "normal_dup" and i["review_status"] == "pending" and len(i["bidder_ids"]) > 1)
        return sum(self.pending_for(b.id) for b in self.bidders) - shared


def _count(items, level) -> int:
    return sum(1 for i in items if i["level"] == level)


def _clue_counts(items) -> str:
    parts = [f"{LEVEL[lv]} {n}" for lv in ("strong", "medium", "weak") if (n := _count(items, lv))]
    return "  ".join(parts) or "—"


# ---------------------------------------------------------------- 封面与方法说明

def _cover(doc, d: _Data, scope: str):
    bidders = "、".join(d.names.values()) or "—"
    tenders = "\n".join(f.original_name for f in d.files if f.bidder_id is None) or "—"
    rows = [("项目名称", d.task.title), ("开标日期", d.project.opening_date.isoformat()), ("初审人", d.user.display_name),
            ("生成时间", d.now.strftime("%Y-%m-%d %H:%M")), ("投标人", f"{len(d.bidders)} 家（按录入顺序）：{bidders}"),
            ("招标文件", tenders), ("要求清单", f"第 {d.project.requirements_version} 版，共 {len(d.reqs)} 条启用的要求"),
            ("报告范围", "全部初审内容" if scope == "full" else "只含串标线索")]
    _table(doc, ("项目", "内容"), rows, (3.5, 23.2))
    _heading(doc, "方法说明")
    _para(doc, "本报告由本机比对生成：文件属性、相同图片、联系信息、相似段落、报价规律与确定性符合项（金额、有效期、保证金、证书有效期等）均在本机计算，招标文件原文与投标文件原文不外发。")
    if d.task.policy == "local_only":
        _para(doc, "本项目选择“只做本地检查”，未调用模型；需要理解的“模型判断”类要求按人工核对处理。")
    elif d.project.model_state == "done" and d.models:
        _para(doc, f"“模型判断”类要求由 {'、'.join(d.models)} 核对：只发送检索出的相关片段（已在本机保护企业与个人信息），"
                   "投标人以代号表示，模型不知道各家名称，核对结果在本机还原。")
    elif d.model_rows:
        _para(doc, "“模型判断”类要求尚未完成模型核对，相关结果显示为“未核对”，不代表投标文件已响应。")
    _para(doc, "结论一律写“疑似”“线索”“未找到”，每条附依据；疑似否决项经人工点选“已核实”后才以核实口吻写入。已排除的条目不进正文。"
               "企业自主招标未必属依法必须招标项目，条文仅作参照依据，最终以招标文件约定为准。")
    p = _para(doc, DECLARATION, bold=True)
    p.paragraph_format.space_before = Pt(6)


# ---------------------------------------------------------------- 总览与覆盖说明

def _pair_lines(d: _Data) -> list[str]:
    pairs = []
    for i, a in enumerate(d.bidders):
        for b in d.bidders[i + 1:]:
            shared = [c for c in d.clues if str(a.id) in map(str, c["bidder_ids"]) and str(b.id) in map(str, c["bidder_ids"])]
            if shared:
                pairs.append(f"{d.name(a.id)} — {d.name(b.id)}：{_clue_counts(shared)}")
    return pairs


def _overview(doc, d: _Data):
    _heading(doc, "一 总览")
    veto = [i for i in d.issues if i["level"] == "veto"]
    verified = sum(1 for i in veto if i["review_status"] == "verified")
    _para(doc, f"疑似否决项 {len(veto)} 项（已人工核实 {verified} 项）；其他问题 {len(d.issues) - len(veto)} 项；串标线索 {len(d.clues)} 条（{_clue_counts(d.clues)}）；"
               f"待人工核对 {d.pending_total()} 项；已排除 {d.excluded} 条（不列入正文）。")
    pricing = d.view["pricing"] or {}
    has_ceiling = bool(d.params.get("ceiling"))  # 未填限价时不出现任何限价内容（V34Q-27）
    rows = []
    for b in d.bidders:
        own = [i for i in d.issues if str(b.id) in map(str, i["bidder_ids"])]
        own_veto = [i for i in own if i["level"] == "veto"]
        price = (pricing.get("bidders") or {}).get(str(b.id)) or {}
        rows.append((crypto.decrypt_text(b.name_enc),
                     f"{len(own_veto)}（已核实 {sum(1 for i in own_veto if i['review_status'] == 'verified')}）" if own_veto else "0",
                     len(own) - len(own_veto), d.pending_for(b.id), _total_text(price, mark=True),
                     *([_ceiling_text(price)] if has_ceiling else []),
                     _clue_counts([c for c in d.clues if str(b.id) in map(str, c["bidder_ids"])])))
    if has_ceiling:
        _table(doc, ("投标人（按录入顺序）", "疑似否决项", "其他问题", "待人工核对", "投标总价", "与最高限价", "涉及串标线索"), rows,
               (6, 2.8, 2.4, 2.6, 3.8, 4.0, 5.1))
    else:
        _table(doc, ("投标人（按录入顺序）", "疑似否决项", "其他问题", "待人工核对", "投标总价", "涉及串标线索"), rows,
               (7, 3, 2.6, 2.8, 5.6, 5.7))
    pairs = _pair_lines(d)
    if pairs:
        _sub(doc, "串标线索涉及的投标人两两关系")
        for line in pairs:
            _para(doc, line, indent=12)
    _coverage(doc, d)


MARK = {"highest": "最高", "lowest": "最低"}


def _total_text(price, mark=False) -> str:
    if not price:
        return "未识别"
    if price.get("total"):
        tag = f"（{MARK[price['mark']]}）" if mark and price.get("mark") in MARK else ""
        return f"{price['total']} {price.get('currency') or ''}".strip() + tag
    found = price.get("totals_found") or []
    # 多个总价：写最高的一个并注明个数（与结果页一致，V34Q-25）
    return f"{found[-1]}（{len(found)} 个总价）" if len(found) > 1 else found[0] if found else "未识别"


def _ceiling_text(price) -> str:
    over = (price or {}).get("over_ceiling")
    if over is None:
        over = (price or {}).get("over_ceiling_max")
    if over is None:
        return "—"
    return "低于限价" if over.startswith("-") else "等于限价" if over == "0.00" else f"超限价 {over}"


def _coverage(doc, d: _Data):
    _sub(doc, "覆盖说明与限制")
    notes = list(d.view["coverage"])
    if d.task.policy == "local_only":
        notes.append("本项目只做本地检查，未调用模型。")
    elif d.model_rows and d.project.model_state != "done":
        notes.append("“模型判断”类要求尚未完成模型核对，相关单元格为“未核对”。")
    elif d.project.model_state == "done":
        notes.append("模型核对只覆盖检索出的相关片段，未检索到的内容可能被遗漏；引文待核实的结论请对照原件。")
    pending = d.pending_total()
    if pending:
        notes.append(f"仍有 {pending} 项待人工核对，报告内容尚未全部核实。")
    notes.append("报告未列出的事项不代表投标文件没有问题。")
    for n in notes:
        _para(doc, "· " + n, indent=12)


# ---------------------------------------------------------------- 要求响应矩阵

def _cell_text(d: _Data, req, bidder_id) -> str:
    cell = d.cells.get((str(req["id"]), str(bidder_id)))
    if cell is None:
        return "未核对" if req["method"] == "model" else "—"
    if cell["review_status"] == "excluded":
        return "已排除"
    text = VERDICT.get(cell["verdict"], cell["verdict"])
    if cell["source"] == "model":
        text += "（模型）"
    if cell["cite"] == "unverified":
        text += "·引文待核实"
    if cell["review_status"] == "verified":
        text += "·已核实"
    return text


def _problem(d: _Data, req) -> bool:
    for b in d.bidders:
        cell = d.cells.get((str(req["id"]), str(b.id)))
        if cell and cell["review_status"] != "excluded" and (cell["verdict"] in ("no", "missing", "unclear") or cell["cite"] == "unverified"):
            return True
    return False


def _matrix_table(doc, d: _Data, reqs):
    rows = [(_clean(f"【{GROUP.get(r['group'], r['group'])}】{r['text']}"), *[_cell_text(d, r, b.id) for b in d.bidders]) for r in reqs]
    _table(doc, ("要求", *[crypto.decrypt_text(b.name_enc) for b in d.bidders]), rows, _widths(9.5, len(d.bidders)))


def _matrix(doc, d: _Data):
    _heading(doc, "二 要求响应矩阵（只列有问题的行）")
    rows = [r for r in d.reqs if _problem(d, r)]
    if not rows:
        _para(doc, "没有疑似不响应、未找到或无法判断的要求。完整矩阵见附录一。")
        return
    _para(doc, f"共 {len(rows)} 行；完整矩阵见附录一。“模型”表示由模型核对，其余为本机规则或人工核对。")
    _matrix_table(doc, d, rows)
    detail = []
    for r in rows:
        for b in d.bidders:
            cell = d.cells.get((str(r["id"]), str(b.id)))
            if cell and cell["review_status"] != "excluded" and (cell["reason"] or cell["note"]) and _problem_cell(cell):
                detail.append((_clean(_clip(r["text"], 60)), crypto.decrypt_text(b.name_enc), VERDICT.get(cell["verdict"], cell["verdict"]),
                               _lines(cell["reason"] and f"理由：{_clean(cell['reason'])}", cell["note"] and f"备注：{_clean(cell['note'])}"),
                               STATUS.get(cell["review_status"], "")))
    if detail:
        _sub(doc, "有问题的单元格说明")
        _table(doc, ("要求", "投标人", "判断", "理由与备注", "核对状态"), detail, (7, 4.5, 2.7, 10, 2.5))


def _problem_cell(cell) -> bool:
    return cell["verdict"] in ("no", "missing", "unclear") or cell["cite"] == "unverified"


# ---------------------------------------------------------------- 证据

def _evidence(d: _Data, item) -> list:
    """一条问题或线索的证据文字（不含并排对照）：每行 [(文字, 高亮)]。"""
    lines = []
    for row in item.get("rows", []):
        label, value = row.get("label", ""), row.get("value")
        head = label
        if row.get("count"):
            head += f"（{row['count']} 处）"
        if row.get("chars"):
            head += f"（约 {row['chars']} 字，{row.get('sentences', 0)} 句" + (f"；{SECTION[row['section']]}" if row.get("section") in SECTION else "") + "）"
        if row.get("sha256"):
            value = f"{(value + '，') if value else ''}SHA-256 {row['sha256'][:16]}…，{row.get('width', 0)}×{row.get('height', 0)} 像素"
        if value or row.get("count") or row.get("chars"):
            lines.append(f"{head}：{_clean(value)}" if value else head)
        for bid, file in (row.get("files") or {}).items():
            lines.append(f"  {d.name(bid)}：{_clean(file)}")
        for bid, places in (row.get("places") or {}).items():
            for p in places:
                lines.append(f"  {d.name(bid)} · {d.place(p)}" + (f"：{_clean(_clip(p['excerpt'], 160))}" if p.get("excerpt") else ""))
    return [x if isinstance(x, list) else [(x, False)] for x in lines]


def _pairs(item) -> list:
    return [(r["left"], r["right"]) for r in item.get("rows", []) if r.get("left") and r.get("right")]


def _price_rows(item) -> list:
    return [r["items"] for r in item.get("rows", []) if r.get("items")]


def _review_text(item) -> str:
    text = STATUS.get(item["review_status"], "")
    return text + (f"\n备注：{_clean(item['note'])}" if item.get("note") else "")


def _pair_table(doc, d: _Data, item, left, right):
    """两家原文并排，相同文字高亮。"""
    table = doc.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.autofit = False
    sides = (left, right)
    for cell, side in zip(table.rows[0].cells, sides):
        _font(cell.paragraphs[0].add_run(_clean(f"{d.name(side['bidder_id'])} · {'、'.join(dict.fromkeys(d.place(p) for p in side.get('places', [])))}")), 9, True)
    for cell, side, other in zip(table.rows[1].cells, sides, reversed(sides)):
        for text, hit in same_spans(_clip(side.get("text", "")), _clip(other.get("text", ""))):
            run = cell.paragraphs[0].add_run(_clean(text))
            _font(run, 9)
            if hit:
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    for row in table.rows:
        for cell in row.cells:
            cell.width = Cm(TOTAL_W / 2)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


# ---------------------------------------------------------------- 各投标人问题清单

def _issues(doc, d: _Data):
    _heading(doc, "三 各投标人问题清单")
    if not d.bidders:
        _para(doc, "没有投标人。")
    for b in d.bidders:
        _sub(doc, crypto.decrypt_text(b.name_enc))
        own = [i for i in d.issues if str(b.id) in map(str, i["bidder_ids"])]
        if not own:
            _para(doc, "本机检查与模型核对未发现问题（见覆盖说明）。", indent=12)
            continue
        rows = []
        for i in own:
            req = next((r for r in d.reqs if str(r["id"]) == str(i["requirement_id"])), None)
            basis = _lines(_article(i), req and f"招标文件要求：{_clean(_clip(req['text'], 120))}")
            rows.append((_level_text(i), _lines(_headline(i), *_evidence(d, i)),
                         basis, _review_text(i)))
        _table(doc, ("等级", "问题与证据（出处、位置、原文摘录）", "依据", "核对状态与备注"), rows, (2.6, 14.3, 6.0, 3.8))


# ---------------------------------------------------------------- 串标线索

def _clues(doc, d: _Data, number="四"):
    _heading(doc, f"{number} 串标线索（强 / 中 / 弱）")
    if not d.clues:
        _para(doc, "本机检查未发现串标线索（见覆盖说明；未发现不等于不存在）。")
    else:
        _para(doc, f"共 {len(d.clues)} 条：{_clue_counts(d.clues)}。线索只说明文件之间存在相同之处，是否构成串通投标由评标委员会核实。")
    n = 0
    for level in ("strong", "medium", "weak"):
        group = [c for c in d.clues if c["level"] == level]
        if not group:
            continue
        _sub(doc, f"{LEVEL[level]}线索 {len(group)} 条")
        for c in group:
            n += 1
            _para(doc, f"线索 {n}　{_headline(c)}", bold=True, indent=6)
            _para(doc, f"涉及：{d.who(c)}　依据：{_article(c) or '—'}　核对状态：{STATUS.get(c['review_status'], '')}"
                  + (f"　备注：{c['note']}" if c.get("note") else ""), size=9.5, indent=12)
            for line in _evidence(d, c):
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Pt(12)
                p.paragraph_format.space_after = Pt(1)
                for text, hit in line:
                    _font(p.add_run(text), 9.5)
            for left, right in _pairs(c):
                _pair_table(doc, d, c, left, right)
            for items in _price_rows(c):
                names = [d.name(i) for i in c["bidder_ids"][:2]]
                _table(doc, ("分项", *names), [(_plain(x["name"]), _plain(x["left"]), _plain(x["right"])) for x in items[:20]], (12, 7.3, 7.4))
            if c.get("normal"):
                _para(doc, f"常见的正常原因：{c['normal']}", size=9.5, indent=12)
    if d.normal:
        _sub(doc, "正常重复（招标范本、厂家资料，不计入线索）")
        for c in d.normal:
            _para(doc, f"· {d.who(c)}：{_title(c)}", size=9.5, indent=12)


# ---------------------------------------------------------------- 报价分析

def _pricing(doc, d: _Data, number="五"):
    _heading(doc, f"{number} 报价分析")
    pricing = d.view["pricing"]
    ceiling = d.params.get("ceiling")
    if not pricing:
        _para(doc, "没有可分析的报价（未识别到报价表或投标总价）。")
        return
    lead = "投标人按录入顺序排列；金额由本机计算。"
    if ceiling:
        lead += f"招标文件最高限价：{ceiling} {d.params.get('currency') or ''}。"
    if cmp := pricing.get("compare"):
        lead += (f"参加比较的 {cmp['n']} 家中，最高 {cmp['highest']}，最低 {cmp['lowest']}，平均 {cmp['mean']}"
                 + (f"，最高比最低高 {cmp['spread_pct']}" if cmp.get("spread_pct") else "") + "；多个总价的投标人不参加比较。")
    _para(doc, lead)
    rows = []
    for b in d.bidders:
        price = pricing["bidders"].get(str(b.id)) or {}
        table = price.get("table") or {}
        above = (f"{price['above_lowest']}（{price['above_lowest_pct']}）" if price.get("above_lowest")
                 else price.get("not_compared") or "—")
        rows.append((crypto.decrypt_text(b.name_enc), _total_text(price, mark=True), above,
                     price.get("vs_mean") if not price.get("not_compared") and price.get("vs_mean") else "—",
                     *([_ceiling_text(price)] if ceiling else []),
                     (f"{table.get('total') or '—'}（{table.get('rows', 0)} 行）" if table else "未识别报价表")))
    if ceiling:
        _table(doc, ("投标人", "投标总价", "比最低价高", "偏离平均", "与最高限价", "报价表合计"), rows, (6.2, 5, 4.6, 2.6, 3.8, 4.5))
    else:
        _table(doc, ("投标人", "投标总价", "比最低价高", "偏离平均", "报价表合计"), rows, (7, 5.6, 5.4, 3, 5.7))
    for b in d.bidders:
        low = (pricing["bidders"].get(str(b.id)) or {}).get("low_price")
        if low:
            _para(doc, f"· 异常低价提示：{d.name(b.id)} 的投标总价比其余 {low['others']} 家平均 {low['others_mean']} 低 {low['below_pct']}，"
                       "参照第五十一条（五），请评标委员会核实是否低于成本。", indent=12)
    for t in pricing.get("total_patterns") or []:
        how = f"从低到高每家相差 {t['step']}" if t["kind"] == "diff" else f"从低到高每家约为前一家的 {t['step']} 倍"
        _para(doc, f"· 总价规律：{'、'.join(d.name(i) for i in t['ids'])} 的投标总价{how}（第四十条（四）线索，见串标线索一节）。", indent=12)
    items = pricing.get("items") or []
    if items:
        _sub(doc, f"分项对齐表（共 {len(items)} 项" + (f"，仅列前 {MAX_PRICE_ROWS} 项" if len(items) > MAX_PRICE_ROWS else "") + "）")
        _table(doc, ("分项", *[crypto.decrypt_text(b.name_enc) for b in d.bidders]),
               [(_plain(it["name"]) + ("（招标文件给定）" if it.get("fixed") else ""), *[_plain((it["prices"] or {}).get(str(b.id))) for b in d.bidders])
                for it in items[:MAX_PRICE_ROWS]], _widths(8, len(d.bidders)))
    pairs = pricing.get("pairs") or []
    if pairs:
        _sub(doc, "两两分项单价比值")
        rows = []
        for p in pairs:
            stat = f"{p['n']} 项，平均 {p['mean_ratio']} 倍，变异系数 {p['cv']}" if p.get("n", 0) >= 5 and p.get("cv") else f"对齐 {p.get('aligned', 0)} 项，不足 5 项"
            rows.append((f"{d.name(p['a'])} — {d.name(p['b'])}", stat, p.get("label") or "—"))
        _table(doc, ("投标人", "统计", "说明"), rows, (10, 9, 7.7))


# ---------------------------------------------------------------- 附录与签字栏

def _file_props(f: TaskFile) -> str:
    if f.meta_enc is None:
        return "—"
    props = (json.loads(crypto.decrypt_text(f.meta_enc)) or {}).get("props") or {}
    parts = []
    for key, label in (("creator", "作者"), ("author", "作者"), ("last_modified_by", "最后保存者"), ("company", "公司"),
                       ("application", "软件"), ("producer", "生成软件"), ("created", "创建")):
        if props.get(key) and f"{label}：{props[key]}" not in parts:
            parts.append(f"{label}：{props[key]}")
    return _clean("；".join(parts)) if parts else "—"


def _appendix(doc, d: _Data):
    _heading(doc, "附录一 完整要求响应矩阵")
    if d.reqs:
        _matrix_table(doc, d, d.reqs)
    else:
        _para(doc, "没有启用的要求。")
    _heading(doc, "附录二 要求清单全文")
    values = d.params
    forms = f"（{values['deposit_forms']}）" if values.get("deposit_forms") else ""
    _para(doc, f"关键参数：最高限价 {_plain(values.get('ceiling'))} {values.get('currency') or ''}；投标有效期 {_plain(values.get('validity_days'))} 天；"
               f"保证金 {_plain(values.get('deposit'))}{forms}；联合体 " + {True: "接受", False: "不接受"}.get(values.get("consortium"), "未填")
          + "；报价 " + {True: "含税", False: "不含税"}.get(values.get("tax_included"), "未填") + "。")
    all_reqs = sorted(d.db.scalars(select(BidRequirement).where(BidRequirement.task_id == d.task.id)), key=lambda r: (GROUP_ORDER.get(r.group, 9), r.seq))
    _table(doc, ("分组", "要求", "检查方式", "来源", "启用"),
           [(GROUP.get(r.group, r.group), crypto.decrypt_text(r.text_enc), METHOD.get(r.method, r.method), SOURCE.get(r.source, r.source),
             "是" if r.enabled else "否") for r in all_reqs], (3.5, 14.5, 3, 4, 1.7))
    _heading(doc, "附录三 文件清单与属性")
    owner = {str(b.id): crypto.decrypt_text(b.name_enc) for b in d.bidders}
    _table(doc, ("所属", "文件名", "格式", "页数", "文件属性"),
           [(owner.get(str(f.bidder_id), "招标文件") if f.bidder_id else "招标文件", f.original_name, f.ext, f.page_count or "—", _file_props(f)) for f in d.files],
           (5, 8.5, 1.8, 1.6, 9.8))
    _heading(doc, "附录四 签章页核对结果")
    sign = d.view["signing"]
    if not sign:
        _para(doc, "没有识别到要求盖章签字的格式页。")
    else:
        _para(doc, "标注由初审人在系统中人工核对；本系统不识别印章与签名。")
        _table(doc, ("投标人", "格式页", "文件与位置", "核对结果"),
               [(d.name(s["bidder_id"]), s["title"], f"{s['file']}" + (f" 第 {s['page']} 页" if s.get("page") else ""), SIGN.get(s.get("mark"), "未标注"))
                for s in sign], (6, 6, 10.5, 4.0))


def _signature(doc):
    _heading(doc, "签字栏")
    _table(doc, ("初审人", "复核人", "日期"), [("\n\n", "\n\n", "\n\n")], (8.9, 8.9, 8.9))
    _para(doc, DECLARATION, size=9)


# ---------------------------------------------------------------- 入口

def build_bid_report(db, task: Task, user: User, scope: str = "full") -> tuple[str, bytes]:
    """scope：full 全部初审内容；clues 只导出串标线索。返回 (文件名, 内容)。"""
    d = _Data(db, task, user)
    doc = _new_document("标书初审疑点报告" if scope == "full" else "标书初审串标线索报告")
    _cover(doc, d, scope)
    if scope == "full":
        _overview(doc, d)
        _matrix(doc, d)
        _issues(doc, d)
        _clues(doc, d)
        _pricing(doc, d)
        _appendix(doc, d)
    else:
        _heading(doc, "一 串标线索涉及的投标人")
        pairs = _pair_lines(d)
        for line in pairs or ["没有涉及两家以上投标人的线索。"]:
            _para(doc, line, indent=12)
        _clues(doc, d, "二")
        _coverage(doc, d)
    _signature(doc)
    data = io.BytesIO()
    doc.save(data)
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", task.title) or "项目"
    prefix = "标书初审报告" if scope == "full" else "标书初审串标线索"
    return f"{prefix}_{stem}_{d.now:%Y%m%d}.docx", data.getvalue()
