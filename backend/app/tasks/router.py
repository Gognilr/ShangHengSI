"""任务接口：/api/v1/tasks（SPEC 19.3，设计 docs/design/M2.md 第三节）。

PATCH /tasks/{id}/files/{fid} 在 M2-13 实现。
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.db.models import Task, User
from app.db.session import get_db
from app.security.deps import current_user, owned_task
from app.security.sessions import client_ip
from app.tasks import service
from app.tasks.schemas import TaskCreated, TaskDetail, TaskPage

router = APIRouter(prefix="/tasks")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TaskCreated)
def create_task(
    request: Request,
    files: list[UploadFile] | None = File(None),
    main_index: int = Form(0),
    stance: Literal["sales", "purchase", "other"] = Form(...),
    source: Literal["outgoing", "incoming"] = Form(...),
    policy: Literal["ai", "local_only"] = Form("ai"),
    user_note: str | None = Form(None, max_length=500),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> TaskCreated:
    # 浏览器在没选文件时会提交一个文件名为空的部分，当作没有文件
    raw = [(f.filename, f.file) for f in (files or []) if f.filename]
    uploads = service.check_uploads(raw, main_index, get_settings().max_upload_mb * 1024 * 1024)
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


@router.get("/{id}", response_model=TaskDetail)
def get_task(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> TaskDetail:
    return service.task_detail(db, task)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    request: Request,
    task: Task = Depends(owned_task),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> Response:
    service.delete_task(db, task, user, client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
