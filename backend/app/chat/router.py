"""追问接口（SPEC 19.6）。任务一律经 owned_task 取得，非本人返回 404。"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session as DbSession

from app.chat import service
from app.db.models import Task, User
from app.db.session import get_db
from app.security.deps import current_user, owned_task

router = APIRouter()

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=service.MAX_CHARS)]


class ChatIn(BaseModel):
    message: Question


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ChatList(BaseModel):
    available: bool
    reason: str | None
    items: list[ChatMessageOut]


class ChatPreview(BaseModel):
    redacted: str
    changed: bool


class ChatReply(BaseModel):
    user: ChatMessageOut
    assistant: ChatMessageOut


@router.get("/tasks/{id}/chat", response_model=ChatList)
def get_chat(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> dict:
    return service.list_messages(db, task)


@router.post("/tasks/{id}/chat/preview", response_model=ChatPreview)
def preview_chat(body: ChatIn, task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> dict:
    return service.preview(db, task, body.message)


@router.post("/tasks/{id}/chat", response_model=ChatReply)
def send_chat(body: ChatIn, task: Task = Depends(owned_task), user: User = Depends(current_user),
              db: DbSession = Depends(get_db)) -> dict:
    return service.send(db, task, user, body.message)
