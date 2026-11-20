"""初审结果页（V34-B-06；V35-E-01）：“不是问题”、单元格详情、签章页速览、原件页面图、返回修改材料。"""
import io
import json
import re
import uuid

from sqlalchemy import delete, func, select

from app.bid.service import require_originals
from app.db.models import BidBidder, BidCell, BidItem, BidProject, BidRequirement, DocNode, LlmCall, TaskFile
from app.security import crypto
from app.security.errors import AppError, NotFoundError
from app.tasks import storage

# ---------------------------------------------------------------- 签章页速览

# 要求盖章签字的格式页标题（按出现先后取第一个命中的作页标题）
_SIGN_TITLES = ("授权委托书", "法定代表人身份证明", "法定代表人授权书", "投标函附录", "投标函", "开标一览表", "报价一览表",
                "投标报价表", "报价表", "报价单", "报价函", "承诺函", "承诺书", "声明函", "声明书", "偏离表", "联合体协议")
_SIGN_MARK = re.compile(r"盖章|签字|签章|公章|印章|签名")
MAX_SIGNING = 40  # 每家最多列出的格式页


def signing_pages(bidders, nodes):
    """从投标文件节点找出要求盖章签字的格式页：短行（标题、≤30 字）含格式页名称，且本页有“盖章 / 签字”等字样；
    标题在页首前 3 行时不要求这些字样（扫描页的识别文字里常没有“盖章”，V34-B-08c）。
    返回 [{bidder_id, file_id, page, loc, title}]；Word 无页码时以标题所在节点为一项。"""
    out = []
    for b in bidders:
        pages, order = {}, []
        for file_id, loc, page, text, kind, *_ in nodes.get(b.id, []):
            key = (str(file_id), page if page is not None else loc)
            entry = pages.get(key)
            if entry is None:
                entry = pages[key] = {"title": None, "mark": False, "loc": loc, "page": page, "file_id": str(file_id), "n": 0}
                order.append(key)
            entry["n"] += 1
            short = re.sub(r"\s+", "", text)
            toc = re.search(r"[.…·．]{3,}|\d+$", short)  # 目录行（引导点、行末页码）不算格式页标题
            if entry["title"] is None and kind != "sheet_cell" and not toc and (kind == "heading" or len(short) <= 30):
                entry["title"] = next((t for t in _SIGN_TITLES if t in short.replace("报价单位", "")), None)
                if entry["title"]:
                    entry["loc"] = loc
                    head = page is not None and entry["n"] <= 3 and not re.match(r"(?:附|见|详见|如)", short)
                    entry["mark"] = entry["mark"] or head
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
    return set_signing_many(db, task, [(file_id, page, loc)], mark)


def set_signing_many(db, task, refs, mark):
    """refs 为 (file_id, page, loc)；任一页不在速览中则全部不改。"""
    project = db.get(BidProject, task.id)
    summary = json.loads(crypto.decrypt_text(project.check_enc)) if project.check_enc else {}
    known = {signing_key(s["file_id"], s["page"], s["loc"]) for s in summary.get("signing", [])}
    keys = [signing_key(*r) for r in refs]
    if any(k not in known for k in keys):
        raise NotFoundError("NOT_FOUND", "该格式页不在速览中，请刷新页面")
    marks = dict(project.signing or {})
    for key in keys:
        if mark is None:
            marks.pop(key, None)
        else:
            marks[key] = mark
    project.signing = marks
    return marks


# ---------------------------------------------------------------- 不是问题

def _uuid(raw):
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise NotFoundError("NOT_FOUND", "条目不存在") from None


def load_conclusion(project) -> dict:
    """项目结论的人工处理：{"dismissed": [条目键]}。"""
    return json.loads(crypto.decrypt_text(project.conclusion_enc)) if project.conclusion_enc else {}


