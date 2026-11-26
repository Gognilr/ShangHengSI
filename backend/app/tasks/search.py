"""全局搜索：POST /api/v1/search（V3-A 6）。只查本人任务的标题；搜索词放请求体，不写审计也不记日志。"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session as DbSession

from app.assistant.service import _latest_run_status, _translation_state, _translation_states, review_session_clause
from app.bid import service as bid_service
from app.db.models import Finding, Task, User
from app.db.session import get_db
from app.security.deps import current_user
from app.security.permissions import user_permissions
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
    # 往返线（V36-F-01）：外发策略与最新一轮的状态
    policy: str = "ai"
    run_status: str | None = None


class SessionHit(BaseModel):
    id: uuid.UUID
    title: str
    purpose: str
    last_activity_at: datetime
    is_review: bool = False
    # 往返线（V36-F-01）：任务状态、外发策略、最新一轮、翻译方式与状态
    status: str = "active"
    policy: str = "ai"
    run_status: str | None = None
    translation_engine: str | None = None
    translation_status: str | None = None


class FindingHit(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    task_title: str
    kind: str
    title: str


class BidHit(BaseModel):
    id: uuid.UUID
    title: str
    last_activity_at: datetime
    # 往返线（V36-F-01）：解析状态、阶段、模型补查状态
    status: str = "active"
    stage: str = "preparing"
    model_state: str | None = None


class SearchResult(BaseModel):
    reviews: list[ReviewHit]
    sessions: list[SessionHit]
    findings: list[FindingHit] = []
    bids: list[BidHit] = []  # 标书初审项目（V34-B-07）；只有 use.bid 的账号才会搜到


@router.post("", response_model=SearchResult)
def search(body: SearchBody, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> SearchResult:
    perms = user_permissions(db, user.id)
    can_review = "use.review" in perms

    def find(kind: str, *conditions) -> list[Task]:
        return list(db.scalars(
            select(Task).where(Task.owner_id == user.id, Task.kind == kind, *conditions,
                               Task.title.ilike(_like_pattern(body.q), escape="\\"))
            .order_by(Task.last_activity_at.desc(), Task.id.desc()).limit(LIMIT)))

    # 问题标题与描述在库里是脱敏后的写法（代号），所以按主题词能搜到，按真实名称、金额搜不到；命中后只还原这几条的标题
    pattern = _like_pattern(body.q)
    rows = db.execute(
        select(Finding, Task).join(Task, Task.latest_run_id == Finding.run_id)
        .where(Task.owner_id == user.id,
               or_(Finding.title.ilike(pattern, escape="\\"), Finding.problem.ilike(pattern, escape="\\")))
        .order_by(Task.last_activity_at.desc(), Finding.seq).limit(LIMIT)).all() if can_review else []
    views: dict[uuid.UUID, _View] = {}
    hits = []
    for finding, task in rows:
        view = views.setdefault(task.id, _View(db, task.id))
        hits.append(FindingHit(id=finding.id, task_id=task.id, task_title=task.title, kind=task.kind, title=view.text(finding.title) or finding.title))
    purposes = ["translate"]
    if "use.meeting" in perms:
        purposes.append("meeting")
    if "use.assistant" in perms:
        purposes.append("chat")
    reviewed = review_session_clause() if can_review else false()
    session_rows = db.execute(
        select(Task, reviewed.label("is_review")).where(
            Task.owner_id == user.id, Task.kind == "assistant",
            Task.title.ilike(pattern, escape="\\"),
            or_(Task.purpose.in_(purposes), reviewed),
        ).order_by(Task.last_activity_at.desc(), Task.id.desc()).limit(LIMIT))
    bid_rows = find("bid") if "use.bid" in perms else []
    bids = bid_service.history_stats(db, [t.id for t in bid_rows]) if bid_rows else {}
    review_rows = find("review") if can_review else []
    session_rows = list(session_rows)
    runs = _latest_run_status(db, review_rows + [t for t, _ in session_rows])
    translations = _translation_states(db, [t for t, _ in session_rows])
    return SearchResult(
        findings=hits,
        bids=[BidHit(id=t.id, title=t.title, last_activity_at=t.last_activity_at, status=t.status,
                     stage=bids[t.id][0], model_state=bids[t.id][4]) for t in bid_rows],
        reviews=[ReviewHit(id=t.id, title=t.title, status=t.status, created_at=t.created_at, policy=t.policy,
                           run_status=runs.get(t.id)) for t in review_rows],
        sessions=[SessionHit(id=t.id, title=t.title, purpose=t.purpose or "chat",
                             last_activity_at=t.last_activity_at, is_review=is_review, status=t.status, policy=t.policy,
                             run_status=runs.get(t.id), **_translation_state(t, translations.get(t.id)))
                  for t, is_review in session_rows],
    )
