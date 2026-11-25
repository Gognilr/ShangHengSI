"""标书初审 Word 问题清单（V35-E-01 定位收缩）：读取结果页共用的结论，在内存中生成。

只写问题、依据与位置：严重问题、串标线索、提示；不写结论性措辞，不含要求响应矩阵、报价分析与签字栏。
"""
import io
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Cm, Pt
from sqlalchemy import select

from app.bid import results
from app.bid.conclusion import DECLARATION as CONCLUSION_DECLARATION
from app.db.models import BidBidder, BidProject, BidRequirement, LlmCall, Task, TaskFile, User
from app.export.report_docx import _clean, _font, _heading, _new_document, _table
from app.security import crypto

DECLARATION = CONCLUSION_DECLARATION + "本清单只供招标方经办人员内部使用。"
GROUP = {"qualification": "资格条件", "substantive": "★ 条款", "veto": "否决条款", "pricing": "报价要求",
         "validity": "有效期与保证金", "signing": "签章与格式页", "general": "必须 / 不得", "format": "投标文件格式（范本）"}
GROUP_ORDER = {key: i for i, key in enumerate(("veto", "substantive", "qualification", "pricing", "validity", "signing", "general", "format"))}
METHOD = {"local": "本机检查", "model": "模型补查", "manual": "只列出"}
SIGN = {"stamped": "已盖章", "missing": "缺章", "unclear": "看不清", None: "未标注"}
SECTION = {"tech": "技术参数章节，已降一档", "price": "报价章节，已升一档"}
MAX_TEXT = 600       # 并排对照每侧最多摘录的字数
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
        self.reqs = {str(r["id"]): r for r in self.view["requirements"]}
        # 条款出处：条款原文所在的招标文件
        self.req_files = {str(r.id): self.file_names.get(str((r.locs or [{}])[0].get("file_id")), "")
                          for r in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id))}
        self.codes = {str(b.id): b.code.strip("（）()") for b in self.bidders}
        self.models = sorted(db.scalars(select(LlmCall.model_name).where(
            LlmCall.task_id == task.id, LlmCall.purpose == "bid", LlmCall.status == "sent").distinct()))
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
            ("开标日期", d.project.opening_date.isoformat()), ("经办人", d.user.display_name),
            ("生成时间", d.now.strftime("%Y-%m-%d %H:%M")), ("投标人", f"{len(d.bidders)} 家（按录入顺序）：{bidders}"),
            ("招标文件", tenders), ("范围", "全部问题" if scope == "full" else "只含串标线索")]
    _table(doc, ("项目", "内容"), rows, (3.5, 23.2))
    _heading(doc, "检查方式")
    _para(doc, "文件属性、相同图片、联系信息、相似段落、报价与确定性检查（多个报价、大小写、合计、证书有效期，以及 ★ 与否决条款中的工期、"
               "交货期、质保期、类似业绩等）均在本机完成，招标文件与投标文件原文不外发。")
    if d.task.policy == "local_only":
        _para(doc, "本项目只做本地检查，未调用模型。")
    elif d.project.model_state == "done" and d.models:
        _para(doc, f"★ 与否决条款中本机判断不了的，由 {'、'.join(d.models)} 补查：只发送检索出的相关片段（已在本机保护企业与个人信息），"
                   "投标人以代号表示，结果在本机还原。")
    p = _para(doc, CONCLUSION_DECLARATION, bold=True)
    p.paragraph_format.space_before = Pt(6)


def _coverage(doc, d: _Data):
    _sub(doc, "覆盖说明与限制")
    notes = list(d.view["coverage"])
    if d.task.policy == "local_only":
        notes.append("本项目只做本地检查，未调用模型。")
    elif d.project.model_state == "done":
        notes.append("模型补查只覆盖检索出的相关片段，未检索到的内容可能被遗漏；引文待核实的请对照原件。")
    notes.append("清单未列出的事项不代表投标文件没有问题。")
    for n in notes:
        _para(doc, "· " + n, indent=12)


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
            owner = "招标文件" if bid == "tender" else d.name(bid)
            for p in places:
                lines.append(f"  {owner} · {d.place(p)}" + (f"：{_clean(_clip(p['excerpt'], 160))}" if p.get("excerpt") else ""))
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


