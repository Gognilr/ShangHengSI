"""标书项目接口；所有读写均需 use.bid，内容仍只本人可见。"""
import json

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import func, select, update as sql_update
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.bid import service
from app.bid.schemas import BidderInput, PasswordInput, ProjectInput
from app.db.base import utcnow
from app.db.models import BidBidder, BidProject, Task, TaskFile, User
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
def projects(page: int = Query(1, ge=1, le=100000), user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    where = (Task.owner_id == user.id, Task.kind == "bid")
    rows = db.execute(select(Task, BidProject).join(BidProject).where(*where)
                      .order_by(Task.last_activity_at.desc(), Task.id).offset((page - 1) * 20).limit(20)).all()
    active = db.scalar(select(func.count()).select_from(Task).join(BidProject).where(*where, BidProject.stage == "checking")) or 0
    return {"items": [{"id": t.id, "title": t.title, "stage": p.stage, "status": t.status,
                       "opening_date": p.opening_date, "last_activity_at": t.last_activity_at} for t, p in rows],
            "total": db.scalar(select(func.count()).select_from(Task).where(*where)), "checking_count": active}


@router.post("", status_code=201)
def create(body: ProjectInput, request: Request, user: User = Depends(require("use.bid")), db: Session = Depends(get_db)):
    task = Task(owner_id=user.id, kind="bid", title=body.title, stance="purchase", source="incoming",
                policy=body.policy, status="draft")
    db.add(task)
    db.flush()
    db.add(BidProject(task_id=task.id, opening_date=body.opening_date))
    audit.record(db, "task_create", user_id=user.id, target_type="task", target_id=str(task.id), detail={"kind": "bid"}, ip=client_ip(request))
    db.commit()
    return service.detail(db, task)


@router.get("/{id}")
def get(task: Task = Depends(owned), db: Session = Depends(get_db)):
    return service.detail(db, task)


@router.patch("/{id}")
def update(body: ProjectInput, task: Task = Depends(owned), db: Session = Depends(get_db)):
    task = service.editable(db, task)
    task.title, task.policy, task.last_activity_at = body.title, body.policy, utcnow()
    db.get(BidProject, task.id).opening_date = body.opening_date
    db.commit()
    return service.detail(db, task)


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
