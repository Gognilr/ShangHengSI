"""脱敏确认接口（SPEC 19.4，设计九）。M2 的疑似遗漏列表恒为空。"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.db.models import Party, RedactionItem, Task
from app.db.session import get_db
from app.redaction import engine
from app.security.deps import owned_task
from app.security.errors import AppError, NotFoundError

router = APIRouter(prefix="/tasks/{id}")


class PartyOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    role_label: str
    country: str | None
    org_type: str | None
    is_our_party: bool


class PartyList(BaseModel):
    items: list[PartyOut]


class PartyUpdate(BaseModel):
    id: uuid.UUID
    is_our_party: StrictBool
    country: str = Field(min_length=1, max_length=50)
    org_type: str = Field(min_length=1, max_length=20)


class PartyUpdates(BaseModel):
    items: list[PartyUpdate]


class RedactionItemOut(BaseModel):
    loc: str
    char_start: int
    char_end: int
    category: str
    placeholder: str


class RedactionOut(BaseModel):
    summary: dict[str, int]
    suspects: list[dict]
    items: list[RedactionItemOut]


class OutboundOut(BaseModel):
    text: str


def _party_list(db: DbSession, task: Task) -> PartyList:
    mapping = engine.load_redaction_map(db, task.id)
    return PartyList(items=[
        PartyOut(id=p.id, code=p.code, name=mapping[p.code], role_label=p.role_label,
                 country=p.country, org_type=p.org_type, is_our_party=p.is_our_party)
        for p in db.scalars(select(Party).where(Party.task_id == task.id).order_by(Party.code))
    ])


@router.get("/parties", response_model=PartyList)
def get_parties(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> PartyList:
    return _party_list(db, task)


@router.put("/parties", response_model=PartyList)
def put_parties(
    body: PartyUpdates, task: Task = Depends(owned_task), db: DbSession = Depends(get_db),
) -> PartyList:
    # 与确认发送共用任务行锁，等待后重读状态，防止已开始审核时继续修改。
    locked = db.scalar(select(Task).where(Task.id == task.id).with_for_update()
                       .execution_options(populate_existing=True))
    if locked is None:
        raise NotFoundError("TASK_NOT_FOUND", "任务不存在")
    if locked.status != "awaiting_confirm":
        raise AppError(409, "TASK_STATE_CONFLICT", "当前任务状态不允许修改")
    if not body.items:
        raise AppError(422, "OUR_PARTY_REQUIRED", "必须恰好一方为我方")
    ids = [p.id for p in body.items]
    existing_ids = set(db.scalars(select(Party.id).where(Party.task_id == task.id)))
    # Q-50：PUT 提交完整列表；重复、遗漏、其他任务的 ID 统一拒绝，不透露归属。
    if len(set(ids)) != len(ids) or set(ids) != existing_ids:
        raise AppError(422, "VALIDATION_ERROR", "签约方列表不正确")
    if sum(p.is_our_party for p in body.items) != 1:
        raise AppError(422, "OUR_PARTY_REQUIRED", "必须恰好一方为我方")
    # Q-50：国家、类型只约束存储长度和非空，不额外增加词典枚举限制。
    if any(not p.country.strip() or not p.org_type.strip() for p in body.items):
        raise AppError(422, "VALIDATION_ERROR", "国家和类型不能为空")
    engine.apply_party_changes(db, locked, [
        engine.PartyChange(party_id=p.id, is_our_party=p.is_our_party,
                           country=p.country.strip(), org_type=p.org_type.strip())
        for p in body.items
    ])
    result = _party_list(db, locked)
    db.commit()
    return result


@router.get("/redaction", response_model=RedactionOut)
def get_redaction(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> RedactionOut:
    rows = db.scalars(select(RedactionItem).where(RedactionItem.task_id == task.id)
                      .order_by(RedactionItem.loc, RedactionItem.char_start, RedactionItem.id))
    return RedactionOut(summary=engine.redaction_summary(db, task), suspects=[], items=[
        RedactionItemOut(loc=r.loc, char_start=r.char_start, char_end=r.char_end,
                         category=r.category, placeholder=r.placeholder) for r in rows
    ])


@router.get("/redaction/outbound", response_model=OutboundOut)
def get_outbound(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> OutboundOut:
    return OutboundOut(text=engine.build_outbound_text(db, task))