def _entry(doc, d: _Data, e, items, *, bold=False):
    """一条问题：投标人与问题一句话，下面写依据（条款或法条）与位置。"""
    _para(doc, f"{'、'.join(d.name(b) for b in e['bidder_ids'])}：{e['title']}", bold=bold)
    req = d.reqs.get(str(e["requirement_id"])) if e.get("requirement_id") else None
    if req:
        _para(doc, f"依据：招标文件{GROUP.get(req['group'], '')}第 {req['seq']} 条：{_clip(req['text'], 200)}", size=9.5, indent=12)
    if e.get("article"):
        law = ("《招标投标法实施条例》第四十条" if e["category"] in ("串通", "串标线索")
               else "《招标投标法实施条例》" if "第五十一条" in e["article"] else "")
        _para(doc, f"依据：{law}{e['article']}", size=9.5, indent=12)
    item = items.get(e.get("item_id") or "")
    if item:
        for line in _evidence(d, item):
            _para(doc, "".join(t for t, _ in line), size=9.5, indent=12)


def _case(doc, d, case, items):
    names = "、".join(f"{d.codes.get(str(i), '')} {d.name(i)}" for i in case["bidder_ids"])
    _sub(doc, names + (f" · 《招标投标法实施条例》第四十条{case['article']}" if case.get("article") else ""))
    for n, e in enumerate(case["evidence"], 1):
        _para(doc, f"{n}　{e['fact']}", bold=True, indent=6)
        item = items[e["item_id"]]
        single = {**item, "rows": [item["rows"][e["row"]]]}
        for line in _evidence(d, single):
            _para(doc, "".join(text for text, _hit in line), size=9.5, indent=12)
        for left, right in _pairs(single):
            _pair_table(doc, d, item, left, right)
    baseline = case["baseline"]
    if baseline.get("sentences") or baseline.get("images"):
        _para(doc, f"已剔除：与招标文件相同的句子 {baseline.get('sentences', 0)} 句、图片 {baseline.get('images', 0)} 张")
    for reason in case["normal"]:
        _para(doc, f"常见的正常原因：{reason}", size=9.5, indent=12)


def _basis(d: _Data, e) -> str:
    """依据：招标文件条款（文件 · 分组 · 第 n 条与条款摘录），或法条。"""
    parts = []
    req = d.reqs.get(str(e["requirement_id"])) if e.get("requirement_id") else None
    if req:
        where = "招标文件" + (f"《{d.req_files[str(req['id'])]}》" if d.req_files.get(str(req["id"])) else "")
        parts.append(f"{where}{GROUP.get(req['group'], '')}第 {req['seq']} 条：{_clip(req['text'], 120)}")
    if e.get("article"):
        law = "《招标投标法实施条例》第四十条" if e["category"] in ("串通", "串标线索") else "《招标投标法实施条例》" if "第五十一条" in e["article"] else ""
        parts.append(f"{law}{e['article']}")
    return "\n".join(parts) or "—"


def _where(d: _Data, e, bidder) -> str:
    """位置：这一家的文件 · 页（最多 3 处）。"""
    own = [p for p in e.get("places", []) if str(p["bidder_id"]) == bidder] or [p for p in e.get("places", []) if p["bidder_id"] == "tender"]
    out = list(dict.fromkeys(d.place(p) for p in own))
    return "、".join(out[:3]) + (f" 等 {len(out)} 处" if len(out) > 3 else "") if out else "—"


def _problem(d: _Data, e, bidder, cases) -> str:
    """问题：一句话写事实；两家之间的线索写成“与某家：相同内容”，不写结论性措辞。"""
    case = cases.get(e.get("case_id") or "")
    if case:
        facts = {}
        for ev in case["evidence"]:
            if bidder in ev["bidder_ids"]:
                for other in ev["bidder_ids"]:
                    if other != bidder:
                        facts.setdefault(other, []).append(ev["fact"])
        return "\n".join(f"与 {d.codes.get(o, '')} {d.name(o)}：{'；'.join(dict.fromkeys(f))}" for o, f in facts.items()) or e["title"]
    title = e["title"]
    others = [b for b in e["bidder_ids"] if b != bidder]
    if others and e["category"] == "串标线索":
        title = f"与 {'、'.join(d.name(o) for o in others)}：{title}"
    return title + ("（模型补查，请核实）" if e.get("source") == "model" else "")


