"""标书本地检查一（bid.md 3.1）：混装、联系信息相同、文件属性、相同图片，含基线剔除与强 / 中 / 弱分级。

全部在本机计算，不调用模型。基线：招标文件中出现过的 rsid、图片、文件标识、作者与联系方式先剔除——
各家都从招标文件复制格式页，这些会一起带过去，是最主要的误报来源。
结果写入 bid_items（source local，证据加密）；日志只写数量。
"""
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations

from sqlalchemy import delete, select

from app.db.base import utcnow
from app.db.models import BidBidder, BidImage, BidItem, BidProject, DocNode, TaskFile
from app.redaction import detect
from app.redaction.normalize import normalize_value
from app.security import crypto

logger = logging.getLogger(__name__)
TYPES = ("mixed", "contact", "props", "image")
RANK = {"strong": 3, "medium": 2, "weak": 1, "normal": 0}

NORMAL = {
    "mixed": "厂家授权书、分包或合作业绩中正当提及另一家；请看原文语境。",
    "contact": "同一代理机构或同一厂家的联系方式；厂家售后电话写在产品资料中。",
    "props": "同一代理机构代为排版；使用同一份公开模板；电脑默认用户名相同。",
    "image": "同一厂家的资料图片；公开的范本或标识图片。",
}
# 电脑默认的作者名、公司名人人相同，不作为“作者相同”的线索
_GENERIC = {"", "administrator", "admin", "user", "users", "owner", "微软用户", "用户", "microsoft", "microsoft office user",
            "microsoft corporation", "微软中国", "微软", "china", "pc", "unknown", "python-docx", "lenovo", "dell", "hp"}

# 联系信息：类别 → (显示名, 等级, 第四十条项)
_CONTACT = {"PHONE": ("电话", "strong", "（二）"), "EMAIL": ("邮箱", "strong", "（二）"),
            "ACCOUNT": ("银行账号", "strong", "（六）"), "IDNO": ("证件号", "strong", "（二）"),
            "PM": ("项目经理", "strong", "（三）"), "PERSON": ("联系人 / 授权代表", "medium", "（二）"),
            "ADDRESS": ("地址", "medium", "（一）")}
_PM = re.compile(r"(?:项目经理|项目负责人|技术负责人)(?:[（(][^）)\n]{1,6}[）)])?[ \t]*[:：][ \t]*([一-鿿·]{2,4}?)"
                 r"(?=$|[^一-鿿·]|电话|手机|联系|职务|身份|证书|性别|年龄)")
_SUFFIX = re.compile(r"(?:集团)?(?:股份有限公司|有限责任公司|有限公司|股份公司|公司|厂)$")
_REGION = re.compile(r"^[一-鿿]{2,3}(?:省|市|自治区)")


# ---------------------------------------------------------------- 材料

def _load(db, task_id):
    """返回 (投标人列表, 节点 {bidder_id|None: [(file_id, loc, page, text)]}, 文件 {file_id: TaskFile})。"""
    bidders = list(db.scalars(select(BidBidder).where(BidBidder.task_id == task_id).order_by(BidBidder.seq)))
    files = {f.id: f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task_id).order_by(TaskFile.seq))}
    nodes = defaultdict(list)
    rows = db.execute(select(DocNode.file_id, DocNode.loc, DocNode.page, DocNode.text_enc)
                      .where(DocNode.task_id == task_id, DocNode.part != "comment").order_by(DocNode.file_id, DocNode.ord))
    for file_id, loc, page, enc in rows:
        f = files.get(file_id)
        if f is None or f.parser_version is None or enc is None:
            continue
        nodes[f.bidder_id].append((file_id, loc, page, crypto.decrypt_text(enc)))
    return bidders, nodes, files


def _excerpt(text, start, end, pad=24):
    return ("…" if start > pad else "") + text[max(0, start - pad):end + pad].strip() + ("…" if end + pad < len(text) else "")


def _where(file_id, loc, page, text, start, end):
    return {"file_id": str(file_id), "loc": loc, "page": page, "excerpt": _excerpt(text, start, end)}


# ---------------------------------------------------------------- 联系信息

