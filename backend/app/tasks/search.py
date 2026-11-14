"""全局搜索：POST /api/v1/search（V3-A 6）。只查本人任务的标题；搜索词放请求体，不写审计也不记日志。"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DbSession

from app.db.models import Finding, Task, User
from app.db.session import get_db
from app.security.deps import current_user
from app.review.service import _View
from app.tasks.service import _like_pattern

router = APIRouter(prefix="/search")
LIMIT = 10


class SearchBody(BaseModel):
    q: str

    @field_validator("q")
    @classmethod
    def strip_length(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 50:
            raise ValueError("搜索词须为 1～50 个字符")
        return value


class ReviewHit(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    created_at: datetime


class SessionHit(BaseModel):
    id: uuid.UUID
    title: str
    last_activity_at: datetime


class FindingHit(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    task_title: str
    kind: str
    title: str


class SearchResult(BaseModel):
    reviews: list[ReviewHit]
    sessions: list[SessionHit]
    findings: list[FindingHit] = []


@router.post("", response_model=SearchResult)
def search(body: SearchBody, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> SearchResult:
    def find(kind: str) -> list[Task]:
        return list(db.scalars(
            select(Task).where(Task.owner_id == user.id, Task.kind == kind,
                               Task.title.ilike(_like_pattern(body.q), escape="\\"))
            .order_by(Task.last_activity_at.desc(), Task.id.desc()).limit(LIMIT)))

    # 问题标题与描述在库里是脱敏后的写法（代号），所以按主题词能搜到，按真实名称、金额搜不到；命中后只还原这几条的标题
    pattern = _like_pattern(body.q)
    rows = db.execute(
        select(Finding, Task).join(Task, Task.latest_run_id == Finding.run_id)
        .where(Task.owner_id == user.id,
               or_(Finding.title.ilike(pattern, escape="\\"), Finding.problem.ilike(pattern, escape="\\")))
        .order_by(Task.last_activity_at.desc(), Finding.seq).limit(LIMIT)).all()
    views: dict[uuid.UUID, _View] = {}
    hits = []
    for finding, task in rows:
        view = views.setdefault(task.id, _View(db, task.id))
        hits.append(FindingHit(id=finding.id, task_id=task.id, task_title=task.title, kind=task.kind, title=view.text(finding.title) or finding.title))
    return SearchResult(
        findings=hits,
        reviews=[ReviewHit(id=t.id, title=t.title, status=t.status, created_at=t.created_at) for t in find("review")],
        sessions=[SessionHit(id=t.id, title=t.title, last_activity_at=t.last_activity_at) for t in find("assistant")],
    )
