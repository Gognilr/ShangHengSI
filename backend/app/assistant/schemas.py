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
    # 稿次（V35-D-02）：上一稿与第几稿；不在链上时为空
    prev_file_id: uuid.UUID | None = None
    draft_no: int | None = None


class SessionItem(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    file_count: int
    last_activity_at: UtcDatetime
    original_deleted: bool
    generating: bool = False
    purpose: Literal['chat', 'meeting', 'translate'] = 'chat'
    # 侧栏列表用（V321-E-03）：审核排队或进行中、已发起的轮数；单个会话详情里以 review_running 为准
    reviewing: bool = False
    run_count: int = 0
    # 会议列表的说明行（V35-F-08）：时长、发言人数、状态；只对会议会话给出
    meeting: dict | None = None


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
    parse_progress: dict | None = None  # 解析阶段：{phase, files: {文件 id: {stage, done, total}}}（V321-E-06）
    # 跨会话提示（V35-D-02）：本人近 30 天另一个会话的主文件名去掉稿次字样后相同：{session_id, title, file_name}
    draft_hint: dict | None = None


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
    kind: Literal['session', 'review', 'bid']
    purpose: Literal['chat', 'meeting', 'translate'] | None = None
    stage: str | None = None  # 标书初审项目的阶段（V34-B-07）；高风险为疑似否决项数，待处理为待核对的问题与线索数
    category: str | None = None  # 标书分类名（V34-B-01a）
    last_activity_at: UtcDatetime
    run_count: int = 0
    file_count: int = 0
    # 最新一轮（V321-E-04）：待处理问题数、可导出报告的轮次 id、审核排队或进行中；本轮高风险数沿用 high_count
    pending_count: int = 0
    latest_run_id: uuid.UUID | None = None
    reviewing: bool = False
    blocked: bool = False  # 最新一轮被发送前复检拦下（V35-F-03，工作台“待你处理”）


class HistoryPage(BaseModel):
    items: list[HistoryItem]
    total: int
    page: int
    page_size: int
