"""全部会话接口校验本人及 assistant kind，复用既有预览与删除。"""
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.assistant import service
from app.assistant.schemas import MessageIn, SessionDetail, SessionPage, SessionPatch
from app.db.models import Task, TaskFile, User
from app.db.session import get_db
from app.security.deps import _parse_id, current_user, load_owned_task
from app.security.errors import NotFoundError
from app.security.sessions import client_ip
from app.tasks import service as tasks
from app.tasks.schemas import PreviewNodes

router = APIRouter(prefix="/assistant/sessions")


def owned_session(id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Task:
    return load_owned_task(db, user, id, "assistant")


def owned_file(fid: str, task: Task = Depends(owned_session), db: Session = Depends(get_db)) -> TaskFile:
    file_id = _parse_id(fid)
    file = db.get(TaskFile, file_id) if file_id is not None else None
    if file is None or file.task_id != task.id:
        raise NotFoundError("FILE_NOT_FOUND", "文件不存在")
    return file


@router.post("", response_model=SessionDetail, status_code=201)
def create(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return service.create(db, user, client_ip(request))


@router.get("", response_model=SessionPage)
def list_sessions(q: str | None = Query(None, max_length=100), page: int = Query(1, ge=1, le=100000),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    return service.list_sessions(db, user, q, page)


@router.get("/{id}", response_model=SessionDetail)
def detail(task: Task = Depends(owned_session), db: Session = Depends(get_db)):
    return service.detail(db, task)


@router.patch("/{id}", response_model=SessionDetail)
def patch(body: SessionPatch, task: Task = Depends(owned_session), db: Session = Depends(get_db)):
    return service.patch(db, task, body)


@router.delete("/{id}", status_code=204)
def delete(request: Request, task: Task = Depends(owned_session), user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    task = service.lock(db, task)
    tasks.delete_task(db, task, user, client_ip(request))
    return Response(status_code=204)


@router.post("/{id}/files", response_model=SessionDetail)
def add_files(files: list[UploadFile] | None = File(None), task: Task = Depends(owned_session),
              db: Session = Depends(get_db)):
    raw = [(f.filename, f.file) for f in files or [] if f.filename]
    return service.add_files(db, task, raw)


@router.delete("/{id}/files/{fid}", response_model=SessionDetail)
def remove_file(file: TaskFile = Depends(owned_file), task: Task = Depends(owned_session), db: Session = Depends(get_db)):
    return service.remove_file(db, task, file)


@router.get("/{id}/files/{fid}/nodes", response_model=PreviewNodes)
def nodes(file: TaskFile = Depends(owned_file), task: Task = Depends(owned_session), db: Session = Depends(get_db)):
    return tasks.preview_nodes(db, task, file)


@router.get("/{id}/files/{fid}/raw")
def raw(file: TaskFile = Depends(owned_file)):
    data, media_type = tasks.load_raw(file)
    return Response(content=data, media_type=media_type, headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(file.original_name, safe='')}",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.post("/{id}/confirm", response_model=SessionDetail)
def confirm(request: Request, task: Task = Depends(owned_session), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return service.confirm(db, task, user, client_ip(request))


@router.post("/{id}/preview")
def preview(body: MessageIn, task: Task = Depends(owned_session), db: Session = Depends(get_db)):
    return service.preview(db, task, body)


@router.post("/{id}/messages", status_code=201)
def send(body: MessageIn, task: Task = Depends(owned_session), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return service.send(db, task, user, body)


@router.get("/{id}/messages")
def messages(after_id: int = Query(0, ge=0), task: Task = Depends(owned_session), db: Session = Depends(get_db)):
    return service.list_messages(db, task, after_id)


@router.get("/{id}/messages/{mid}")
def delta(mid: int, offset: int = Query(0, ge=0), task: Task = Depends(owned_session), db: Session = Depends(get_db)):
    return service.get_delta(db, task, mid, offset)


@router.post("/{id}/messages/{mid}/stop")
def stop(mid: int, task: Task = Depends(owned_session), db: Session = Depends(get_db)):
    return service.stop(db, task, mid)


@router.post("/{id}/messages/{mid}/retry", status_code=201)
def retry(mid: int, task: Task = Depends(owned_session), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return service.retry(db, task, user, mid)


@router.get("/{id}/messages/{mid}/export")
def export(mid: int, request: Request, format: Literal['docx', 'xlsx'] = 'docx',
           variant: Literal['translation', 'bilingual'] = 'translation', task: Task = Depends(owned_session),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.assistant.export import generate
    data, media, filename = generate(db, task, mid, format, variant, user, client_ip(request))
    return Response(content=data, media_type=media, headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
