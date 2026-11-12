"""会话接口出入参。"""
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from app.tasks.schemas import PreviewNode, TaskFileOut, TaskListItem, UtcDatetime
from app.parsing.model import strip_invisible


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=60)
    stance: Literal["sales", "purchase", "other"] | None = None
    source: Literal["incoming", "outgoing"] | None = None


class SessionFile(TaskFileOut):
    media: dict | None = None
    confirmed_at: UtcDatetime | None
    speakers: dict[str, str | None] | None = None


class SessionItem(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    file_count: int
    last_activity_at: UtcDatetime
    original_deleted: bool
    generating: bool = False
    purpose: Literal['chat', 'meeting', 'translate'] = 'chat'


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    purpose: Literal['chat', 'meeting', 'translate'] = 'chat'


class SessionPage(BaseModel):
    items: list[SessionItem]
    total: int
    page: int
    page_size: int = 30
    kb_enabled: bool = False
    asr_enabled: bool = False
    mt_enabled: bool = False


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
    participants: list[str] = Field(default_factory=list)
    asr_enabled: bool = False
    mt_enabled: bool = False
    material_version: int = 0
    review_running: bool = False  # 会话发起的审核排队或进行中（V32-A-02）


class AssistantPreviewNode(PreviewNode):
    speaker_code: str | None = None


class AssistantPreviewNodes(BaseModel):
    text_available: bool
    items: list[AssistantPreviewNode]


class MessageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    decision: Literal["replace", "keep"]
    category: Literal["ORG", "PERSON", "CUSTOM"] | None = None


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: int | None = Field(None, gt=0)
    message: str = Field(default="", max_length=4000)
    template: Literal["translate", "summary", "reply", "crosscheck", "compare", "minutes"] | None = None
    params: dict = Field(default_factory=dict)
    decisions: list[MessageDecision] = Field(default_factory=list)
    use_kb: StrictBool = False
    use_history: StrictBool = False
    expected_material_version: int | None = Field(None, ge=0)
    finding_id: uuid.UUID | None = None  # 按问题讨论：该问题必须属于本会话（V32-B-02）

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value):
        return strip_invisible(value).strip() if isinstance(value, str) else value


class HistoryItem(TaskListItem):
    """历史记录行（V32-B-01）：kind 区分工作台会话与旧审核任务；会话的 run_count 为已发起的审核轮数。"""
    kind: Literal['session', 'review']
    last_activity_at: UtcDatetime
    run_count: int = 0
    file_count: int = 0


class HistoryPage(BaseModel):
    items: list[HistoryItem]
    total: int
    page: int
    page_size: int
