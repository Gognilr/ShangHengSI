"""初审结果页（V34-B-06）：核对状态与备注、单元格详情、签章页速览、原件页面图、返回修改材料。"""
import io
import json
import re
import uuid

from sqlalchemy import select

from app.db.models import BidBidder, BidCell, BidItem, BidProject, BidRequirement, DocNode, TaskFile
from app.security import crypto
from app.security.errors import AppError, NotFoundError
from app.tasks import storage

# ---------------------------------------------------------------- 签章页速览

# 要求盖章签字的格式页标题（按出现先后取第一个命中的作页标题）
_SIGN_TITLES = ("授权委托书", "法定代表人身份证明", "法定代表人授权书", "投标函附录", "投标函", "开标一览表", "报价一览表",
                "投标报价表", "报价表", "承诺函", "承诺书", "声明函", "声明书", "偏离表", "联合体协议")
_SIGN_MARK = re.compile(r"盖章|签字|签章|公章|印章|签名")
MAX_SIGNING = 40  # 每家最多列出的格式页


def signing_pages(bidders, nodes):
    """从投标文件节点找出要求盖章签字的格式页：短行（标题、≤30 字）含格式页名称，且本页有“盖章 / 签字”等字样。
    返回 [{bidder_id, file_id, page, loc, title}]；Word 无页码时以标题所在节点为一项。"""
    out = []
    for b in bidders:
        pages, order = {}, []
        for file_id, loc, page, text, kind, _conf in nodes.get(b.id, []):
            key = (str(file_id), page if page is not None else loc)
            entry = pages.get(key)
            if entry is None:
                entry = pages[key] = {"title": None, "mark": False, "loc": loc, "page": page, "file_id": str(file_id)}
                order.append(key)
            short = re.sub(r"\s+", "", text)
            toc = re.search(r"[.…·．]{3,}|\d+$", short)  # 目录行（引导点、行末页码）不算格式页标题
            if entry["title"] is None and kind != "sheet_cell" and not toc and (kind == "heading" or len(short) <= 30):
                entry["title"] = next((t for t in _SIGN_TITLES if t in short), None)
                if entry["title"]:
                    entry["loc"] = loc
            if _SIGN_MARK.search(text):
                entry["mark"] = True
        found, seen = [], set()
        for key in order:
            e = pages[key]
            if not e["title"]:
                continue
            if e["page"] is None:
                # Word 没有页码：标题节点自成一项（“盖章”字样常在后面几段）；同名标题只留第一处（目录行与正文重复）
                if (e["file_id"], e["title"]) in seen:
                    continue
                seen.add((e["file_id"], e["title"]))
            elif not e["mark"]:
                continue
            found.append({"bidder_id": str(b.id), "file_id": e["file_id"], "page": e["page"], "loc": e["loc"], "title": e["title"]})
        out += found[:MAX_SIGNING]
    return out


def signing_key(file_id, page, loc):
    return f"{file_id}:{page if page is not None else loc}"


SIGN_MARKS = ("stamped", "missing", "unclear")


def set_signing(db, task, file_id, page, loc, mark):
    project = db.get(BidProject, task.id)
    summary = json.loads(crypto.decrypt_text(project.check_enc)) if project.check_enc else {}
    key = signing_key(file_id, page, loc)
    if not any(signing_key(s["file_id"], s["page"], s["loc"]) == key for s in summary.get("signing", [])):
        raise NotFoundError("NOT_FOUND", "该格式页不在速览中，请刷新页面")
    marks = dict(project.signing or {})
    if mark is None:
        marks.pop(key, None)
    else:
        marks[key] = mark
    project.signing = marks
    return marks


# ---------------------------------------------------------------- 核对状态与备注

def _uuid(raw):
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise NotFoundError("NOT_FOUND", "条目不存在") from None


def review_item(db, task, item_id, status, note):
    row = db.get(BidItem, _uuid(item_id))
    if row is None or row.task_id != task.id:
        raise NotFoundError("NOT_FOUND", "条目不存在")
    _apply(row, status, note)
    return _review(row)


