"""会话接口出入参。"""
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

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
    generating: bool = False


class SessionPage(BaseModel):
    items: list[SessionItem]
    total: int
    page: int
    page_size: int = 30
    kb_enabled: bool = False


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
    kb_enabled: bool = False


class MessageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    decision: Literal["replace", "keep"]
    category: Literal["ORG", "PERSON", "CUSTOM"] | None = None


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=4000)
    template: Literal["translate", "summary", "reply", "crosscheck", "compare"] | None = None
    params: dict = Field(default_factory=dict)
    decisions: list[MessageDecision] = Field(default_factory=list)
    use_kb: StrictBool = False
    use_history: StrictBool = False

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value):
        return value.strip() if isinstance(value, str) else value
