"""标书本地检查（bid.md 3.1，A 段）：一为混装、联系信息相同、文件属性、相同图片（B-03）；
二为报价、确定性符合项与相似段落（B-04，见 pricing、compliance、similar）。含基线剔除与强 / 中 / 弱分级。

全部在本机计算，不调用模型。基线：招标文件中出现过的 rsid、图片、文件标识、作者与联系方式先剔除——
各家都从招标文件复制格式页，这些会一起带过去，是最主要的误报来源。
结果写入 bid_items / bid_cells（source local，证据加密）；三步各自提交，日志只写数量。
"""
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import combinations

from sqlalchemy import delete, select

from app.db.base import utcnow
from app.db.models import BidBidder, BidCell, BidImage, BidItem, BidProject, BidRequirement, DocNode, TaskFile
from app.redaction import detect
from app.redaction.normalize import normalize_value
from app.security import crypto

logger = logging.getLogger(__name__)
# 三步依次提交，前端轮询时先看到先完成的（bid.md 3.1“先出结果的先做”）
STEPS = {"clues": ("mixed", "contact", "props", "image"),
         "pricing": ("pricing", "multi_total", "sum_total", "ceiling", "low_price", "uppercase", "row_calc", "sum_rows", "validity",
                     "deposit", "cert_expired", "ai_instruction", "manual_total_diff"),
         "similar": ("similar",)}
RANK = {"strong": 3, "medium": 2, "weak": 1, "normal": 0}

NORMAL = {
    "mixed": "厂家授权书、分包或合作业绩中正当提及另一家；请看原文语境。",
    "contact": "同一代理机构或同一厂家的联系方式；厂家售后电话写在产品资料中。",
    "props": "同一代理机构代为排版；使用同一份公开模板；电脑默认用户名相同。",
    "image": "同一厂家的资料图片；公开的范本或标识图片。",
    "similar": "同一代理机构的模板；同一厂家的产品说明；行业通用写法或标准条文。",
    "pricing": "按同一份市场指导价或同一厂家报价单下浮；分项价格本身由招标文件给定。",
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
    """返回 (投标人列表, 节点 {bidder_id|None: [(file_id, loc, page, text, kind, ocr_conf)]}, 文件 {file_id: TaskFile})。"""
    bidders = list(db.scalars(select(BidBidder).where(BidBidder.task_id == task_id).order_by(BidBidder.seq)))
    files = {f.id: f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task_id).order_by(TaskFile.seq))}
    nodes = defaultdict(list)
    seqs = {i: f.seq for i, f in files.items()}
    rows = db.execute(select(DocNode.file_id, DocNode.loc, DocNode.page, DocNode.text_enc, DocNode.kind, DocNode.ocr_conf)
                      .where(DocNode.task_id == task_id, DocNode.part != "comment").order_by(DocNode.file_id, DocNode.ord)).all()
    rows.sort(key=lambda r: seqs.get(r[0], 0))  # 按文件录入顺序，文件内按 ord（稳定排序）
    for file_id, loc, page, enc, kind, conf in rows:
        f = files.get(file_id)
        if f is None or f.parser_version is None or enc is None:
            continue
        nodes[f.bidder_id].append((file_id, loc, page, crypto.decrypt_text(enc), kind, conf))
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
    baseline = {(c if c != "PM" else "PERSON", k) for _f, _l, _p, t, *_ in nodes.get(None, []) for c, k, *_ in contacts_in(t)}
    seen = defaultdict(lambda: defaultdict(list))  # (类别, 值) → bidder → [位置]
    shown = {}
    for b in bidders:
        for file_id, loc, page, text, *_ in nodes.get(b.id, []):
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
    tender = normalize_value("".join(n[3] for n in nodes.get(None, [])))
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
            cover = []
            for file_id, loc, page, text, *_ in nodes.get(host.id, []):
                for n in sorted(wanted, key=len, reverse=True):
                    i = text.find(n)
                    if i >= 0:
                        place = _where(file_id, loc, page, text, i, i + len(n)) | {"name": n}
                        hits.append(place)
                        if page == 1 and re.search(r"(?:投标人|投标单位)\s*[：:]\s*" + re.escape(names[other.id]), text):
                            cover.append(place)
                        break
            if hits:
                evidence = [{"label": "出现位置", "count": len(hits), "places": {str(host.id): hits[:5]}}]
                if cover:
                    evidence.insert(0, {"label": "封面", "mixed_page": True, "places": {str(host.id): cover[:5]}})
                items.append(_item("clue", (host.id, other.id), "mixed", "strong", "（五）",
                                   f"{names[host.id]}的文件中出现{names[other.id]}的名称", evidence))
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