def review_cell(db, task, requirement_id, bidder_id, status, note):
    """单元格的核对状态与备注；人工核对行没有判断结果，首次标注时建立单元格（verdict manual）。"""
    rid, bid = _uuid(requirement_id), _uuid(bidder_id)
    req, bidder = db.get(BidRequirement, rid), db.get(BidBidder, bid)
    if req is None or req.task_id != task.id or bidder is None or bidder.task_id != task.id:
        raise NotFoundError("NOT_FOUND", "单元格不存在")
    cell = db.scalar(select(BidCell).where(BidCell.requirement_id == rid, BidCell.bidder_id == bid))
    if cell is None:
        if req.method != "manual":
            raise AppError(409, "CELL_PENDING", "这一格还没有判断结果，请等检查完成")
        cell = BidCell(task_id=task.id, requirement_id=rid, bidder_id=bid, verdict="manual", source="local")
        db.add(cell)
    _apply(cell, status, note)
    return _review(cell)


def _apply(row, status, note):
    if status is not None:
        row.review_status = status
    if note is not None:
        row.note_enc = crypto.encrypt_text(note) if note.strip() else None


def _review(row):
    return {"review_status": row.review_status, "note": crypto.decrypt_text(row.note_enc) if row.note_enc else None}


# ---------------------------------------------------------------- 单元格详情

def _context(db, task, file_id, loc, around=2):
    """节点原文及同一文件前后各 around 个节点（片段上下文）。"""
    node = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc == loc))
    if node is None or node.text_enc is None:
        return None
    rows = db.execute(select(DocNode.loc, DocNode.text_enc).where(
        DocNode.file_id == node.file_id, DocNode.ord.between(node.ord - around, node.ord + around),
        DocNode.part.in_(("body", "textbox"))).order_by(DocNode.ord)).all()
    at = next((i for i, (l, _t) in enumerate(rows) if l == loc), 0)

    def text(part):
        return "\n".join(crypto.decrypt_text(t) for _l, t in part if t is not None)
    return {"before": text(rows[:at]), "text": crypto.decrypt_text(node.text_enc), "after": text(rows[at + 1:])}


def cell_detail(db, task, requirement_id, bidder_id):
    rid, bid = _uuid(requirement_id), _uuid(bidder_id)
    req, bidder = db.get(BidRequirement, rid), db.get(BidBidder, bid)
    if req is None or req.task_id != task.id or bidder is None or bidder.task_id != task.id:
        raise NotFoundError("NOT_FOUND", "单元格不存在")
    files = {f.id: f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id))}
    sources = []
    for loc in req.locs or []:
        ctx = _context(db, task, loc.get("file_id"), loc.get("loc"), 0)
        f = files.get(_uuid(loc.get("file_id")))
        if ctx and f:
            sources.append({"file": f.original_name, "text": ctx["text"]})
    cell = db.scalar(select(BidCell).where(BidCell.requirement_id == rid, BidCell.bidder_id == bid))
    out = {"requirement": {"id": str(req.id), "text": crypto.decrypt_text(req.text_enc), "group": req.group, "method": req.method,
                           "sources": sources},
           "bidder": {"id": str(bid), "name": crypto.decrypt_text(bidder.name_enc)},
           "cell": None, "places": []}
    if cell is None:
        return out
    out["cell"] = {"verdict": cell.verdict, "source": cell.source, "cite": cell.cite,
                   "reason": crypto.decrypt_text(cell.reason_enc) if cell.reason_enc else None, **_review(cell)}
    for loc in cell.locs or []:
        f = files.get(_uuid(loc.get("file_id")))
        ctx = _context(db, task, loc.get("file_id"), loc.get("loc"))
        if f is None or ctx is None:
            continue
        out["places"].append({"file_id": str(f.id), "file": f.original_name, "page": loc.get("page"),
                              "renderable": renderable(f), **ctx})
    return out


# ---------------------------------------------------------------- 原件页面图

def renderable(f):
    return f.original_deleted_at is None and f.ext in ("pdf", "jpg", "jpeg", "png")