def _key(category, value):
    if category == "PHONE":
        digits = re.sub(r"\D", "", value)
        return re.sub(r"^(?:0086|86)(?=1\d{10}$)", "", digits) if len(digits) >= 7 else None
    if category == "EMAIL":
        return value.strip().lower()
    if category in ("ACCOUNT", "IDNO"):
        v = re.sub(r"[^0-9A-Za-z]", "", value).upper()
        return v if len(v) >= 8 else None
    if category in ("PERSON", "PM"):
        v = re.sub(r"\s", "", value)
        return v if len(v) >= 2 else None
    v = re.sub(r"[\s,，.。、:：;；()（）\-—#号]", "", normalize_value(value))
    return v if len(v) >= 8 else None  # 地址太短（只到市、区）不比


def contacts_in(text):
    """[(类别, 归一化值, 原文值, start, end)]：复用脱敏的规则识别器，另认“项目经理：某某”。"""
    out = []
    for h in detect.find_formatted(text):
        if h.category in ("PHONE", "EMAIL", "ACCOUNT", "IDNO"):
            out.append((h.category, h.start, h.end))
    for h in detect.find_persons(text):
        out.append(("PERSON", h.start, h.end))
    for h in detect.find_addresses(text):
        out.append(("ADDRESS", h.start, h.end))
    for m in _PM.finditer(text):
        out.append(("PM", m.start(1), m.end(1)))
    result = []
    for category, start, end in out:
        key = _key(category, text[start:end])
        if key:
            result.append((category, key, text[start:end].strip(), start, end))
    return result


def check_contacts(bidders, nodes):
    baseline = {(c if c != "PM" else "PERSON", k) for _f, _l, _p, t in nodes.get(None, []) for c, k, *_ in contacts_in(t)}
    seen = defaultdict(lambda: defaultdict(list))  # (类别, 值) → bidder → [位置]
    shown = {}
    for b in bidders:
        for file_id, loc, page, text in nodes.get(b.id, []):
            for category, key, raw, start, end in contacts_in(text):
                # 同一人名既按“项目经理”又按普通标签识别时合并为一项，取更强的类别
                base = "PERSON" if category == "PM" else category
                if (base, key) in baseline:
                    continue
                slot = (base, key)
                if slot not in shown or category == "PM":
                    shown[slot] = (category, raw)
                places = seen[slot][b.id]
                if len(places) < 3:
                    places.append(_where(file_id, loc, page, text, start, end))
    groups = defaultdict(list)
    for slot, per in seen.items():
        if len(per) >= 2:
            category, raw = shown[slot]
            groups[tuple(sorted(per, key=lambda i: [b.id for b in bidders].index(i)))].append((category, raw, per))
    items = []
    for ids, rows in groups.items():
        rows.sort(key=lambda r: -RANK[_CONTACT[r[0]][1]])
        level = _CONTACT[rows[0][0]][1]
        articles = sorted({_CONTACT[r[0]][2] for r in rows})
        items.append(_item("clue", ids, "contact", level, "".join(articles),
                           "、".join(dict.fromkeys(_CONTACT[r[0]][0] for r in rows)) + "相同",
                           [{"label": _CONTACT[c][0], "value": raw, "places": {str(k): v for k, v in per.items()}} for c, raw, per in rows]))
    return items


# ---------------------------------------------------------------- 混装

def short_names(name):
    full = re.sub(r"\s", "", name)
    core = _SUFFIX.sub("", full)
    names = {full}
    for candidate in (core, _REGION.sub("", core)):
        if len(candidate) >= 4:
            names.add(candidate)
    return names


def check_mixed(bidders, nodes):
    tender = normalize_value("".join(t for *_x, t in nodes.get(None, [])))
    names = {b.id: crypto.decrypt_text(b.name_enc) for b in bidders}
    items = []
    for host in bidders:
        own = normalize_value(re.sub(r"\s", "", names[host.id]))
        for other in bidders:
            if other.id == host.id:
                continue
            # 简称在招标文件中出现、或是本家名称的一部分时不认（避免“仪器有限公司”之类误配）
            wanted = [n for n in short_names(names[other.id]) if normalize_value(n) not in tender and normalize_value(n) not in own]
            if not wanted:
                continue
            hits = []
            for file_id, loc, page, text in nodes.get(host.id, []):
                for n in sorted(wanted, key=len, reverse=True):
                    i = text.find(n)
                    if i >= 0:
                        hits.append(_where(file_id, loc, page, text, i, i + len(n)) | {"name": n})
                        break
            if hits:
                items.append(_item("clue", (host.id, other.id), "mixed", "strong", "（五）",
                                   f"{names[host.id]}的文件中出现{names[other.id]}的名称",
                                   [{"label": "出现位置", "count": len(hits), "places": {str(host.id): hits[:5]}}]))
    return items


