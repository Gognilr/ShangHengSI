"""任务接口：/api/v1/tasks（SPEC 19.3，设计 docs/design/M2.md 第三节）。"""
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field, StrictBool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.parsing.model import strip_invisible
from app.db.models import Task, TaskFile, User
from app.db.session import get_db
from app.security.deps import current_user, owned_task, owned_task_file
from app.security.errors import AppError, NotFoundError
from app.security.sessions import client_ip
from app.tasks import service
from app.tasks.schemas import PreviewNodes, TaskCreated, TaskDetail, TaskFileOut, TaskPage, TaskPatch

router = APIRouter(prefix="/tasks")


class FileTypePatch(BaseModel):
    doc_type: Literal["contract", "quotation", "order", "inquiry", "letter"] | None = None
    is_main: StrictBool | None = None


class ProceedIn(BaseModel):
    stance: Literal['sales', 'purchase', 'other']
    source: Literal['outgoing', 'incoming']
    policy: Literal['ai', 'local_only']
    user_note: str | None = Field(None, max_length=500)


@router.patch("/{id}/files/{fid}", response_model=TaskFileOut)
def patch_file_type(
    patch: FileTypePatch,
    file: TaskFile = Depends(owned_task_file),
    task: Task = Depends(owned_task),
    db: DbSession = Depends(get_db),
) -> TaskFileOut:
    # Q-50：与确认发送共用任务行锁，等待锁后刷新状态。
    if not patch.model_fields_set or ('doc_type' in patch.model_fields_set and patch.doc_type is None) or (
            'is_main' in patch.model_fields_set and patch.is_main is not True):
        raise AppError(422, 'VALIDATION_ERROR', '请设置文件类型或主文件')
    locked = service.lock_files(db, task, main_change=patch.is_main is True)
    file = db.scalar(select(TaskFile).where(TaskFile.id == file.id, TaskFile.task_id == locked.id)
                     .execution_options(populate_existing=True))
    if file is None:
        raise NotFoundError('FILE_NOT_FOUND', '文件不存在')
    if patch.doc_type is not None:
        file.doc_type, file.doc_type_source = patch.doc_type, 'user'
    if patch.is_main:
        for row in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id)):
            row.is_main = row.id == file.id
        locked.title = file.original_name
        from app.db import jobs
        jobs.enqueue(db, 'parse', {'task_id': str(task.id), 'file_ids': []})
    db.flush()
    result = next(f for f in service.task_detail(db, locked).files if f.id == file.id)
    db.commit()
    return result


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TaskCreated)
def create_task(
    request: Request,
    files: list[UploadFile] | None = File(None),
    main_index: int = Form(0),
    stance: Literal["sales", "purchase", "other"] = Form(...),
    source: Literal["outgoing", "incoming"] = Form(...),
    policy: Literal["ai", "local_only"] = Form("ai"),
    user_note: str | None = Form(None, max_length=500),
    allow_duplicate: bool = Form(False),
    draft: bool = Form(False),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> TaskCreated:
    # 浏览器在没选文件时会提交一个文件名为空的部分，当作没有文件
    raw = [(f.filename, f.file) for f in (files or []) if f.filename]
    uploads = service.check_uploads(raw, main_index, get_settings().max_upload_mb * 1024 * 1024)
    if not allow_duplicate:
        duplicate = service.duplicate_task(db, user, uploads[main_index])
        if duplicate is not None:
            return JSONResponse(status_code=409, content=jsonable_encoder({
                "code": "DUPLICATE_FILE", "message": "你已上传过同一文件，请选择打开上次结果或继续新建",
                "task_id": duplicate.id, "title": duplicate.title, "created_at": duplicate.created_at,
            }))
    note = strip_invisible(user_note or "").strip() or None
    task = service.create_task(
        db,
        user,
        uploads,
        main_index=main_index,
        stance=stance,
        source=source,
        policy=policy,
        user_note=note,
        ip=client_ip(request),
        draft=draft,
    )
    return TaskCreated(id=task.id)


@router.post('/{id}/files', response_model=TaskDetail)
def add_task_files(files: list[UploadFile] = File(...), task: Task = Depends(owned_task),
                   db: DbSession = Depends(get_db)) -> TaskDetail:
    uploads = service.check_uploads([(f.filename, f.file) for f in files if f.filename], 0,
                                    get_settings().max_upload_mb * 1024 * 1024)
    return service.add_files(db, task, uploads)


@router.delete('/{id}/files/{fid}', response_model=TaskDetail)
def delete_task_file(request: Request, file: TaskFile = Depends(owned_task_file), task: Task = Depends(owned_task),
                     user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> TaskDetail:
    return service.delete_file(db, task, file, user, client_ip(request))


@router.post('/{id}/proceed', response_model=TaskDetail)
def proceed_task(body: ProceedIn, task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> TaskDetail:
    body.user_note = strip_invisible(body.user_note or '').strip() or None
    return service.proceed(db, task, body)


@router.get("", response_model=TaskPage)
def list_tasks(
    page: int = Query(1, ge=1, le=100000),
    q: str | None = Query(None, max_length=100),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> TaskPage:
    return service.list_tasks(db, user, page=page, q=q, date_from=date_from, date_to=date_to)


@router.get("/{id}/files/{fid}/nodes", response_model=PreviewNodes)
def get_file_nodes(
    file: TaskFile = Depends(owned_task_file),
    task: Task = Depends(owned_task),
    db: DbSession = Depends(get_db),
) -> PreviewNodes:
    return service.preview_nodes(db, task, file)


@router.get("/{id}/files/{fid}/raw")
def get_file_raw(file: TaskFile = Depends(owned_task_file)) -> Response:
    # Q-63：只读预览，不写审计；日志不写文件名
    data, media_type = service.load_raw(file)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(file.original_name, safe='')}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get("/{id}", response_model=TaskDetail)
def get_task(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> TaskDetail:
    return service.task_detail(db, task)


@router.patch("/{id}", response_model=TaskDetail)
def patch_task(patch: TaskPatch, task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> TaskDetail:
    if patch.user_note is not None:
        patch.user_note = strip_invisible(patch.user_note).strip() or None
    return service.patch_task(db, task, patch)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    request: Request,
    task: Task = Depends(owned_task),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> Response:
    service.delete_task(db, task, user, client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