def _authors(props, ext=None):
    # PDF Creator 是生成程序，Author 才是作者；Word creator 对应作者。
    keys = ('author',) if ext == 'pdf' else ('creator', 'last_modified_by', 'author')
    return {normalize_value(str(props[k])).strip() for k in keys if props.get(k)} - _GENERIC


def check_props(bidders, files, history=()):
    per = _props(files)
    base_ids = {p.get(k) for _f, p in per.get(None, []) for k in ("pdf_id", "xmp_document_id", "xmp_original_id") if p.get(k)}
    base_authors = set().union(*(_authors(p, f.ext) for f, p in per.get(None, []))) if per.get(None) else set()
    base_rsids = set().union(*(set(p.get("rsids") or []) for _f, p in per.get(None, []))) if per.get(None) else set()
    for _f, props in _props({f.id: f for f in history}).get(None, []):
        base_rsids.update(props.get("rsids") or [])
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
                for name in sorted((_authors(pa, fa.ext) & _authors(pb, fb.ext)) - base_authors):
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
                    hit("生成软件相同且创建时间相差 10 分钟内", tool_a, "weak", fa, fb)
        if rows:
            items.append(_item("clue", (a.id, b.id), "props", level, "（一）", "文件属性相同", rows))
    return items


def timeline(bidders, files):
    """文件时间线（甘特图，V34-B-06d）：每个读到时间的文件一条“创建—最后修改”。group 相同的文件来自不同投标人、
    文件标识或作者相同（已剔除招标文件中的）或同一软件 10 分钟内创建，与“文件属性相同”线索同一口径。"""
    per = _props(files)
    order = {b.id: k for k, b in enumerate(bidders)}
    base = per.get(None, [])
    base_ids = {p.get(k) for _f, p in base for k in ("pdf_id", "xmp_document_id", "xmp_original_id") if p.get(k)}
    base_authors = set().union(*(_authors(p, f.ext) for f, p in base)) if base else set()
    base_times = [(_tool(p), _time(p.get("created"))) for _f, p in base]
    rows = [(f, p, _time(p.get("created")), _time(p.get("modified"))) for owner, found in per.items()
            if owner is None or owner in order for f, p in found]
    rows = [r for r in rows if r[2] or r[3]]
    rows.sort(key=lambda r: (-1 if r[0].bidder_id is None else order[r[0].bidder_id], r[2] or r[3]))
    parent = list(range(len(rows)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, j in combinations(range(len(rows)), 2):
        (fa, pa, ta, _ma), (fb, pb, tb, _mb) = rows[i], rows[j]
        if fa.bidder_id is None or fb.bidder_id is None or fa.bidder_id == fb.bidder_id:
            continue
        same_id = any(pa.get(k) and pa.get(k) == pb.get(k) and pa[k] not in base_ids for k in ("pdf_id", "xmp_document_id", "xmp_original_id"))
        same_author = bool((_authors(pa, fa.ext) & _authors(pb, fb.ext)) - base_authors)
        tool = _tool(pa)
        near = (tool and tool == _tool(pb) and ta and tb and abs(ta - tb) <= timedelta(minutes=10)
                and not any(tool == bt and tt and abs(ta - tt) <= timedelta(minutes=10) for bt, tt in base_times))
        if same_id or same_author or near:
            parent[root(i)] = root(j)
    sizes = defaultdict(int)
    for i in range(len(rows)):
        sizes[root(i)] += 1
    groups = {}
    out = []
    for i, (f, _p, created, modified) in enumerate(rows):
        r = root(i)
        group = groups.setdefault(r, len(groups) + 1) if sizes[r] > 1 else None
        out.append({"bidder_id": str(f.bidder_id) if f.bidder_id else None, "file_id": str(f.id),
                    "created": created.astimezone(timezone.utc).isoformat() if created else None,
                    "modified": modified.astimezone(timezone.utc).isoformat() if modified else None, "group": group})
    return out


# ---------------------------------------------------------------- 相同图片

def check_images(db, bidders, files, history=(), stats=None):
    rows = db.execute(select(BidImage.file_id, BidImage.page, BidImage.sha256, BidImage.width, BidImage.height, BidImage.category)
                      .where(BidImage.file_id.in_(list(files)))).all()
    baseline = {r.sha256 for r in rows if files[r.file_id].bidder_id is None}
    if history:
        baseline.update(db.scalars(select(BidImage.sha256).where(BidImage.file_id.in_([f.id for f in history]))))
    order = [b.id for b in bidders]
    if stats is not None:
        stats["images"] = len({r.sha256 for r in rows if files[r.file_id].bidder_id in order and r.sha256 in baseline})
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

def _item(kind, ids, type_, level, article, title, rows, requirement_id=None):
    evidence = {"title": title, "rows": rows}
    if type_ in NORMAL:
        evidence["normal"] = NORMAL[type_]
    return {"kind": kind, "bidder_ids": [str(i) for i in ids], "type": type_, "level": level, "article": article,
            "evidence": evidence, "requirement_id": requirement_id}


def ocr_progress(files):
    """(已识别页数, 合计页数)：投标文件的扫描页延后识别，做完一页补一页（pipeline.ocr_project）。"""
    done = total = 0
    for f in files.values():
        if f.bidder_id and f.meta_enc is not None:
            meta = json.loads(crypto.decrypt_text(f.meta_enc))
            done += meta.get("ocr_pages", 0)
            total += meta.get("ocr_pages", 0) + len(meta.get("ocr_pending") or [])
    return done, total


def coverage(bidders, files, extra=()):
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
    done, total = ocr_progress(files)
    if total > done:
        notes.append(f"扫描页识别中 {done} / {total}，尚未识别的页暂未参与比对，识别后自动补上")
    elif done:
        notes.append(f"扫描页 {done} 页经识别后参与比对，识别有误时可能漏报")
    notes += list(extra)
    notes.append("印章、签名只做图片字节比对，不识别印章内容；“模型判断”类要求由模型核对补上")
    return notes


def _replace(db, task_id, types, items):
    """替换指定类别；证据相同的条目保留原标识与核验记录。"""
    old = defaultdict(list)
    for row in db.scalars(select(BidItem).where(BidItem.task_id == task_id, BidItem.source == "local", BidItem.type.in_(types))):
        key = (row.kind, row.type, tuple(row.bidder_ids), str(row.requirement_id), row.level, row.article)
        old[key].append((row, json.loads(crypto.decrypt_text(row.evidence_enc))))
    for it in items:
        key = (it["kind"], it["type"], tuple(it["bidder_ids"]), str(it["requirement_id"]), it["level"], it["article"])
        rows = old[key]
        at = next((i for i, (_row, evidence) in enumerate(rows) if evidence == it["evidence"]), None)
        if at is not None:
            rows.pop(at)
            continue
        db.add(BidItem(task_id=task_id, kind=it["kind"], bidder_ids=it["bidder_ids"], type=it["type"], level=it["level"],
                       article=it["article"], requirement_id=it["requirement_id"],
                       evidence_enc=crypto.encrypt_text(json.dumps(it["evidence"], ensure_ascii=False)), source="local"))
    for rows in old.values():
        for row, _evidence in rows:
            db.delete(row)


QUOTE_TYPES = ("pricing", "ceiling", "low_price", "manual_total_diff", "uppercase")


def _save_pricing(project, analysis, facts):
    """修正后报价由 compliance.evaluate 写入 facts，随报价分析一并加密保存。"""
    for bid, fact in facts.items():
        if fact.get("corrected"):
            analysis["bidders"][str(bid)]["corrected"] = fact["corrected"]
    project.pricing_enc = crypto.encrypt_text(json.dumps(analysis, ensure_ascii=False))


def _pricing_step(db, task, bidders, nodes, files, *, quote_only=False):
    """报价分析、报价规律线索、确定性符合项问题与“本地规则”单元格。"""
    from app.bid import compliance, pricing, requirements as R
    project = db.get(BidProject, task.id)
    from app.bid.service import opening_price
    manual = {str(b.id): opening_price(b) for b in bidders if b.opening_price_enc}
    params = R.load_state(project)["values"]
    ceiling = Decimal(params["ceiling"]) if params.get("ceiling") else None
    per = {b.id: [compliance.BidNode(loc, text, files[file_id].seq, None, kind=kind, ocr_conf=conf, file_id=file_id, page=page)
                  for file_id, loc, page, text, kind, conf in nodes.get(b.id, [])] for b in bidders}
    analysis, clues, facts = pricing.analyze([b.id for b in bidders], per, ceiling, params.get("currency"),
                                             lambda n: _where(n.file_id, n.loc, n.page, n.text, 0, len(n.text)), manual)
    kinds = {}
    for r in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id, BidRequirement.enabled.is_(True),
                                                     BidRequirement.method == "local").order_by(BidRequirement.seq)):
        kind = compliance.requirement_kind(crypto.decrypt_text(r.text_enc))
        if kind and (not quote_only or kind == "ceiling"):
            kinds.setdefault(kind, []).append(r)

    def place(n, s, e):
        where = _where(n.file_id, n.loc, n.page, n.text, s, e)
        if n.ocr_conf is not None and n.ocr_conf < compliance.LOW_OCR_CONF:
            where["low_ocr"] = True
        return where

    items = [_item("clue", c["ids"], "pricing", c["level"], "（四）", c["title"], c["rows"]) for c in clues]
    if quote_only:
        for b in bidders:
            issues, verdicts = compliance.evaluate(b.id, per[b.id], facts[b.id], params, project.opening_date, place)
            linked = kinds.get("ceiling", [])
            items.extend(_item("issue", (b.id,), it["type"], it["level"], it["article"], it["title"], it["rows"],
                               linked[0].id if linked and it["type"] == "ceiling" else None)
                         for it in issues if it["type"] in QUOTE_TYPES)
            for req in linked:
                cell = db.scalar(select(BidCell).where(BidCell.requirement_id == req.id, BidCell.bidder_id == b.id))
                verdict, reason, where = verdicts["ceiling"]
                if cell is None:
                    cell = BidCell(task_id=task.id, requirement_id=req.id, bidder_id=b.id, source="local")
                    db.add(cell)
                if cell.verdict != verdict or not cell.reason_enc or crypto.decrypt_text(cell.reason_enc) != reason:
                    cell.review_status, cell.note_enc = "pending", None
                cell.verdict, cell.reason_enc = verdict, crypto.encrypt_text(reason)
                cell.locs = [{k: w[k] for k in ("file_id", "loc", "page")} for w in where]
        _save_pricing(project, analysis, facts)
        return items, []
    # 重跑保留同一单元格的核对状态与备注；人工核对行的单元格（verdict manual）由用户建立，不在此删除
    # 方法已不是“人工核对”的行（返回修改材料时改过）去掉人工建立的单元格，免得与本地 / 模型判断冲突
    db.execute(delete(BidCell).where(BidCell.task_id == task.id, BidCell.verdict == "manual", BidCell.requirement_id.in_(
        select(BidRequirement.id).where(BidRequirement.task_id == task.id, BidRequirement.method != "manual"))))
    kept = {(c.requirement_id, c.bidder_id): (c.review_status, c.note_enc) for c in db.scalars(select(BidCell).where(
        BidCell.task_id == task.id, BidCell.source == "local", BidCell.verdict != "manual"))}
    db.execute(delete(BidCell).where(BidCell.task_id == task.id, BidCell.source == "local", BidCell.verdict != "manual"))
    no_table = []
    for b in bidders:
        if facts[b.id]["main"] is None:
            no_table.append(crypto.decrypt_text(b.name_enc))
        issues, verdicts = compliance.evaluate(b.id, per[b.id], facts[b.id], params, project.opening_date, place)
        for it in issues:
            linked = kinds.get(it["kind"], [None])[0]
            items.append(_item("issue", (b.id,), it["type"], it["level"], it["article"], it["title"], it["rows"],
                               linked.id if linked else None))
        for kind, rows in kinds.items():
            verdict, reason, where = verdicts[kind]
            for r in rows:
                status, note = kept.get((r.id, b.id), ("pending", None))
                db.add(BidCell(task_id=task.id, requirement_id=r.id, bidder_id=b.id, verdict=verdict,
                               locs=[{"file_id": w["file_id"], "loc": w["loc"], "page": w["page"]} for w in where],
                               reason_enc=crypto.encrypt_text(reason), source="local", review_status=status, note_enc=note))
    _save_pricing(project, analysis, facts)
    notes = [f"{'、'.join(no_table)}未识别出报价表（需有数量、单价、金额列），未做分项计算与报价规律比对"] if no_table else []
    return items, notes


