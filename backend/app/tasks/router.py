"""任务接口：/api/v1/tasks（SPEC 19.3，设计 docs/design/M2.md 第三节）。"""
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from pydantic import BaseModel
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.db.models import Task, TaskFile, User
from app.db.session import get_db
from app.security.deps import current_user, owned_task, owned_task_file
from app.security.errors import AppError, NotFoundError
from app.security.sessions import client_ip
from app.tasks import service
from app.tasks.schemas import PreviewNodes, TaskCreated, TaskDetail, TaskFileOut, TaskPage, TaskPatch

router = APIRouter(prefix="/tasks")


class FileTypePatch(BaseModel):
    doc_type: Literal["contract", "quotation", "order", "inquiry", "letter"]


@router.patch("/{id}/files/{fid}", response_model=TaskFileOut)
def patch_file_type(
    patch: FileTypePatch,
    file: TaskFile = Depends(owned_task_file),
    task: Task = Depends(owned_task),
    db: DbSession = Depends(get_db),
) -> TaskFileOut:
    # Q-50：与确认发送共用任务行锁，等待锁后刷新状态。
    locked = db.scalar(select(Task).where(Task.id == task.id).with_for_update()
                       .execution_options(populate_existing=True))
    if locked is None:
        raise NotFoundError("TASK_NOT_FOUND", "任务不存在")
    if locked.status != "awaiting_confirm":
        raise AppError(409, "TASK_STATE_CONFLICT", "当前任务状态不允许修改")
    file.doc_type = patch.doc_type
    file.doc_type_source = "user"
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
    note = user_note.strip() if user_note and user_note.strip() else None
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
    )
    return TaskCreated(id=task.id)


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