def dismiss(db, task, key, dismissed):
    """“不是问题”（可撤销）：条目键须是本项目结论中出现过的事项。不提交。"""
    project = db.get(BidProject, task.id, populate_existing=True, with_for_update=True)
    state = load_conclusion(project)
    keys = set(state.get("dismissed", []))
    if dismissed:
        c = check_view(db, task)["conclusion"]
        if key not in {e["id"] for level in ("severe", "hint", "dismissed") for e in c[level]}:
            raise NotFoundError("NOT_FOUND", "事项不存在，请刷新页面")
        keys.add(key)
    else:
        keys.discard(key)
    state["dismissed"] = sorted(keys)
    project.conclusion_enc = crypto.encrypt_text(json.dumps(state, ensure_ascii=False, sort_keys=True))


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
                           "seq": req.seq, "sources": sources},
           "bidder": {"id": str(bid), "name": crypto.decrypt_text(bidder.name_enc)},
           "cell": None, "places": []}
    if cell is None:
        return out
    out["cell"] = {"verdict": cell.verdict, "source": cell.source, "cite": cell.cite,
                   "reason": crypto.decrypt_text(cell.reason_enc) if cell.reason_enc else None}
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


def page_image(db, task, file_id, page, width, loc=None):
    """把投标文件 PDF 的一页（或图片文件）渲染成 JPEG，供签章页速览与“查看原件页”；只在内存中生成。
    loc 为命中的节点：节点在这一页且有坐标时，用琥珀色浅底框出（证据栏的“高亮命中处”，V35-F-05）。"""
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
                scale = width / max(w, 1.0)
                img = p.render(scale=scale).to_pil().convert("RGB")
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
        scale = 1.0
        if img.width > width:
            scale = width / img.width
            img = img.resize((width, max(1, round(img.height * width / img.width))))
    if loc:
        _highlight(db, task, f, page, loc, img, scale)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return out.getvalue()


def _highlight(db, task, f, page, loc, img, scale):
    """节点框按页面坐标（PDF 点或原图像素）乘渲染比例画到页面图上；不在这一页或没有坐标时不画。"""
    from PIL import Image, ImageDraw
    bbox = db.scalar(select(DocNode.bbox).where(DocNode.task_id == task.id, DocNode.file_id == f.id, DocNode.loc == loc,
                                                DocNode.page == page))
    if not bbox or len(bbox) != 4:
        return
    box = tuple(round(v * scale) + d for v, d in zip(bbox, (-3, -3, 3, 3)))
    layer = img.copy()
    ImageDraw.Draw(layer).rectangle(box, fill=(255, 214, 102))
    img.paste(Image.blend(img, layer, 0.35))
    ImageDraw.Draw(img).rectangle(box, outline=(230, 150, 20), width=2)


# ---------------------------------------------------------------- 返回修改材料

def clear_model_results(db, project):
    # 再次初审使用当前材料生成结论，历史外发记录仍独立保留。
    for kind in (BidCell, BidItem):
        db.execute(delete(kind).where(kind.task_id == project.task_id, kind.source == "model"))
    project.model_state, project.model_error, project.model_enc = None, None, None


def reopen(db, task):
    """已完成初审的项目退回“待开标”，可改清单、加减文件后重新开始初审；“不是问题”按条目键保留。
    模型补查需重新准备并确认（片段随材料变化）。"""
    project = db.get(BidProject, task.id, populate_existing=True)
    if project.stage != "checked":
        raise AppError(409, "NOT_CHECKED", "只有初审完成的项目可以返回修改材料")
    require_originals(db, task.id)
    from app.bid.model import require_idle
    require_idle(project)
    project.stage, project.check_state, project.check_error = "ready", None, None
    project.model_state, project.model_error, project.model_enc = None, None, None


# ---------------------------------------------------------------- 初审结果（结果页与 Word 报告共用）

def sent_count(db, task_id) -> int:
    return db.scalar(select(func.count()).select_from(LlmCall).where(LlmCall.task_id == task_id, LlmCall.purpose == 'bid')) or 0