# ---------------------------------------------------------------- 文件属性

def _time(value):
    if not value:
        return None
    v = str(value)
    m = re.match(r"D?:?(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?(?:([+\-Z])(\d{2})?'?(\d{2})?)?", v)
    if m and not re.match(r"\d{4}-", v):
        y, mo, d, h, mi, s = (int(x or 0) for x in m.groups()[:6])
        tz = timezone.utc
        if m.group(7) in ("+", "-"):
            delta = timedelta(hours=int(m.group(8) or 0), minutes=int(m.group(9) or 0))
            tz = timezone(delta if m.group(7) == "+" else -delta)
        try:
            return datetime(y, mo, d, h, mi, s, tzinfo=tz)
        except ValueError:
            return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _props(files):
    """{bidder_id|None: [(TaskFile, props)]}；props 为 None 的文件（旧格式转换件、表格、图片）不比属性。"""
    out = defaultdict(list)
    for f in files.values():
        if f.parser_version is None or f.meta_enc is None:
            continue
        props = json.loads(crypto.decrypt_text(f.meta_enc)).get("props")
        if props:
            out[f.bidder_id].append((f, props))
    return out


def _tool(props):
    return props.get("producer") or props.get("application")


def _authors(props):
    return {normalize_value(str(props[k])).strip() for k in ("creator", "last_modified_by", "author") if props.get(k)} - _GENERIC


def check_props(bidders, files):
    per = _props(files)
    base_ids = {p.get(k) for _f, p in per.get(None, []) for k in ("pdf_id", "xmp_document_id", "xmp_original_id") if p.get(k)}
    base_authors = set().union(*(_authors(p) for _f, p in per.get(None, []))) if per.get(None) else set()
    base_rsids = set().union(*(set(p.get("rsids") or []) for _f, p in per.get(None, []))) if per.get(None) else set()
    base_company = {normalize_value(p.get("company", "")) for _f, p in per.get(None, [])}
    # 从招标文件（或其模板）另存的文件带着同一个创建时间，不算“同时生成”
    base_times = [(_tool(p), _time(p.get("created"))) for _f, p in per.get(None, [])]

    def from_tender(tool, t):
        return any(tool == bt and tt and abs(t - tt) <= timedelta(minutes=10) for bt, tt in base_times)

    items = []
    for a, b in combinations(bidders, 2):
        rows, level = [], None

        def hit(label, value, lv, fa, fb):
            nonlocal level
            rows.append({"label": label, "value": value, "files": {str(a.id): fa.original_name, str(b.id): fb.original_name}})
            if level is None or RANK[lv] > RANK[level]:
                level = lv

        for fa, pa in per.get(a.id, []):
            for fb, pb in per.get(b.id, []):
                for key, label in (("pdf_id", "PDF 原始文件标识"), ("xmp_document_id", "XMP 文档标识"), ("xmp_original_id", "XMP 原始文档标识")):
                    if pa.get(key) and pa.get(key) == pb.get(key) and pa[key] not in base_ids:
                        hit(label, pa[key], "strong", fa, fb)
                for name in sorted((_authors(pa) & _authors(pb)) - base_authors):
                    hit("作者 / 最后保存者", name, "strong", fa, fb)
                company = normalize_value(pa.get("company") or "")
                if company and company not in _GENERIC and company == normalize_value(pb.get("company") or "") and company not in base_company:
                    hit("文档属性中的公司", pa["company"], "medium", fa, fb)
                shared = (set(pa.get("rsids") or []) & set(pb.get("rsids") or [])) - base_rsids
                if shared:
                    hit("Word 编辑会话标识（rsid）", f"共有 {len(shared)} 个（已剔除招标文件中的）", "medium", fa, fb)
                tool_a, tool_b = _tool(pa), _tool(pb)
                ta, tb = _time(pa.get("created")), _time(pb.get("created"))
                if (tool_a and tool_a == tool_b and ta and tb and abs(ta - tb) <= timedelta(minutes=10)
                        and not from_tender(tool_a, ta)):
                    hit("生成软件相同且创建时间相差 10 分钟内", f"{tool_a}", "weak", fa, fb)
        if rows:
            items.append(_item("clue", (a.id, b.id), "props", level, "（一）", "文件属性相同", rows))
    return items


