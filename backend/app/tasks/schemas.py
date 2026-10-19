"""任务接口的出参（SPEC 19.3，设计 docs/design/M2.md 第三节）。"""
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

# 接口时间一律为 ISO 8601 UTC（SPEC 19.1）；数据库连接的时区设置不同也不影响输出
UtcDatetime = Annotated[datetime, AfterValidator(lambda d: d.astimezone(timezone.utc))]


class TaskPatch(BaseModel):
    """重新审核（设计 docs/design/M5.md 第七节）：至少一项；user_note 出现且为空表示清除。"""
    stance: Literal["sales", "purchase", "other"] | None = None
    source: Literal["outgoing", "incoming"] | None = None
    user_note: str | None = Field(None, max_length=500)


class TaskCreated(BaseModel):
    id: uuid.UUID


class TaskListItem(BaseModel):
    id: uuid.UUID
    title: str
    doc_type: str | None
    stance: str
    status: str
    high_count: int
    created_at: UtcDatetime


class TaskPage(BaseModel):
    items: list[TaskListItem]
    total: int
    page: int
    page_size: int


class TaskFileOut(BaseModel):
    id: uuid.UUID
    seq: int
    is_main: bool
    original_name: str
    ext: str
    size_bytes: int
    doc_type: str | None
    doc_type_source: str | None
    page_count: int | None
    paragraph_count: int
    table_count: int
    has_revisions: bool
    warnings: list[str]


class PreviewNode(BaseModel):
    """预览节点（设计 M3 第七节）；text 为解密后的原文，保留期清理后为 None。"""

    loc: str
    kind: str
    part: str
    text: str | None
    clause_no: str | None
    page: int | None
    bbox: list[float] | None
    sheet: str | None
    cell_ref: str | None
    ocr_conf: float | None
    parent_loc: str | None


class PreviewNodes(BaseModel):
    text_available: bool
    items: list[PreviewNode]


class LatestRun(BaseModel):
    id: uuid.UUID
    run_no: int
    status: str


class TaskDetail(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    error_message: str | None
    stance: str
    source: str
    policy: str
    user_note: str | None
    created_at: UtcDatetime
    files: list[TaskFileOut]
    latest_run: LatestRun | None
    queue_position: int | None