def _similar_step(bidders, nodes, history=()):
    from app.bid import similar
    found, stats = similar.find([b.id for b in bidders], {b.id: [n[:5] for n in nodes.get(b.id, [])] for b in bidders},
                                [n[:5] for n in nodes.get(None, [])] + list(history))
    items = []
    for f in found:
        items.append(_item("clue", f["ids"], "similar", f["level"], "（四）", f["title"], f["rows"]))
        items[-1]["evidence"]["map"] = f["map"]
    return items, stats


def run(db, task):
    """依次计算三步，每步替换本地条目并提交（前端先看到先完成的）；返回条目数。
    运行期间扫描页识别有进展（识别任务只在检查完成后才重跑）时，结束后再跑一轮，最多三轮。"""
    for _ in range(3):
        count, before = _run_once(db, task)
        files = {f.id: f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id)
                                             .execution_options(populate_existing=True))}
        if ocr_progress(files)[0] == before:
            break
    return count


def _history(db, task, project):
    from app.bid.feedback import agency
    from app.bid.requirements import tender_nodes
    from app.db.models import Task
    organization = agency(project)
    if not organization:
        return [], []
    projects = [p.task_id for p in db.scalars(select(BidProject).join(Task).where(
        Task.owner_id == task.owner_id, Task.id != task.id,
        BidProject.parameters_enc.is_not(None))) if agency(p) == organization]
    if not projects:
        return [], []
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id.in_(projects), TaskFile.bidder_id.is_(None),
                                                TaskFile.original_deleted_at.is_(None))))
    live_ids = {str(f.id) for f in files}
    nodes = [(n.file_id, n.loc, None, n.text, n.kind) for p in projects
             for n in tender_nodes(db, p) if n.file_id in live_ids]
    return files, nodes