def _by_bidder(doc, d: _Data):
    """全部问题按投标人分节：严重、提示各一张“问题 / 依据 / 位置”表（澄明 8.5）。"""
    c = d.view["conclusion"]
    cases = {case["id"]: case for case in c["cases"]}
    _heading(doc, "问题清单")
    serious = sum(1 for b in c["bidders"] if b["severe"])
    _para(doc, f"{len(c['bidders'])} 家投标人中，{serious} 家列出严重问题。每条写问题、依据与位置，请对照原件核实。")
    if c["sub"]:
        _para(doc, c["sub"])
    for b in c["bidders"]:
        bid = str(b["id"])
        severe = [e for e in c["severe"] if bid in e["bidder_ids"]]
        hint = [e for e in c["hint"] if bid in e["bidder_ids"]]
        _heading(doc, f"{d.codes.get(bid, '')}　{b['name']}")
        if not severe and not hint:
            _para(doc, "未列出问题。")
            continue
        for label, rows in (("严重", severe), ("提示", hint)):
            if rows:
                _sub(doc, f"{label} {len(rows)} 条")
                _table(doc, ("问题", "依据", "位置"), [(_problem(d, e, bid, cases), _basis(d, e), _where(d, e, bid)) for e in rows],
                       (11.2, 8.5, 7.0))


def _issues(doc, d: _Data, scope):
    c = d.view["conclusion"]
    items = {str(i["id"]): i for i in d.view["items"]}
    clues = scope != "full"
    if not clues:
        _by_bidder(doc, d)
        cases = [case for case in c["cases"] if case["id"] not in {e["id"] for e in c["dismissed"]}]
        if cases:
            _heading(doc, "附：两家之间的相同内容")
            for case in cases:
                _case(doc, d, case, items)
        _coverage(doc, d)
        return
    hint = [e for e in c["hint"] if not e["case_id"] and e["category"] == "串标线索"]
    cases = [case for case in c["cases"] if case["id"] not in {e["id"] for e in c["dismissed"]}]
    _heading(doc, "串标线索")
    for case in cases:
        _case(doc, d, case, items)
    if not cases:
        _para(doc, "未形成疑似串通投标案件。")
    _heading(doc, f"提示 {len(hint)} 条")
    for e in hint:
        _entry(doc, d, e, items)
        item = items.get(e.get("item_id") or "")
        if item:
            for left, right in _pairs(item):
                _pair_table(doc, d, item, left, right)
    _coverage(doc, d)


def _appendix(doc, d: _Data):
    _heading(doc, "附录一 条款清单")
    all_reqs = sorted(d.db.scalars(select(BidRequirement).where(BidRequirement.task_id == d.task.id)), key=lambda r: (GROUP_ORDER.get(r.group, 9), r.seq))
    _table(doc, ("分组", "条款", "检查方式", "启用"),
           [(GROUP.get(r.group, r.group), f"{r.seq}. " + crypto.decrypt_text(r.text_enc), METHOD.get(r.method, r.method), "是" if r.enabled else "否")
            for r in all_reqs], (3.5, 17, 4.5, 1.7))
    _heading(doc, "附录二 文件清单与属性")
    owner = {str(b.id): crypto.decrypt_text(b.name_enc) for b in d.bidders}
    _table(doc, ("所属", "文件名", "格式", "页数", "文件属性"),
           [(owner.get(str(f.bidder_id), "招标文件") if f.bidder_id else "招标文件", f.original_name, f.ext, f.page_count or "—", _file_props(f)) for f in d.files],
           (5, 8.5, 1.8, 1.6, 9.8))
    _heading(doc, "附录三 签章页核对结果")
    sign = d.view["signing"]
    if not sign:
        _para(doc, "没有识别到要求盖章签字的格式页。")
    else:
        _para(doc, "标注由经办人在系统中人工核对；本系统不识别印章与签名。")
        _table(doc, ("投标人", "格式页", "文件与位置", "核对结果"),
               [(d.name(s["bidder_id"]), s["title"], f"{s['file']}" + (f" 第 {s['page']} 页" if s.get("page") else ""), SIGN.get(s.get("mark"), "未标注"))
                for s in sign], (6, 6, 10.5, 4.0))


def build_bid_report(db, task: Task, user: User, scope: str = "full") -> tuple[str, bytes]:
    """scope：full 全部问题；clues 只导出串标线索。返回 (文件名, 内容)。"""
    d = _Data(db, task, user)
    doc = _new_document("标书初审问题清单" if scope == "full" else "标书初审串标线索")
    if note := d.view["ocr_result"]["note"]:
        _para(doc, note)
    _cover(doc, d, scope)
    _issues(doc, d, scope)
    if scope == "full":
        _appendix(doc, d)
    _para(doc, DECLARATION, size=9)
    data = io.BytesIO()
    doc.save(data)
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", task.title) or "项目"
    prefix = "标书初审问题清单" if scope == "full" else "标书初审串标线索"
    return f"{prefix}_{stem}_{d.now:%Y%m%d}.docx", data.getvalue()
