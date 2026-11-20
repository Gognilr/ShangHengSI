"""标书初审 Word 报告：读取结果页共用的结论，在内存中生成。"""
import io
import json
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Cm, Pt
from sqlalchemy import select

from app.bid import requirements as R, results
from app.bid.conclusion import DECLARATION as CONCLUSION_DECLARATION
from app.checks.numbers import money
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
        "construction_max": "工期", "delivery_max": "交货期", "warranty_min": "质保期",
        "defect_min": "缺陷责任期", "performance": "类似业绩",
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
        self.cells = {(str(c["requirement_id"]), str(c["bidder_id"])): c for c in self.view["cells"]}
        self.reqs = results.matrix_requirements(self.view["requirements"], self.view["cells"], len(self.bidders))
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

def _cover(doc, d: _Data, scope: str):
    bidders = "、".join(d.names.values()) or "—"
    tenders = "\n".join(f.original_name for f in d.files if f.bidder_id is None) or "—"
    from app.bid.folders import category_name
    category = category_name(d.db, d.project.category_id)
    rows = [("项目名称", d.task.title), *([("项目分类", category)] if category else []),
            ("开标日期", d.project.opening_date.isoformat()), ("初审人", d.user.display_name),
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
    p = _para(doc, CONCLUSION_DECLARATION, bold=True)
    p.paragraph_format.space_before = Pt(6)


# ---------------------------------------------------------------- 总览与覆盖说明

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


def _coverage(doc, d: _Data):
    _sub(doc, "覆盖说明与限制")
    notes = list(d.view["coverage"])
    if d.task.policy == "local_only":
        notes.append("本项目只做本地检查，未调用模型。")
    elif d.model_rows and d.project.model_state != "done":
        notes.append("“模型判断”类要求尚未完成模型核对，相关单元格为“未核对”。")
    elif d.project.model_state == "done":
        notes.append("模型核对只覆盖检索出的相关片段，未检索到的内容可能被遗漏；引文待核实的结论请对照原件。")
    pending = len(d.view["conclusion"]["verify"])
    if pending:
        notes.append(f"仍有 {pending} 项待人工核对，报告内容尚未全部核实。")
    notes.append("报告未列出的事项不代表投标文件没有问题。")
    for n in notes:
        _para(doc, "· " + n, indent=12)


# ---------------------------------------------------------------- 要求响应矩阵

def _cell_text(d: _Data, req, bidder_id) -> str:
    if req.get("not_applicable"):
        return "—"
    cell = d.cells.get((str(req["id"]), str(bidder_id)))
    if cell is None:
        return "？"
    manual = req["method"] == "manual" or cell["verdict"] == "manual"
    if cell["review_status"] == "excluded":
        return "—" if manual else "已排除"
    if manual:
        if req["method"] == "manual" and cell["verdict"] == "manual" and cell["review_status"] == "verified":
            return "✓·已核实"
        return "？"
    text = "？" if cell["cite"] == "unverified" else (
        {'yes': '✓', 'no': '不响应', 'missing': '未找到', 'unclear': '？'}.get(cell['verdict'], '？'))
    if cell["source"] == "model":
        text += "（模型）"
    if cell["cite"] == "unverified":
        text += "·引文待核实"
    elif cell["review_status"] == "verified":
        text += "·已核实"
    return text


def _has_row_anomaly(d: _Data, req, verdict: str) -> bool:
    if req["method"] == "manual" or req.get("not_applicable"):
        return False
    return any(cell and cell["review_status"] != "excluded" and cell["cite"] != "unverified" and cell["verdict"] == verdict
               for b in d.bidders if (cell := d.cells.get((str(req["id"]), str(b.id)))))


def _matrix_table(doc, d: _Data, reqs):
    for group in results.MATRIX_GROUPS:
        grouped = [r for r in reqs if results.matrix_group(r) == group]
        if not grouped:
            continue
        counts = [f"{label} {count}" for label, count in [
            ('疑似不响应', sum(_has_row_anomaly(d, r, 'no') for r in grouped)),
            ('未找到', sum(_has_row_anomaly(d, r, 'missing') for r in grouped)),
        ] if count]
        _sub(doc, f"{group} · {len(grouped)}" + ("　" + " · ".join(counts) if counts else ''))
        rows = [(_clean(f"{r['seq']}. {r['text']}"),
                 *[_cell_text(d, r, b.id) for b in d.bidders]) for r in grouped]
        _table(doc, ("序号 · 要求", *[crypto.decrypt_text(b.name_enc) for b in d.bidders]), rows, _widths(9.5, len(d.bidders)))


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


# ---------------------------------------------------------------- 报价分析

def _pricing(doc, d: _Data, number="五"):
    """与报价分析页同序（bid-pricing.md 三）：结论 → 各家总价与结论 → 需处理事项 → 修正后报价 → 明细。"""
    _heading(doc, f"{number} 报价分析")
    pricing = d.view["pricing"]
    ceiling = d.params.get("ceiling")
    if not pricing:
        _para(doc, "没有可分析的报价（未识别到报价表或投标总价）。")
        return
    summary = d.view["conclusion"]["pricing"]
    _para(doc, summary["lede"])
    lead = "投标人按录入顺序排列；金额由本机计算。"
    if ceiling:
        lead += f"招标文件最高限价：{money(Decimal(ceiling))} {d.params.get('currency') or ''}。"
    _para(doc, lead, size=9.5)
    tier = {"certain": "确凿", "verify": "需核实", "reference": "参考"}
    rows = []
    for b in d.bidders:
        price = pricing["bidders"].get(str(b.id)) or {}
        total = _total_text(price)
        if price.get("manual_total"):
            total += "\n唱标价（人工录入）" + (f"；文件总价 {price['file_total']}" if price.get("file_total") else "")
        elif (src := price.get("source")) and price.get("total"):
            total += f"\n{src['label']}" + (f" · 第 {src['page']} 页" if src.get("page") else "")
        tags = summary["tags"].get(str(b.id)) or []
        rows.append((crypto.decrypt_text(b.name_enc), total,
                     "；".join(f"{t['text']}（{tier[t['tier']]}）" for t in tags) or "✓"))
    _table(doc, ("投标人", "投标总价", "结论"), rows, (7.5, 7, 12.2))
    c = d.view["conclusion"]
    keys = {t["key"] for tags in summary["tags"].values() for t in tags}
    entries = [e for e in c["certain"] + c["verify"] + c["reference"] if e["id"] in keys]
    if entries:
        _sub(doc, "需处理事项")
        items = {str(i["id"]): i for i in d.view["items"]}
        for e in entries:
            review = c["reviews"].get(e["id"])
            state = ("已核实 · 属实" if review["status"] == "verified" else "已核实 · 不属实") if review else tier[e["tier"]]
            _para(doc, f"{'、'.join(d.name(x) for x in e['bidder_ids'])}：{e['title']}（{state}）")
            if e["item_id"] in items:
                for line in _evidence(d, items[e["item_id"]]):
                    _para(doc, "".join(t for t, _ in line), size=9.5, indent=12)
    fixed = [(b, (pricing["bidders"].get(str(b.id)) or {}).get("corrected")) for b in d.bidders]
    if any(v for _b, v in fixed):
        _sub(doc, "修正后报价")
        for b, v in fixed:
            if v:
                _para(doc, f"{d.name(b.id)}：修正后总价 {v['total']}（{v['basis']}，原为 {v['from']}），供评标委员会参考，须投标人确认。"
                           + (f"修正后{v['over_ceiling']}。" if v.get("over_ceiling") else ""))
    _sub(doc, "明细（供复核）")
    if cmp := pricing.get("compare"):
        _para(doc, f"参加比较的 {cmp['n']} 家中，最高 {cmp['highest']}，最低 {cmp['lowest']}，平均 {cmp['mean']}"
                   + (f"，最高比最低高 {cmp['spread_pct']}" if cmp.get("spread_pct") else "") + "。")
    rows = []
    for b in d.bidders:
        price = pricing["bidders"].get(str(b.id)) or {}
        table = price.get("table") or {}
        above = (f"{price['above_lowest']}（{price['above_lowest_pct']}）" if price.get("above_lowest")
                 else price.get("not_compared") or "—")
        rows.append((crypto.decrypt_text(b.name_enc), above,
                     price.get("vs_mean") if not price.get("not_compared") and price.get("vs_mean") else "—",
                     (f"{table.get('total') or '—'}（{table.get('rows', 0)} 行）" if table else "未识别报价表")))
    _table(doc, ("投标人", "比最低价高", "偏离平均", "报价表合计"), rows, (8.7, 6, 4, 8))
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
    for key, label in (("creator", "创建程序" if f.ext == 'pdf' else "作者"), ("author", "作者"), ("last_modified_by", "最后保存者"), ("company", "公司"),
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
    _pricing(doc, d, "附录二")
    _heading(doc, "附录三 要求清单全文")
    values = d.params
    forms = f"（{values['deposit_forms']}）" if values.get("deposit_forms") else ""
    ceiling = f"最高限价 {money(Decimal(values['ceiling']))} {values.get('currency') or ''}；" if values.get("ceiling") else ""
    _para(doc, f"关键参数：{ceiling}投标有效期 {_plain(values.get('validity_days'))} 天；"
               f"保证金 {_plain(values.get('deposit'))}{forms}；联合体 " + {True: "接受", False: "不接受"}.get(values.get("consortium"), "未填")
          + "；报价 " + {True: "含税", False: "不含税"}.get(values.get("tax_included"), "未填") + "。")
    extra = [(label, values[key]) for key, label in (
        ("construction_max", "工期上限"), ("delivery_max", "交货期上限"),
        ("warranty_min", "质保期下限"), ("defect_min", "缺陷责任期下限"),
        ("performance_from", "类似业绩签订起日（含）"), ("performance_until", "类似业绩签订止日（含）"),
        ("performance_amount", "类似业绩单项最低金额（人民币元）"), ("performance_count", "类似业绩最低份数"),
    ) if values.get(key) is not None]
    if extra:
        _para(doc, "；".join(f"{label} {value}" for label, value in extra) + "。")
    all_reqs = sorted(d.db.scalars(select(BidRequirement).where(BidRequirement.task_id == d.task.id)), key=lambda r: (GROUP_ORDER.get(r.group, 9), r.seq))
    _table(doc, ("分组", "要求", "检查方式", "来源", "启用"),
           [(GROUP.get(r.group, r.group), f"{r.seq}. " + crypto.decrypt_text(r.text_enc), METHOD.get(r.method, r.method), SOURCE.get(r.source, r.source),
             "是" if r.enabled else "否") for r in all_reqs], (3.5, 14.5, 3, 4, 1.7))
    _heading(doc, "附录四 文件清单与属性")
    owner = {str(b.id): crypto.decrypt_text(b.name_enc) for b in d.bidders}
    _table(doc, ("所属", "文件名", "格式", "页数", "文件属性"),
           [(owner.get(str(f.bidder_id), "招标文件") if f.bidder_id else "招标文件", f.original_name, f.ext, f.page_count or "—", _file_props(f)) for f in d.files],
           (5, 8.5, 1.8, 1.6, 9.8))
    _heading(doc, "附录五 签章页核对结果")
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

def _conclusion_case(doc, d, case, items):
    mark = "确凿" if case["tier"] == "certain" else "需核实"
    _sub(doc, f"{case['title']} · {mark}")
    for n, e in enumerate(case["evidence"], 1):
        _para(doc, f"{n}　{e['fact']}", bold=True, indent=6)
        item = items[e["item_id"]]
        row = item["rows"][e["row"]]
        single = {**item, "rows": [row]}
        for line in _evidence(d, single):
            _para(doc, "".join(text for text, _hit in line), size=9.5, indent=12)
        for left, right in _pairs(single):
            _pair_table(doc, d, item, left, right)
        for quotes in _price_rows(single):
            names = [d.name(i) for i in e["bidder_ids"][:2]]
            _table(doc, ("分项", *names), [(_plain(x["name"]), _plain(x["left"]), _plain(x["right"])) for x in quotes[:20]],
                   (12, 7.3, 7.4))
    baseline = case["baseline"]
    if baseline.get("sentences") or baseline.get("images"):
        _para(doc, f"已剔除：与招标文件相同的句子 {baseline.get('sentences', 0)} 句、图片 {baseline.get('images', 0)} 张")
    for reason in case["normal"]:
        _para(doc, f"常见的正常原因：{reason}", size=9.5, indent=12)


def _conclusion_report(doc, d, scope):
    c = d.view["conclusion"]
    items = {str(i["id"]): i for i in d.view["items"]}
    labels = {"certain": "建议否决", "verify": "需核实", None: "未发现问题"}
    if scope == "full":
        _heading(doc, "初审结论")
        _para(doc, c["lede"])
        if c["sub"]:
            _para(doc, c["sub"])
        rows = []
        for b in c["bidders"]:
            reason = "；".join(r["title"] for r in b["reasons"][:3])
            if len(b["reasons"]) > 3:
                reason += f" 等 {len(b['reasons'])} 项"
            total = b["total"] or "未识别"
            if b["manual_total"]:
                total += "\n唱标价 · 人工录入"
            rows.append((labels[b["tier"]], b["name"], reason, total))
        _table(doc, ("结论", "投标人", "理由", "投标总价"), rows, (3.3, 6.2, 12.2, 5))
    if c["cases"]:
        _heading(doc, "疑似串通投标")
        for case in c["cases"]:
            _conclusion_case(doc, d, case, items)
    if scope != "full":
        reference = [e for e in c["reference"] if e["category"] == "串标线索"]
    else:
        reference = c["reference"]
        _heading(doc, "需核实事项")
        for review in c["reviews"].values():
            entry = review["entry"]
            result = "已核实 · 属实" if review["status"] == "verified" else f"已核实 · 不属实（{review['detail'] or review['reason_label']}）"
            _para(doc, f"{'、'.join(d.name(b) for b in entry['bidder_ids'])}：{entry['title']} · {result}")
        if not c["reviews"] and not c["verify"]:
            _para(doc, "无")
        if c["verify"]:
            _sub(doc, "尚待核实")
            for e in c["verify"]:
                _para(doc, f"{'、'.join(d.name(b) for b in e['bidder_ids'])}：{e['title']}")
                if e["item_id"] in items:
                    for line in _evidence(d, items[e["item_id"]]):
                        _para(doc, "".join(t for t, _ in line), size=9.5, indent=12)
        if c["certain"]:
            _heading(doc, "确凿项")
            if c["signed"]:
                date = datetime.fromisoformat(c["signed"]).astimezone(ZoneInfo("Asia/Shanghai"))
                _para(doc, f"已签认（{date:%Y-%m-%d %H:%M}）")
            for e in c["certain"]:
                if not e["case_id"]:
                    _para(doc, f"{'、'.join(d.name(b) for b in e['bidder_ids'])}：{e['title']}", bold=True)
                    item = items.get(e["item_id"])
                    if item:
                        for line in _evidence(d, item):
                            _para(doc, "".join(t for t, _ in line), size=9.5, indent=12)
                        if item["note"]:
                            _para(doc, f"备注：{item['note']}", size=9.5, indent=12)
        if c["second_calls"]:
            _para(doc, f"二次核验 {c['second_calls']} 次，均经本机脱敏与出口复检。")
        if c["site"]:
            _heading(doc, "现场核验清单")
            for req in c["site"]:
                _para(doc, f"□ {req['seq']}. {req['text']}")
    _heading(doc, f"附录 参考事项 {len(reference)} 条（不影响结论）")
    for e in reference:
        _para(doc, f"{'、'.join(d.name(b) for b in e['bidder_ids'])}：{e['title']}")
        item = items.get(e["item_id"])
        if item and item["review_status"] != "excluded":
            if item.get("normal") or item.get("reference_reason"):
                _para(doc, item.get("reference_reason") or f"常见的正常原因：{item['normal']}", size=9.5)
            for left, right in _pairs(item):
                _pair_table(doc, d, item, left, right)
    _coverage(doc, d)


def build_bid_report(db, task: Task, user: User, scope: str = "full") -> tuple[str, bytes]:
    """scope：full 全部初审内容；clues 只导出串标线索。返回 (文件名, 内容)。"""
    d = _Data(db, task, user)
    doc = _new_document("标书初审疑点报告" if scope == "full" else "标书初审串标线索报告")
    _cover(doc, d, scope)
    _conclusion_report(doc, d, scope)
    if scope == "full":
        _appendix(doc, d)
    _signature(doc)
    data = io.BytesIO()
    doc.save(data)
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", task.title) or "项目"
    prefix = "标书初审报告" if scope == "full" else "标书初审串标线索"
    return f"{prefix}_{stem}_{d.now:%Y%m%d}.docx", data.getvalue()