def page_image(db, task, file_id, page, width):
    """把投标文件 PDF 的一页（或图片文件）渲染成 JPEG，供签章页速览与“查看原件页”；只在内存中生成。"""
    f = db.get(TaskFile, _uuid(file_id))
    if f is None or f.task_id != task.id or f.bidder_id is None:
        raise NotFoundError("NOT_FOUND", "文件不存在")
    if not renderable(f):
        raise AppError(422, "NOT_RENDERABLE", "该文件不能显示页面图")
    from PIL import Image
    data = storage.load_original(f.storage_key)
    if f.ext == "pdf":
        import pypdfium2
        password = crypto.decrypt_text(f.password_enc) if f.password_enc else None
        try:
            doc = pypdfium2.PdfDocument(data, password=password)
        except Exception:  # noqa: BLE001
            raise AppError(422, "PDF_UNREADABLE", "原件无法打开") from None
        try:
            if not 1 <= page <= len(doc):
                raise NotFoundError("NOT_FOUND", "页码不存在")
            p = doc[page - 1]
            try:
                w, _h = p.get_size()
                img = p.render(scale=width / max(w, 1.0)).to_pil().convert("RGB")
            finally:
                p.close()
        finally:
            doc.close()
    else:
        if page != 1:
            raise NotFoundError("NOT_FOUND", "页码不存在")
        try:
            img = Image.open(io.BytesIO(data))
            img = img.convert("RGB")
        except Exception:  # noqa: BLE001
            raise AppError(422, "IMAGE_UNREADABLE", "图片无法打开") from None
        if img.width > width:
            img = img.resize((width, max(1, round(img.height * width / img.width))))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return out.getvalue()


# ---------------------------------------------------------------- 返回修改材料

def reopen(db, task):
    """已完成初审的项目退回“待开标”，可改清单、加减文件后重新开始初审；条目与单元格保留，重跑时沿用核对状态与备注。
    模型核对需重新准备并确认（片段随材料变化）。"""
    project = db.get(BidProject, task.id)
    if project.stage != "checked":
        raise AppError(409, "NOT_CHECKED", "只有初审完成的项目可以返回修改材料")
    if project.model_state in ("preparing", "running"):
        raise AppError(409, "MODEL_BUSY", "模型核对正在进行，请完成后再返回修改")
    project.stage, project.check_state, project.check_error = "ready", None, None
    project.model_state, project.model_error = None, None


# ---------------------------------------------------------------- 初审结果（结果页与 Word 报告共用）

def check_view(db, task):
    """条目（含核对状态与备注）、单元格、要求清单、报价分析、签章页速览与覆盖说明。"""
    from app.bid import requirements as R
    project = db.get(BidProject, task.id)
    summary = json.loads(crypto.decrypt_text(project.check_enc)) if project.check_enc else {}
    rank = {"veto": 0, "strong": 1, "general": 2, "medium": 3, "weak": 4, "hint": 5, "normal": 6}
    items = sorted(db.scalars(select(BidItem).where(BidItem.task_id == task.id)), key=lambda i: (rank.get(i.level, 9), i.type))
    cells = db.scalars(select(BidCell).where(BidCell.task_id == task.id))
    reqs = db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id, BidRequirement.enabled.is_(True))
                      .order_by(BidRequirement.seq))
    files = {str(f.id): f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id))}
    marks = project.signing or {}
    values = R.load_state(project)["values"]

    def note(row):
        return crypto.decrypt_text(row.note_enc) if row.note_enc else None
    return {"state": project.check_state, "error": project.check_error, "coverage": summary.get("coverage", []),
            "steps": summary.get("steps", []), "elapsed": summary.get("elapsed", {}),
            "pricing": json.loads(crypto.decrypt_text(project.pricing_enc)) if project.pricing_enc else None,
            "parameters": {"ceiling": values.get("ceiling"), "currency": values.get("currency")},
            "requirements": [{"id": r.id, "group": r.group, "text": crypto.decrypt_text(r.text_enc), "method": r.method, "seq": r.seq}
                             for r in reqs],
            "items": [{"id": i.id, "kind": i.kind, "type": i.type, "level": i.level, "article": i.article,
                       "bidder_ids": i.bidder_ids, "requirement_id": i.requirement_id, "source": i.source,
                       "review_status": i.review_status, "note": note(i), **json.loads(crypto.decrypt_text(i.evidence_enc))} for i in items],
            "cells": [{"requirement_id": c.requirement_id, "bidder_id": c.bidder_id, "verdict": c.verdict, "locs": c.locs,
                       "reason": crypto.decrypt_text(c.reason_enc) if c.reason_enc else None, "source": c.source, "cite": c.cite,
                       "review_status": c.review_status, "note": note(c)} for c in cells],
            "signing": [{**s, "file": files[s["file_id"]].original_name, "renderable": renderable(files[s["file_id"]]),
                         "mark": marks.get(signing_key(s["file_id"], s["page"], s["loc"]))}
                        for s in summary.get("signing", []) if s["file_id"] in files]}