def _run_once(db, task, *, progressive=True):
    bidders, nodes, files = _load(db, task.id)
    before = ocr_progress(files)[0]
    active = [b for b in bidders if nodes.get(b.id)]
    two = len(active) >= 2
    project = db.get(BidProject, task.id)
    from app.bid.results import signing_pages
    historic_files, historic_nodes = _history(db, task, project) if two else ([], [])
    summary = {"steps": [], "elapsed": {}, "notes": {}, "signing": signing_pages(active, nodes), "baseline": {}}
    count = 0

    def save(name, started, items, notes=()):
        nonlocal count
        _replace(db, task.id, STEPS[name], items)
        summary["steps"].append(name)
        summary["notes"][name] = list(notes)
        summary["elapsed"][name] = round(time.monotonic() - started, 1)
        extra = [n for step in summary["notes"].values() for n in step]
        if len(summary["steps"]) < len(STEPS):
            extra.append("报价、符合项与相似段落检查正在进行，结果随后补上")
        summary["coverage"] = coverage(bidders, files, extra)
        summary["finished_at"] = utcnow().isoformat()
        project.check_enc = crypto.encrypt_text(json.dumps(summary, ensure_ascii=False))
        if progressive:
            db.commit()
        else:
            db.flush()
        count += len(items)

    started = time.monotonic()
    clues = (check_mixed(active, nodes) + check_contacts(active, nodes) + check_props(active, files, historic_files)
             + check_images(db, active, files, historic_files, summary["baseline"])) if two else []
    summary["timeline"] = timeline(active, files)
    save("clues", started, clues)
    started = time.monotonic()
    items, notes = _pricing_step(db, task, [b for b in bidders if nodes.get(b.id) or b.opening_price_enc], nodes, files)
    save("pricing", started, items if two else [i for i in items if i["kind"] == "issue"], notes)
    started = time.monotonic()
    items, stats = _similar_step(active, nodes, historic_nodes) if two else ([], {})
    summary["baseline"]["sentences"] = stats.get("dropped_tender", 0)
    save("similar", started, items)
    logger.info("标书本地检查完成 task=%s 条目=%s 句子=%s 用时=%s", task.id, count, stats.get("sentences", 0), summary["elapsed"])
    return count, before
