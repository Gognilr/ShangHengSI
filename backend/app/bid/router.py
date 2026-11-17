"""标书项目接口；所有读写均需 use.bid，内容仍只本人可见。"""
import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import func, select, update as sql_update
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.bid import folders, service
from app.bid.schemas import (BatchReviewInput, BidderInput, CellReviewInput, FolderInput, FolderPatch, ParametersInput, PlaceInput, PasswordInput, ProjectInput, RequirementInput,
                             RequirementPatch, ReviewInput, SigningInput)
from app.db.base import utcnow
from app.db.models import BidBidder, BidProject, BidRequirement, Task, TaskFile, User
from app.db.session import get_db
from app.parsing.pdf import PdfPasswordError, _open
from app.parsing.model import ParseError
from app.security import crypto
from app.security.deps import load_owned_task
from app.security.errors import AppError
from app.security.permissions import require
from app.security.sessions import client_ip
from app.tasks import service as tasks, storage

router = APIRouter(prefix="/bid", dependencies=[Depends(require("use.bid"))])


def owned(id: str, user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    return load_owned_task(db, user, id, "bid")


@router.get("")
def projects(page: int = Query(1, ge=1, le=100000), q: str = Query("", max_length=100), category: int | None = None,
             folder: str | None = Query(None, max_length=40), stage: str | None = Query(None, pattern="^(preparing|ready|checking|checked)$"),
             date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
             user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    """项目列表与搜索筛选（V34-B-01a）：名称包含、分类、文件夹（含子文件夹；none 为未归档）、阶段、开标日期区间。"""
    base = (Task.owner_id == user.id, Task.kind == "bid")
    where = list(base)
    if q.strip():
        where.append(Task.title.ilike(tasks._like_pattern(q.strip()), escape="\\"))
    if category is not None:
        where.append(BidProject.category_id == category)
    if folder == "none":
        where.append(BidProject.folder_id.is_(None))
    elif folder:
        where.append(BidProject.folder_id.in_(folders.subtree(db, user, folders.folder(db, user, folder).id)))
    if stage:
        where.append(BidProject.stage == stage)
    if date_from:
        where.append(BidProject.opening_date >= date_from)
    if date_to:
        where.append(BidProject.opening_date <= date_to)
    rows = db.execute(select(Task, BidProject).join(BidProject).where(*where)
                      .order_by(Task.last_activity_at.desc(), Task.id).offset((page - 1) * 20).limit(20)).all()
    active = db.scalar(select(func.count()).select_from(Task).join(BidProject).where(*base, BidProject.stage == "checking")) or 0
    return {"items": [{"id": t.id, "title": t.title, "stage": p.stage, "status": t.status, "opening_date": p.opening_date,
                       "last_activity_at": t.last_activity_at, "category_id": p.category_id, "folder_id": p.folder_id} for t, p in rows],
            "total": db.scalar(select(func.count()).select_from(Task).join(BidProject).where(*where)), "checking_count": active}


@router.post("", status_code=201)
def create(body: ProjectInput, request: Request, user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    category = folders.choose_category(db, body.category_id)
    folder_id = folders.folder(db, user, body.folder_id).id if body.folder_id else None
    task = Task(owner_id=user.id, kind="bid", title=body.title, stance="purchase", source="incoming",
                policy=body.policy, status="draft")
    db.add(task)
    db.flush()
    db.add(BidProject(task_id=task.id, opening_date=body.opening_date, category_id=category, folder_id=folder_id))
    audit.record(db, "task_create", user_id=user.id, target_type="task", target_id=str(task.id), detail={"kind": "bid"}, ip=client_ip(request))
    db.commit()
    return service.detail(db, task)


# ---------------------------------------------------------------- 文件夹与分类（V34-B-01a）；写在 /{id} 之前

@router.get("/folders")
def folder_tree(user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    return folders.tree(db, user)


@router.post("/folders", status_code=201)
def folder_create(body: FolderInput, user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    folders.create(db, user, body.name, body.parent_id)
    db.commit()
    return folders.tree(db, user)


@router.patch("/folders/{fid}")
def folder_rename(fid: str, body: FolderPatch, user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    folders.rename(db, user, fid, body.name)
    db.commit()
    return folders.tree(db, user)


@router.delete("/folders/{fid}")
def folder_delete(fid: str, user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    folders.remove(db, user, fid)
    db.commit()
    return folders.tree(db, user)


@router.get("/categories")
def category_list(db: Session = Depends(get_db)):
    return {"items": folders.categories(db)}


@router.get("/{id}")
def get(task: Task = Depends(owned), db: Session = Depends(get_db)):
    return service.detail(db, task)


@router.patch("/{id}")
def update(body: ProjectInput, user: User = Depends(require("use.bid")), task: Task = Depends(owned), db: Session = Depends(get_db)):
    """名称、开标日期、外发策略只在开始初审前可改；分类与文件夹任何阶段都可改（只改请求中给出的项）。"""
    project = db.get(BidProject, task.id)
    if (body.title, body.opening_date, body.policy) != (task.title, project.opening_date, task.policy):
        task = service.editable(db, task)
        task.title, task.policy = body.title, body.policy
        project.opening_date = body.opening_date
    if "category_id" in body.model_fields_set:
        project.category_id = folders.choose_category(db, body.category_id, project.category_id)
    if "folder_id" in body.model_fields_set:
        project.folder_id = folders.folder(db, user, body.folder_id).id if body.folder_id else None
    task.last_activity_at = utcnow()
    db.commit()
    return service.detail(db, task)


@router.put("/{id}/folder")
def place(body: PlaceInput, user: User = Depends(require("use.bid")), task: Task = Depends(owned), db: Session = Depends(get_db)):
    """拖动归档；不改最近活动时间，免得列表顺序跟着跳。"""
    db.get(BidProject, task.id).folder_id = folders.folder(db, user, body.folder_id).id if body.folder_id else None
    db.commit()
    return {"folder_id": db.get(BidProject, task.id).folder_id}


@router.delete("/{id}", status_code=204)
def delete(request: Request, task: Task = Depends(owned), user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    # 任何阶段都可删除；正在解析的文件提交后才轮到删除，解析循环随后发现项目已不存在
    tasks.delete_task(db, task, user, client_ip(request))
    return Response(status_code=204)


@router.post("/{id}/bidders", status_code=201)
def add_bidder(body: BidderInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    task = service.editable(db, task)
    service.new_bidder(db, task, body.name)
    task.last_activity_at = utcnow()
    db.commit()
    return service.detail(db, task)


@router.patch("/{id}/bidders/{bidder_id}")
def rename_bidder(bidder_id: str, body: BidderInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    task = service.editable(db, task)
    service.bidder(db, task, bidder_id).name_enc = crypto.encrypt_text(body.name)
    task.last_activity_at = utcnow()
    db.commit()
    return service.detail(db, task)


@router.delete("/{id}/bidders/{bidder_id}")
def delete_bidder(bidder_id: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    task = service.editable(db, task)
    row = service.bidder(db, task, bidder_id)
    if db.scalar(select(TaskFile.id).where(TaskFile.bidder_id == row.id).limit(1)):
        raise AppError(409, "BIDDER_HAS_FILES", "请先移除此投标人的文件")
    db.delete(row)
    task.last_activity_at = utcnow()
    db.commit()
    return service.detail(db, task)


@router.post("/{id}/files")
def upload(files: list[UploadFile] = File(...), bidder_id: str | None = Form(None), groups: str | None = Form(None),
           task: Task = Depends(owned), db: Session = Depends(get_db)):
    names = None
    if groups is not None:
        try:
            names = json.loads(groups)
            if not isinstance(names, list) or len(names) != len(files):
                raise ValueError
            names = [BidderInput(name=name).name for name in names]
        except (ValueError, TypeError):
            raise AppError(422, "INVALID_GROUPS", "请检查文件夹分组名称") from None
    return service.add_files(db, task, files, bidder_id, names)


@router.delete("/{id}/files/{fid}")
def remove_file(fid: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    task = service.editable(db, task)
    row = service.file(db, task, fid)
    key = row.storage_key
    db.delete(row)
    db.flush()
    if task.status != "parsing":  # 解析中由解析循环收尾时给出状态
        failed = db.scalar(select(TaskFile.id).where(TaskFile.task_id == task.id, TaskFile.parse_error.is_not(None)).limit(1))
        task.status = "parse_failed" if failed else "draft"
        task.error_message = "部分文件尚未解析，请处理文件行中的提示" if failed else None
    task.last_activity_at = utcnow()
    db.commit()
    storage.delete_original(key)
    return service.detail(db, task)


@router.put("/{id}/files/{fid}/password")
def set_password(fid: str, body: PasswordInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    task = service.editable(db, task)
    row = service.file(db, task, fid)
    if row.ext != "pdf" or row.original_deleted_at is not None:
        raise AppError(422, "NOT_PDF", "只能为仍保留原件的 PDF 设置密码")
    try:
        with _open(storage.load_original(row.storage_key), body.password, service.LIMITS["pdf_pages"]):
            pass
    except PdfPasswordError:
        raise AppError(422, "PDF_PASSWORD_INCORRECT", "密码不对，请核对投标方提供的密码") from None
    except ParseError as exc:
        raise AppError(422, "PDF_UNREADABLE", str(exc)) from None
    row.password_enc = crypto.encrypt_text(body.password)
    row.parse_error, row.parser_version = None, None
    service.queue_parse(db, task)
    db.commit()
    return service.detail(db, task)


@router.get("/{id}/files/{fid}/password")
def reveal_password(fid: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    row = service.file(db, task, fid)
    return Response(content=json.dumps({"password": crypto.decrypt_text(row.password_enc) if row.password_enc else None}),
                    media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/{id}/parse")
def reparse(task: Task = Depends(owned), db: Session = Depends(get_db)):
    task = service.editable(db, task)
    if not db.scalar(select(TaskFile.id).where(TaskFile.task_id == task.id).limit(1)):
        raise AppError(422, "NO_FILES", "请先加入招标文件或投标文件")
    db.execute(sql_update(TaskFile).where(TaskFile.task_id == task.id, TaskFile.original_deleted_at.is_(None))
               .values(parser_version=None, parse_error=None))
    service.queue_parse(db, task)
    db.commit()
    return service.detail(db, task)


# ---------------------------------------------------------------- 第 1 步：招标文件与要求清单（V34-B-02）

def _requirements(db, task):
    from app.bid import extract, requirements as R
    data = R.view(db, task)
    data["receipt"] = extract.receipt(db, task)
    data["stage"] = db.get(BidProject, task.id).stage
    return data


def _changed(db, task):
    """改清单或参数：版本加一；已确认（待开标）的项目保持待开标，开标后由检查任务只重跑受影响的行。"""
    db.get(BidProject, task.id).requirements_version += 1
    task.last_activity_at = utcnow()


@router.get("/{id}/tender")
def tender_text(view: str = Query("original", pattern="^(original|ai)$"), task: Task = Depends(owned), db: Session = Depends(get_db)):
    """招标文件文字画布；AI 视野只给脱敏后的文字，未脱敏的行不返回内容。"""
    from app.bid.requirements import markers, tender_nodes
    from app.db.models import DocNode
    files = {str(f.id): f.original_name for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.bidder_id.is_(None)).order_by(TaskFile.seq))}
    if view == "ai":
        rows = db.execute(select(DocNode.file_id, DocNode.loc, DocNode.kind, DocNode.redacted_text).join(TaskFile, TaskFile.id == DocNode.file_id)
                          .where(DocNode.task_id == task.id, DocNode.part == "body", TaskFile.bidder_id.is_(None))
                          .order_by(TaskFile.seq, DocNode.ord)).all()
        return {"files": files, "nodes": [{"file_id": str(f), "loc": loc, "kind": kind, "text": text} for f, loc, kind, text in rows if text is not None],
                "markers": []}
    nodes = tender_nodes(db, task.id)
    return {"files": files, "nodes": [vars(n) for n in nodes],
            "markers": [{"kind": k, "loc": n.loc, "start": s, "end": e} for k, n, s, e in markers(nodes)]}


@router.get("/{id}/requirements")
def requirements_view(task: Task = Depends(owned), db: Session = Depends(get_db)):
    return _requirements(db, task)


@router.put("/{id}/parameters")
def save_parameters(body: ParametersInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid import requirements as R
    task = service.editable(db, task)
    project = db.get(BidProject, task.id)
    state = R.load_state(project)
    state["values"], state["confirmed"] = body.model_dump(), True
    R.save_state(project, state)
    _changed(db, task)
    db.commit()
    return _requirements(db, task)


@router.post("/{id}/requirements", status_code=201)
def add_requirement(body: RequirementInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid import requirements as R
    task = service.editable(db, task)
    known = {n.loc: n for n in R.tender_nodes(db, task.id)}
    locs = []
    for loc in body.locs:
        node = known.get(loc.loc)
        if node is None or node.file_id != loc.file_id or loc.end > len(node.text) or loc.start >= loc.end:
            raise AppError(422, "INVALID_LOC", "所选原文位置无效，请重新划选")
        locs.append(loc.model_dump())
    R._add(db, task.id, R.next_seq(db, task.id), body.group, body.text, locs, body.method, "manual")
    _changed(db, task)
    db.commit()
    return _requirements(db, task)


@router.patch("/{id}/requirements/{rid}")
def patch_requirement(rid: str, body: RequirementPatch, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid import requirements as R
    task = service.editable(db, task)
    row = R.requirement(db, task, rid)
    if body.group is not None:
        row.group = body.group
    if body.text is not None:
        row.text_enc, row.redacted_text = crypto.encrypt_text(body.text), None  # 改过的文字由 B-05 发送前重新脱敏
    if body.method is not None:
        row.method = body.method
    if body.enabled is not None:
        row.enabled = body.enabled
    _changed(db, task)
    db.commit()
    return _requirements(db, task)


@router.delete("/{id}/requirements/{rid}")
def delete_requirement(rid: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid import requirements as R
    task = service.editable(db, task)
    db.delete(R.requirement(db, task, rid))
    _changed(db, task)
    db.commit()
    return _requirements(db, task)


@router.post("/{id}/requirements/extract")
def reextract(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """重新规则抽取：手工加入的行保留，其余按当前招标文件重建。"""
    from app.bid import requirements as R
    task = service.editable(db, task)
    if not R.parsed_tender_ids(db, task.id):
        raise AppError(422, "NO_TENDER", "请先加入并解析招标文件")
    R.extract(db, task)
    task.last_activity_at = utcnow()
    db.commit()
    return _requirements(db, task)


@router.post("/{id}/assist/{action}")
def model_assist(action: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """模型补充：start 本机脱敏 → confirm 确认发送 → cancel 放弃。"""
    from app.bid import extract
    if action not in ("start", "confirm", "cancel"):
        raise AppError(404, "NOT_FOUND", "接口不存在")
    task = service.editable(db, task)
    getattr(extract, action)(db, task)
    task.last_activity_at = utcnow()
    db.commit()
    return _requirements(db, task)


@router.post("/{id}/confirm")
def confirm_requirements(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """确认关键参数与要求清单：项目进入“待开标”。"""
    from app.bid import requirements as R
    task = service.editable(db, task)
    project = db.get(BidProject, task.id)
    if not R.parsed_tender_ids(db, task.id):
        raise AppError(422, "NO_TENDER", "请先加入并解析招标文件")
    if project.extract_state in ("redacting", "extracting"):
        raise AppError(409, "EXTRACT_BUSY", "模型补充正在进行，请完成后再确认")
    if not db.scalar(select(func.count()).select_from(BidRequirement).where(BidRequirement.task_id == task.id, BidRequirement.enabled.is_(True))):
        raise AppError(422, "NO_REQUIREMENTS", "要求清单为空，请至少保留一条要求")
    state = R.load_state(project)
    state["confirmed"] = True
    R.save_state(project, state)
    project.stage = "ready"
    task.last_activity_at = utcnow()
    db.commit()
    return _requirements(db, task)


@router.post("/{id}/check")
def start_check(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """开始初审：本地检查在后台进行；开始后材料锁定。"""
    from app.db import jobs
    task = service.editable(db, task)
    project = db.get(BidProject, task.id)
    if project.stage != "ready":
        raise AppError(409, "NOT_READY", "请先确认关键参数与要求清单")
    if service.busy(db, task) or db.scalar(select(TaskFile.id).where(TaskFile.task_id == task.id, TaskFile.parser_version.is_(None),
                                                                      TaskFile.parse_error.is_(None)).limit(1)):
        raise AppError(409, "PARSING", "文件还在解析，请稍候")
    if not db.scalar(select(TaskFile.id).where(TaskFile.task_id == task.id, TaskFile.bidder_id.is_not(None),
                                               TaskFile.parser_version.is_not(None)).limit(1)):
        raise AppError(422, "NO_BIDS", "请先加入并解析投标文件")
    project.stage, project.check_state, project.check_error = "checking", "running", None
    jobs.enqueue(db, "bid", {"task_id": str(task.id), "stage": "check"})
    task.last_activity_at = utcnow()
    db.commit()
    return service.detail(db, task)


@router.get("/{id}/check")
def check_result(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """初审结果：条目（含核对状态与备注）、单元格、要求清单、报价分析、签章页速览与覆盖说明。"""
    from app.bid import results
    return results.check_view(db, task)


@router.get("/{id}/report", dependencies=[Depends(require("use.export"))])
def report(scope: str = Query("full", pattern="^(full|clues)$"), task: Task = Depends(owned),
           user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    """Word 报告（服务器内存生成、不留存）：full 全部初审内容，clues 只含串标线索。初审完成后可导出。"""
    from app.export.bid_docx import build_bid_report
    from app.export.router import _response
    if db.get(BidProject, task.id).stage != "checked":
        raise AppError(409, "REPORT_UNAVAILABLE", "初审完成后才能导出报告")
    filename, content = build_bid_report(db, task, user, scope)
    audit.record(db, "report_export", user_id=user.id, target_type="task", target_id=str(task.id), detail={"kind": "bid", "scope": scope})
    db.commit()
    return _response(filename, content)


@router.patch("/{id}/items/{iid}")
def review_item(iid: str, body: ReviewInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid import results
    out = results.review_item(db, task, iid, body.review_status, body.note)
    task.last_activity_at = utcnow()
    db.commit()
    return out


@router.put("/{id}/cells")
def review_cell(body: CellReviewInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid import results
    out = results.review_cell(db, task, body.requirement_id, body.bidder_id, body.review_status, body.note)
    task.last_activity_at = utcnow()
    db.commit()
    return out


@router.post("/{id}/review")
def review_batch(body: BatchReviewInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """批量核对（V34-B-06c）：确认本机计算的 n 条、人工核对“各家都符合”、签章页“本家全部已盖章”。一次事务。"""
    from app.bid import results
    if (body.items or body.cells) and body.review_status is None:
        raise AppError(422, "INVALID", "缺少核对状态")
    db.scalar(select(BidProject).where(BidProject.task_id == task.id).with_for_update().execution_options(populate_existing=True))
    results.review_batch(db, task, body.items, [(c.requirement_id, c.bidder_id) for c in body.cells], body.review_status)
    marks = results.set_signing_many(db, task, [(s.file_id, s.page, s.loc) for s in body.signing], body.mark) if body.signing else None
    task.last_activity_at = utcnow()
    db.commit()
    return {"items": len(set(body.items)), "cells": len({(c.requirement_id, c.bidder_id) for c in body.cells}), "marks": marks}


@router.get("/{id}/cell")
def cell_detail(requirement_id: str, bidder_id: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """矩阵单元格抽屉：要求原文与出处、该家对应片段（含上下文）、判断理由、核对状态与备注。"""
    from app.bid import results
    return results.cell_detail(db, task, requirement_id, bidder_id)


@router.put("/{id}/signing")
def mark_signing(body: SigningInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """签章页速览的人工标注：已盖章 / 缺章 / 看不清，mark 为空即清除。"""
    from app.bid import results
    db.scalar(select(BidProject).where(BidProject.task_id == task.id).with_for_update().execution_options(populate_existing=True))
    marks = results.set_signing(db, task, body.file_id, body.page, body.loc, body.mark)
    task.last_activity_at = utcnow()
    db.commit()
    return {"marks": marks}


@router.get("/{id}/files/{fid}/page/{page}")
def page_image(fid: str, page: int, width: int = Query(320, ge=120, le=1600), task: Task = Depends(owned),
               db: Session = Depends(get_db)):
    """投标文件某一页的图片（签章页缩略图、查看原件页），服务器内存中生成，不缓存。"""
    from app.bid import results
    return Response(content=results.page_image(db, task, fid, page, width), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.post("/{id}/reopen")
def reopen(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """返回修改材料：退回“待开标”，重新开始初审时沿用已有的核对状态与备注。"""
    from app.bid import results
    task = db.scalar(select(Task).where(Task.id == task.id).with_for_update(key_share=True).execution_options(populate_existing=True))
    db.get(BidProject, task.id, populate_existing=True)
    results.reopen(db, task)
    task.last_activity_at = utcnow()
    db.commit()
    return service.detail(db, task)


# ---------------------------------------------------------------- 模型核对（V34-B-05）

@router.get("/{id}/model")
def model_view(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """片段确认页：将发送的片段（原文与 AI 视野）、要求与回执。"""
    from app.bid import model
    return model.view(db, task)


@router.post("/{id}/model/{action}")
def model_action(action: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """prepare 准备（重新准备）片段 → confirm 确认并开始模型核对 → cancel 放弃。项目材料已锁定，不再改清单。"""
    from app.bid import model
    if action not in ("prepare", "confirm", "cancel"):
        raise AppError(404, "NOT_FOUND", "接口不存在")
    task = db.scalar(select(Task).where(Task.id == task.id).with_for_update(key_share=True).execution_options(populate_existing=True))
    db.get(BidProject, task.id, populate_existing=True)
    {"prepare": model.start, "confirm": model.confirm, "cancel": model.cancel}[action](db, task)
    task.last_activity_at = utcnow()
    db.commit()
    return model.view(db, task)
