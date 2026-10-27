"""会话接口出入参。"""
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.tasks.schemas import TaskFileOut, UtcDatetime


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=60)
    stance: Literal["sales", "purchase", "other"] | None = None
    source: Literal["incoming", "outgoing"] | None = None


class SessionFile(TaskFileOut):
    confirmed_at: UtcDatetime | None


class SessionItem(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    file_count: int
    last_activity_at: UtcDatetime
    original_deleted: bool


class SessionPage(BaseModel):
    items: list[SessionItem]
    total: int
    page: int
    page_size: int = 30


class SessionDetail(SessionItem):
    stance: str
    source: str
    policy: str
    error_message: str | None
    created_at: UtcDatetime
    files: list[SessionFile]
    can_send: bool
    send_reason: str | None
    code_count: int