# ---------------------------------------------------------------- 相同图片

def check_images(db, bidders, files):
    rows = db.execute(select(BidImage.file_id, BidImage.page, BidImage.sha256, BidImage.width, BidImage.height, BidImage.category)
                      .where(BidImage.file_id.in_(list(files)))).all()
    baseline = {r.sha256 for r in rows if files[r.file_id].bidder_id is None}
    order = [b.id for b in bidders]
    by_hash = defaultdict(list)
    for r in rows:
        owner = files[r.file_id].bidder_id
        if owner is not None and owner in order and r.sha256 not in baseline:
            by_hash[r.sha256].append(r)
    groups = defaultdict(list)
    for digest, found in by_hash.items():
        ids = tuple(sorted({files[r.file_id].bidder_id for r in found}, key=order.index))
        if len(ids) < 2:
            continue
        cats = {r.category for r in found}
        kind = "maker" if "maker" in cats else "seal" if "seal" in cats else "other"
        groups[(ids, kind)].append((digest, found))
    items = []
    for (ids, kind), images in groups.items():
        data = [{"label": "图片", "sha256": digest, "width": found[0].width, "height": found[0].height,
                 "places": {str(i): [{"file_id": str(r.file_id), "page": r.page} for r in found if files[r.file_id].bidder_id == i][:3]
                            for i in ids}} for digest, found in images]
        if kind == "maker":
            items.append(_item("normal_dup", ids, "image", "normal", "（一）（四）", f"相同图片 {len(images)} 张（厂家资料）", data))
        else:
            label = "印章、签名、授权或业绩页上的" if kind == "seal" else ""
            items.append(_item("clue", ids, "image", "medium" if kind == "seal" else "weak", "（一）（四）",
                               f"{label}相同图片 {len(images)} 张", data))
    return items


# ---------------------------------------------------------------- 汇总

def _item(kind, ids, type_, level, article, title, rows):
    return {"kind": kind, "bidder_ids": [str(i) for i in ids], "type": type_, "level": level, "article": article,
            "evidence": {"title": title, "rows": rows, "normal": NORMAL[type_]}}


def coverage(bidders, files):
    notes = []
    active = [b for b in bidders if any(f.bidder_id == b.id and f.parser_version for f in files.values())]
    if len(active) < 2:
        notes.append("已解析的投标人不足 2 家，未比对串标线索")
    failed = [f for f in files.values() if f.parse_error]
    if failed:
        notes.append(f"{len(failed)} 份文件未能解析，未参与比对")
    no_props = [f for f in files.values() if f.bidder_id and f.parser_version and f.ext not in ("docx", "pdf")]
    if no_props:
        notes.append(f"{len(no_props)} 份文件为旧格式、表格或图片，未比对文件属性")
    ocr = 0
    for f in files.values():
        if f.bidder_id and f.meta_enc is not None:
            ocr += json.loads(crypto.decrypt_text(f.meta_enc)).get("ocr_pages", 0)
    if ocr:
        notes.append(f"扫描页 {ocr} 页经识别后参与比对，识别有误时可能漏报")
    notes.append("印章、签名只做图片字节比对，不识别印章内容；相似段落、报价与符合项检查随后补上")
    return notes


def run(db, task):
    """计算并替换本地线索；不提交。"""
    bidders, nodes, files = _load(db, task.id)
    active = [b for b in bidders if nodes.get(b.id)]
    items = []
    if len(active) >= 2:
        items = check_mixed(active, nodes) + check_contacts(active, nodes) + check_props(active, files) + check_images(db, active, files)
    db.execute(delete(BidItem).where(BidItem.task_id == task.id, BidItem.source == "local", BidItem.type.in_(TYPES)))
    for it in items:
        db.add(BidItem(task_id=task.id, kind=it["kind"], bidder_ids=it["bidder_ids"], type=it["type"], level=it["level"],
                       article=it["article"], evidence_enc=crypto.encrypt_text(json.dumps(it["evidence"], ensure_ascii=False)),
                       source="local"))
    project = db.get(BidProject, task.id)
    project.check_enc = crypto.encrypt_text(json.dumps({"coverage": coverage(bidders, files), "finished_at": utcnow().isoformat()},
                                                       ensure_ascii=False))
    logger.info("标书本地检查完成 task=%s 线索=%s", task.id, len(items))
    return items
