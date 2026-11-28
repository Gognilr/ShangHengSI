"""标书项目接口；所有读写均需 use.bid，内容仍只本人可见。"""
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import delete as sql_delete, func, select, update as sql_update
from sqlalchemy.orm import Session

from app import content_drafts
from app.audit import service as audit
from app.bid import folders, service
from app.bid.schemas import (BidderInput, DismissInput, FolderInput, FolderPatch, InviteesInput, OpeningPriceInput, PlaceInput,
                             PasswordInput, ProjectInput, RequirementInput, RequirementPatch, SigningBatchInput, SigningInput)
from app.db.base import utcnow
from app.db.models import BidItem, BidProject, BidRequirement, Task, TaskFile, User
from app.db.session import get_db
from app.parsing.pdf import PdfPasswordError, _open
from app.parsing.model import ParseError
from app.security import crypto
from app.security.deps import load_owned_task
from app.security.errors import AppError, NotFoundError
from app.security.permissions import require
from app.security.sessions import client_ip
from app.tasks import service as tasks, storage

router = APIRouter(prefix="/bid", dependencies=[Depends(require("use.bid"))])


def owned(id: str, user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    return load_owned_task(db, user, id, "bid")


def _lock_owned(db, task):
    locked = service.lock_task(db, task.id)
    if locked is None:
        raise NotFoundError("TASK_NOT_FOUND", "项目不存在")
    return locked


def _list_filters(q, category, stage, date_from, date_to):
    """列表与文件夹计数共用的筛选；文件夹选择与分页只影响右侧列表。"""
    where = []
    if q.strip():
        where.append(Task.title.ilike(tasks._like_pattern(q.strip()), escape="\\"))
    if category is not None:
        where.append(BidProject.category_id == category)
    if stage == "active":  # 分段控件“进行中 / 已完成”（V35-F-05）
        where.append(BidProject.stage != "checked")
    elif stage == "done":
        where.append(BidProject.stage == "checked")
    elif stage:
        where.append(BidProject.stage == stage)
    if date_from:
        where.append(BidProject.opening_date >= date_from)
    if date_to:
        where.append(BidProject.opening_date <= date_to)
    return where


@router.get("")
def projects(page: int = Query(1, ge=1, le=100000), q: str = Query("", max_length=100), category: int | None = None,
             folder: str | None = Query(None, max_length=40), stage: str | None = Query(None, pattern="^(preparing|ready|checking|checked|active|done)$"),
             date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
             opening_view: bool = False,
             user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    """项目列表与搜索筛选（V34-B-01a）：名称包含、分类、文件夹（含子文件夹；none 为未归档）、阶段、开标日期区间。"""
    base = (Task.owner_id == user.id, Task.kind == "bid")
    scope = [*base, *_list_filters(q, category, stage, None, None)]
    if folder == "none":
        scope.append(BidProject.folder_id.is_(None))
    elif folder:
        scope.append(BidProject.folder_id.in_(folders.subtree(db, user, folders.folder(db, user, folder).id)))
    where = [*scope, *_list_filters("", None, None, date_from, date_to)]
    order = [Task.last_activity_at.desc(), Task.id]
    ruler, states = {}, {}
    if opening_view:
        today = utcnow().astimezone(ZoneInfo("Asia/Shanghai")).date()
        # 先看距今天最近的开标日；同距时未过的开标日在前。
        opening = BidProject.opening_date
        order = [func.abs(opening - today), opening.desc(), *order]
        start, end = today - timedelta(days=7), today + timedelta(days=22)
        # 时间尺每个项目画一格，有严重问题的画红；结论与下面的列表行共用一次计算。
        days = {}
        for t, p in db.execute(select(Task, BidProject).join(BidProject).where(*scope, opening.between(start, end))):
            states[t.id] = _list_state(db, t, p)
            day = days.setdefault(p.opening_date, {"date": p.opening_date, "count": 0, "severe": 0})
            day["count"] += 1
            day["severe"] += 1 if states[t.id].get("severe") else 0
        ruler = {"opening_ruler": {"today": today, "start": start, "end": end, "days": [days[d] for d in sorted(days)]}}
    rows = db.execute(select(Task, BidProject).join(BidProject).where(*where)
                      .order_by(*order).offset((page - 1) * 20).limit(20)).all()
    active = db.scalar(select(func.count()).select_from(Task).join(BidProject).where(*base, BidProject.stage == "checking")) or 0
    from app.db.models import BidBidder
    ids = [t.id for t, _p in rows]
    counts = dict(db.execute(select(BidBidder.task_id, func.count()).where(BidBidder.task_id.in_(ids))
                             .group_by(BidBidder.task_id)).all()) if ids else {}
    return {"items": [{"id": t.id, "title": t.title, "stage": p.stage, "status": t.status, "opening_date": p.opening_date,
                       "last_activity_at": t.last_activity_at, "category_id": p.category_id, "folder_id": p.folder_id,
                       "bidder_count": counts.get(t.id, 0), "model_state": p.model_state,
                       **(states[t.id] if t.id in states else _list_state(db, t, p))} for t, p in rows],
            "total": db.scalar(select(func.count()).select_from(Task).join(BidProject).where(*where)),
            "checking_count": active, **ruler}


def _list_state(db, task, project):
    """列表行的状态徽标（澄明 8.5）：检查中给进度，已完成给严重条数；结论随“不是问题”、签章标注实时算。"""
    if project.stage == "checking":
        return {"progress": service.check_progress(project)}
    if project.stage == "checked" and project.check_state == "done":
        from app.bid import results
        view = results.check_view(db, task)
        c = view["conclusion"]
        return {"severe": len(c["severe"]), "hint": len(c["hint"]), "ocr_state": view["ocr_result"]["state"]}
    if project.check_state == "failed":
        return {"check_failed": True}
    return {}


@router.post("", status_code=201)
def create(body: ProjectInput, request: Request, user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    from app.bid.requirements import snapshot_company_checks

    category = folders.choose_category(db, body.category_id)
    folder_id = folders.folder(db, user, body.folder_id).id if body.folder_id else None
    task = Task(owner_id=user.id, kind="bid", title=body.title, stance="purchase", source="incoming",
                policy=body.policy, status="draft")
    db.add(task)
    db.flush()
    project = BidProject(task_id=task.id, opening_date=body.opening_date, category_id=category, folder_id=folder_id)
    snapshot_company_checks(db, task, project)
    db.add(project)
    audit.record(db, "task_create", user_id=user.id, target_type="task", target_id=str(task.id), detail={"kind": "bid"}, ip=client_ip(request))
    db.commit()
    return service.detail(db, task)


# ---------------------------------------------------------------- 文件夹与分类（V34-B-01a）；写在 /{id} 之前

@router.get("/folders")
def folder_tree(q: str = Query("", max_length=100), category: int | None = None,
                stage: str | None = Query(None, pattern="^(preparing|ready|checking|checked|active|done)$"),
                date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
                user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    return folders.tree(db, user, _list_filters(q, category, stage, date_from, date_to))


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
    """名称、开标日期、外发策略只在开始初审前可改；分类与文件夹任何阶段都可改（只改请求中给出的项）。
    例外：本地检查完成后可由“只在本机处理”改为“可选模型补查”，不必退回修改材料；补查仍须逐段确认后才发送。"""
    from app.bid import extract, model, requirements

    task = _lock_owned(db, task)
    project = db.get(BidProject, task.id, populate_existing=True)
    policy_changed = "policy" in body.model_fields_set and body.policy != task.policy
    if policy_changed:
        extract.require_idle(project)
        model.require_idle(project)
    meta_changed = (body.title, body.opening_date) != (task.title, project.opening_date)
    if meta_changed or (policy_changed and body.policy != "ai"):
        service.require_editable(project)
        task.title, project.opening_date = body.title, body.opening_date
    if policy_changed:
        if body.policy == "ai" and service.has_pending_model_job(db, task.id):
            raise AppError(409, "MODEL_JOBS_PENDING", "旧模型作业尚未收尾，请稍后再启用模型")
        task.policy = body.policy
        service.clear_model_work(db, project)
        if body.policy == "ai":
            # 只在本机处理时，本该交模型的 ★ / 否决条款记成了人工核对；改为可补查后恢复，补查卡才能列出它们
            for row in db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id, BidRequirement.source != "manual",
                                                               BidRequirement.method == "manual")):
                if requirements.default_method(row.group, crypto.decrypt_text(row.text_enc), "ai") == "model":
                    row.method = "model"
    if "category_id" in body.model_fields_set:
        category_id = folders.choose_category(db, body.category_id, project.category_id)
        if project.category_id != category_id:
            project.category_id = category_id
            if project.stage == "preparing":
                requirements.snapshot_company_checks(db, task, project)
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
    from app.bid import checks, model
    task = _lock_owned(db, task)
    project = db.get(BidProject, task.id, populate_existing=True)
    checked = project.stage == "checked"
    if checked:
        service.require_originals(db, task.id)
        model.require_idle(project)
    else:
        service.require_editable(project)
    row = service.bidder(db, task, bidder_id)
    service.require_unique_name(db, task, body.name, skip=row.id)
    row.name_enc = crypto.encrypt_text(body.name)
    if checked:
        db.flush()
        bidders, nodes, _files = checks._load(db, task.id)
        checks._replace(db, task.id, ("mixed",), checks.check_mixed([b for b in bidders if nodes.get(b.id)], nodes))
        model.cancel(db, task)
    task.last_activity_at = utcnow()
    db.commit()
    return service.detail(db, task)


@router.delete("/{id}/bidders/{bidder_id}")
def delete_bidder(bidder_id: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    task = service.editable(db, task)
    row = service.bidder(db, task, bidder_id)
    if db.scalar(select(TaskFile.id).where(TaskFile.bidder_id == row.id).limit(1)):
        raise AppError(409, "BIDDER_HAS_FILES", "请先移除此投标人的文件")
    db.execute(sql_delete(BidItem).where(BidItem.task_id == task.id, BidItem.bidder_ids.contains([str(row.id)])))
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
    password = crypto.decrypt_text(row.password_enc) if row.original_deleted_at is None and row.password_enc else None
    return Response(content=json.dumps({"password": password}),
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
    if task.policy == "ai" and data["extract_state"] == "awaiting" and data["receipt"] is None:
        data["extract_state"], data["extract_error"] = "failed", extract.STALE_PREVIEW
    data["stage"] = db.get(BidProject, task.id).stage
    return data


def _changed(db, task):
    """改清单或条款参数：版本加一；已确认（待开标）的项目保持待开标，开标后由检查任务只重跑受影响的行。"""
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


@router.post("/{id}/requirements/{rid}/company-reuse")
def share_company_requirement(rid: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid.requirements import requirement

    row = requirement(db, task, rid)
    db.refresh(row, with_for_update=True)
    if row.group == "format" or row.source == "default":
        raise AppError(422, "CONTENT_DRAFT_NOT_APPLICABLE", "请选择一条具体的招标要求")
    project = db.get(BidProject, task.id)
    result = content_drafts.publish(db, task, row.id, crypto.decrypt_text(row.text_enc), "bid",
                                    category_id=project.category_id, project_names=content_drafts.bid_names(db, task, project))
    db.commit()
    return result


@router.delete("/{id}/requirements/{rid}/company-reuse")
def withdraw_company_requirement(rid: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid.requirements import requirement

    row = requirement(db, task, rid)
    db.refresh(row, with_for_update=True)
    result = content_drafts.withdraw(db, "bid", task.owner_id, source_id=row.id)
    db.commit()
    return result


@router.put("/{id}/invitees")
def save_invitees(body: InviteesInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """受邀投标单位名单（V34-B-01b）：开始初审前可改；改过后重新抽取不再覆盖。"""
    from app.bid import invitees, requirements as R
    task = service.editable(db, task)
    project = db.get(BidProject, task.id)
    state = R.load_state(project)
    invitees.replace(state, body.names)
    R.save_state(project, state)
    task.last_activity_at = utcnow()
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
    method = R.policy_method(body.method, task.policy)
    R._add(db, task.id, R.next_seq(db, task.id), body.group, body.text, locs, method, "manual")
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
    row.method = R.policy_method(body.method or row.method, task.policy)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.param is not None:
        kind = R.param_kind(crypto.decrypt_text(row.text_enc)) if row.group in R.KEY_GROUPS else None
        if kind is None:
            raise AppError(422, "NO_PARAM", "只有 ★ 与否决条款中的工期、交货期、质保期、缺陷责任期与类似业绩可以填写参数")
        R.save_param(row, R.clean_param(kind, body.param))
        row.method = "local"
    _changed(db, task)
    db.commit()
    return _requirements(db, task)


@router.delete("/{id}/requirements/{rid}")
def delete_requirement(rid: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid import requirements as R
    task = service.editable(db, task)
    row = R.requirement(db, task, rid)
    R.delete_rows(db, BidRequirement.id == row.id)
    _changed(db, task)
    db.commit()
    return _requirements(db, task)


@router.post("/{id}/requirements/extract")
def reextract(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """重新规则抽取：手工加入的行保留，其余按当前招标文件重建。"""
    from app.bid import extract, requirements as R
    task = service.editable(db, task)
    extract.require_idle(db.get(BidProject, task.id))
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
    """确认条款清单：项目进入“待开标”。"""
    from app.bid import extract, requirements as R
    task = service.editable(db, task)
    project = db.get(BidProject, task.id)
    if not R.parsed_tender_ids(db, task.id):
        raise AppError(422, "NO_TENDER", "请先加入并解析招标文件")
    extract.require_idle(project)
    # 招标文件可以没有 ★ / 否决条款（V35-E-01）：串标与确定性检查照常进行
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
    from app.bid import extract
    from app.db import jobs
    task = service.editable(db, task)
    project = db.get(BidProject, task.id)
    extract.require_idle(project)
    if project.stage != "ready":
        raise AppError(409, "NOT_READY", "请先确认条款清单")
    service.require_originals(db, task.id)
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


@router.post("/{id}/check/refresh")
def refresh_check(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """补算失败时重新排队，保留当前结果和模型补查。"""
    from app.db import jobs
    task = _lock_owned(db, task)
    project = db.get(BidProject, task.id, populate_existing=True)
    service.require_originals(db, task.id)
    if project.stage != "checked" or project.check_error != service.OCR_RECHECK_FAILED:
        raise AppError(409, "NO_CHECK_REFRESH", "当前结论无需重试更新")
    project.check_error = None
    jobs.enqueue(db, "bid", {"task_id": str(task.id), "stage": "ocr"})
    db.commit()
    return service.detail(db, task)


@router.get("/{id}/check")
def check_result(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """初审结果：严重 / 提示两档的问题清单、条目、单元格、条款、签章页速览与覆盖说明。"""
    from app.bid import results
    return results.check_view(db, task)


@router.get("/{id}/report", dependencies=[Depends(require("use.export"))])
def report(scope: str = Query("full", pattern="^(full|clues)$"), task: Task = Depends(owned),
           user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    """Word 问题清单（服务器内存生成、不留存）：full 全部，clues 只含串标线索。初审完成后可导出。"""
    from app.export.bid_docx import build_bid_report
    from app.export.router import _response
    if db.get(BidProject, task.id).stage != "checked":
        raise AppError(409, "REPORT_UNAVAILABLE", "初审完成后才能导出报告")
    filename, content = build_bid_report(db, task, user, scope)
    audit.record(db, "report_export", user_id=user.id, target_type="task", target_id=str(task.id), detail={"kind": "bid", "scope": scope})
    db.commit()
    return _response(filename, content)


@router.get("/{id}/cell")
def cell_detail(requirement_id: str, bidder_id: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """条款证据：条款原文与出处、该家对应片段（含上下文）与判断理由。"""
    from app.bid import results
    return results.cell_detail(db, task, requirement_id, bidder_id)


@router.post("/{id}/signing/batch")
def mark_signing_batch(body: SigningBatchInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """签章页批量标注（本家全部已盖章）：一次事务。"""
    from app.bid import results
    task = _lock_owned(db, task)
    db.get(BidProject, task.id, populate_existing=True, with_for_update=True)
    marks = results.set_signing_many(db, task, [(s.file_id, s.page, s.loc) for s in body.signing], body.mark)
    task.last_activity_at = utcnow()
    db.commit()
    return {"marks": marks}


@router.put("/{id}/signing")
def mark_signing(body: SigningInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """签章页速览的人工标注：已盖章 / 缺章 / 看不清，mark 为空即清除。"""
    from app.bid import results
    task = _lock_owned(db, task)
    db.get(BidProject, task.id, populate_existing=True, with_for_update=True)
    marks = results.set_signing(db, task, body.file_id, body.page, body.loc, body.mark)
    task.last_activity_at = utcnow()
    db.commit()
    return {"marks": marks}


@router.get("/{id}/files/{fid}/page/{page}")
def page_image(fid: str, page: int, width: int = Query(320, ge=120, le=1600), loc: str | None = Query(None, max_length=64),
               task: Task = Depends(owned), db: Session = Depends(get_db)):
    """投标文件某一页的图片（签章页缩略图、查看原件页、证据栏）；loc 为要框出的命中节点。服务器内存中生成，不缓存。"""
    from app.bid import results
    return Response(content=results.page_image(db, task, fid, page, width, loc), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.post("/{id}/reopen")
def reopen(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """返回修改材料：退回“待开标”，重新开始初审时沿用已标的“不是问题”。"""
    from app.bid import results
    task = _lock_owned(db, task)
    results.reopen(db, task)
    task.last_activity_at = utcnow()
    db.commit()
    return service.detail(db, task)


# ---------------------------------------------------------------- 模型补查（V34-B-05；V35-E-01 改为可选）

def _locked_result(db, task):
    from app.bid import model
    task = _lock_owned(db, task)
    project = db.get(BidProject, task.id, populate_existing=True)
    if project.stage != "checked":
        raise AppError(409, "NOT_CHECKED", "请等待本地检查完成")
    model.require_idle(project)
    return task, project


@router.put("/{id}/bidders/{bidder_id}/opening-price")
def opening_price(bidder_id: str, body: OpeningPriceInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """唱标价（V34-B-06e）：除初审中外各阶段都可录入；检查完成后只重跑报价与确定性符合项。审计与日志不写金额。"""
    from app.bid import checks, model, results
    task = _lock_owned(db, task)
    project = db.get(BidProject, task.id, populate_existing=True)
    if project.stage == "checking":
        raise AppError(409, "BID_CHECKING", "初审进行中，请完成后再录入唱标价")
    bidder = service.bidder(db, task, bidder_id)
    value = {"amount": body.amount, "upper": body.upper, "note": body.note} if body.amount else None
    bidder.opening_price_enc = crypto.encrypt_text(json.dumps(value, ensure_ascii=False)) if value else None
    if project.stage == "checked":
        model.require_idle(project)
        service.require_originals(db, task.id)
        bidders, nodes, files = checks._load(db, task.id)
        active = [b for b in bidders if nodes.get(b.id) or b.opening_price_enc]
        items, _notes = checks._pricing_step(db, task, active, nodes, files, quote_only=True)
        checks._replace(db, task.id, checks.QUOTE_TYPES, items)
    task.last_activity_at = utcnow()
    db.commit()
    return results.check_view(db, task) if project.stage == "checked" else service.detail(db, task)


@router.put("/{id}/dismissed")
def dismiss(body: DismissInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """“不是问题”：标记或撤销；初审完成后可用。"""
    from app.bid import results
    task = _lock_owned(db, task)
    if db.get(BidProject, task.id, populate_existing=True).stage != "checked":
        raise AppError(409, "NOT_CHECKED", "请等待本地检查完成")
    results.dismiss(db, task, body.key, body.dismissed)
    task.last_activity_at = utcnow()
    db.commit()
    return results.check_view(db, task)


@router.get("/{id}/sent")
def sent(response: Response, task: Task = Depends(owned), db: Session = Depends(get_db)):
    from app.bid import results
    response.headers['Cache-Control'] = 'no-store'
    return results.sent_view(db, task)

@router.get("/{id}/model")
def model_view(task: Task = Depends(owned), db: Session = Depends(get_db)):
    """片段确认页：将发送的片段（原文与 AI 视野）、条款与回执。"""
    from app.bid import model
    return model.view(db, task)


@router.post("/{id}/model/{action}")
def model_action(action: str, task: Task = Depends(owned), db: Session = Depends(get_db)):
    """prepare 准备（重新准备）片段 → confirm 确认并开始模型补查 → cancel 放弃。项目材料已锁定，不再改清单。"""
    from app.bid import model
    if action not in ("prepare", "confirm", "cancel"):
        raise AppError(404, "NOT_FOUND", "接口不存在")
    task = _lock_owned(db, task)
    db.get(BidProject, task.id, populate_existing=True)
    {"prepare": model.start, "confirm": model.confirm, "cancel": model.cancel}[action](db, task)
    task.last_activity_at = utcnow()
    db.commit()
    return model.view(db, task)