def sent_view(db, task):
    """只读实际请求，不还原；本人权限由路由 owned 校验。"""
    rows = db.scalars(select(LlmCall).where(LlmCall.task_id == task.id, LlmCall.purpose == 'bid').order_by(LlmCall.id))
    out, counts = [], {}
    for seq, row in enumerate(rows, 1):
        text, purpose = row.request_text, '标书模型请求'
        if text:
            try:
                messages = json.loads(text)['messages']
                system = messages[0]['content']
                body = '\n\n'.join(m['content'] for m in messages)
                if system.startswith('你是招标文件整理助手'):
                    purpose = '要求补充'
                elif system.startswith('二次核验'):
                    purpose = '二次核验'
                else:
                    who = re.search(r'投标人代号：(\[投标人[A-Z]+\])(?:（第 (\d+) / (\d+) 批）)?', body)
                    if who:
                        purpose = f"模型核对 {who[1]}" + (f" 第 {who[2]} / {who[3]} 批" if who[2] else '')
                text = body
            except (ValueError, KeyError, TypeError, IndexError):
                pass
        counts[purpose] = counts.get(purpose, 0) + 1
        out.append({'seq': seq, 'purpose': purpose + (f" 第 {counts[purpose]} 次" if purpose in ('要求补充', '二次核验') else ''),
                    'created_at': row.created_at, 'status': row.status, 'chars': len(text) if text is not None else None, 'text': text})
    return out

def check_view(db, task):
    """条目、单元格、条款、各家总价、签章页速览、覆盖说明与初审结论（严重 / 提示）。"""
    project = db.get(BidProject, task.id)
    summary = json.loads(crypto.decrypt_text(project.check_enc)) if project.check_enc else {}
    rank = {"veto": 0, "strong": 1, "general": 2, "medium": 3, "weak": 4, "hint": 5, "normal": 6}
    items = sorted(db.scalars(select(BidItem).where(BidItem.task_id == task.id)), key=lambda i: (rank.get(i.level, 9), i.type))
    cells = db.scalars(select(BidCell).where(BidCell.task_id == task.id))
    reqs = db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id, BidRequirement.enabled.is_(True))
                      .order_by(BidRequirement.seq))
    files = {str(f.id): f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id))}
    marks = project.signing or {}
    view = {"state": project.check_state, "error": project.check_error, "coverage": summary.get("coverage", []),
            "steps": summary.get("steps", []), "elapsed": summary.get("elapsed", {}),
            "pricing": json.loads(crypto.decrypt_text(project.pricing_enc)) if project.pricing_enc else None,
            "requirements": [{"id": r.id, "group": r.group, "text": crypto.decrypt_text(r.text_enc), "method": r.method, "seq": r.seq}
                             for r in reqs],
            "items": [{"id": i.id, "kind": i.kind, "type": i.type, "level": i.level, "article": i.article,
                       "bidder_ids": i.bidder_ids, "requirement_id": i.requirement_id, "source": i.source,
                       **json.loads(crypto.decrypt_text(i.evidence_enc))} for i in items],
            "cells": [{"requirement_id": c.requirement_id, "bidder_id": c.bidder_id, "verdict": c.verdict, "locs": c.locs,
                       "reason": crypto.decrypt_text(c.reason_enc) if c.reason_enc else None, "source": c.source, "cite": c.cite}
                      for c in cells],
            "signing": [{**s, "file": files[s["file_id"]].original_name, "renderable": renderable(files[s["file_id"]]),
                         "mark": marks.get(signing_key(s["file_id"], s["page"], s["loc"]))}
                        for s in summary.get("signing", []) if s["file_id"] in files]}
    from app.bid import conclusion
    bidders = {str(b.id): crypto.decrypt_text(b.name_enc) for b in db.scalars(
        select(BidBidder).where(BidBidder.task_id == task.id).order_by(BidBidder.seq))}
    view["conclusion"] = conclusion.build(
        view, bidders, local_only=task.policy == "local_only", model_state=project.model_state,
        baseline=summary.get("baseline"), dismissed=load_conclusion(project).get("dismissed", []))
    return view
