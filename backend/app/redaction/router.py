"""脱敏确认接口（SPEC 19.4，设计 docs/design/M2.md 第九节、docs/design/M4.md 第五节）。

修改类接口（PUT /parties、疑似遗漏决定、手动补充）与确认发送共用任务行锁，取得锁后重读状态，只在 awaiting_confirm 时允许。
返回的疑似遗漏文字和上下文是本人的原文，只用于第二步显示；日志不写任何文字，不写审计日志（Q-70 ⑥）。
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictBool, StrictInt
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.db.models import Party, RedactionItem, Task, User
from app.db.session import get_db
from app.redaction import engine
from app.security.deps import current_user, owned_task
from app.security.errors import AppError, NotFoundError
from app.audit import service as audit

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
    can_revoke: bool


class SuspectView(BaseModel):
    id: int
    loc: str
    reason: str
    decision: str
    category: str | None
    text: str
    before: str
    after: str


class RedactionOut(BaseModel):
    summary: dict[str, int]
    suspects: list[SuspectView]
    items: list[RedactionItemOut]


UserCategory = Literal["ORG", "PERSON", "IM", "ACCOUNT", "IDNO", "REFNO", "ADDR", "AMOUNT", "PROJECT", "CUSTOM"]


class SuspectDecision(BaseModel):
    decision: Literal["replace", "ignore"]
    category: UserCategory | None = None


class ManualRedaction(BaseModel):
    loc: str = Field(min_length=1, max_length=64)
    char_start: StrictInt
    char_end: StrictInt
    category: UserCategory


class RevokeRedaction(BaseModel):
    loc: str = Field(min_length=1, max_length=64)
    char_start: StrictInt
    char_end: StrictInt


class OutboundOut(BaseModel):
    text: str
    note: str | None


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


def _lock_awaiting(db: DbSession, task: Task) -> Task:
    """与确认发送共用任务行锁，等待后重读状态，防止已开始审核时继续修改。"""
    locked = db.scalar(select(Task).where(Task.id == task.id).with_for_update()
                       .execution_options(populate_existing=True))
    if locked is None:
        raise NotFoundError("TASK_NOT_FOUND", "任务不存在")
    if locked.status != "awaiting_confirm":
        raise AppError(409, "TASK_STATE_CONFLICT", "当前任务状态不允许修改")
    return locked


def _input_error(exc: engine.RedactionInputError) -> AppError:
    if exc.status == 404:
        return NotFoundError(exc.code, exc.message)
    return AppError(exc.status, exc.code, exc.message)


@router.put("/parties", response_model=PartyList)
def put_parties(
    body: PartyUpdates, task: Task = Depends(owned_task), db: DbSession = Depends(get_db),
) -> PartyList:
    locked = _lock_awaiting(db, task)
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


def _redaction(db: DbSession, task: Task) -> RedactionOut:
    rows = db.scalars(select(RedactionItem).where(RedactionItem.task_id == task.id)
                      .order_by(RedactionItem.loc, RedactionItem.char_start, RedactionItem.id))
    return RedactionOut(
        summary=engine.redaction_summary(db, task),
        suspects=[SuspectView(**v) for v in engine.suspect_views(db, task)],
        items=[RedactionItemOut(loc=r.loc, char_start=r.char_start, char_end=r.char_end,
                                category=r.category, placeholder=r.placeholder,
                                can_revoke=task.status == 'awaiting_confirm' and
                                (r.detected_by == 'user' or r.detected_by == 'person' and r.category == 'PERSON'))
               for r in rows],
    )


@router.get("/redaction", response_model=RedactionOut)
def get_redaction(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> RedactionOut:
    return _redaction(db, task)


_MAX_ID = 2 ** 63 - 1


@router.post("/redaction/suspects/{sid}", response_model=RedactionOut)
def decide_suspect(
    sid: str, body: SuspectDecision, task: Task = Depends(owned_task), user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> RedactionOut:
    locked = _lock_awaiting(db, task)
    suspect_id = int(sid) if sid.isascii() and sid.isdigit() and int(sid) <= _MAX_ID else None
    if suspect_id is None:
        raise NotFoundError("SUSPECT_NOT_FOUND", "疑似遗漏不存在")
    try:
        engine.decide_suspect(db, locked, suspect_id, decision=body.decision, category=body.category, user_id=user.id)
    except engine.RedactionInputError as exc:
        db.rollback()  # 不保留本次的任何修改（也释放任务行锁）
        raise _input_error(exc) from None
    result = _redaction(db, locked)
    db.commit()
    return result


@router.post("/redaction/manual", response_model=RedactionOut)
def manual_redaction(
    body: ManualRedaction, task: Task = Depends(owned_task), db: DbSession = Depends(get_db),
) -> RedactionOut:
    locked = _lock_awaiting(db, task)
    try:
        engine.add_user_term(db, locked, loc=body.loc, char_start=body.char_start, char_end=body.char_end,
                             category=body.category)
    except engine.RedactionInputError as exc:
        db.rollback()  # 不保留本次的任何修改（也释放任务行锁）
        raise _input_error(exc) from None
    result = _redaction(db, locked)
    db.commit()
    return result


@router.get("/redaction/outbound", response_model=OutboundOut)
def get_outbound(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> OutboundOut:
    return OutboundOut(text=engine.build_outbound_text(db, task), note=task.user_note_redacted)


@router.post("/redaction/revoke", response_model=RedactionOut)
def revoke_redaction(body: RevokeRedaction, task: Task = Depends(owned_task), user: User = Depends(current_user),
                     db: DbSession = Depends(get_db)) -> RedactionOut:
    locked = _lock_awaiting(db, task)
    try:
        category = engine.revoke_redaction(db, locked, loc=body.loc, char_start=body.char_start,
                                           char_end=body.char_end, user_id=user.id)
    except engine.RedactionInputError as exc:
        db.rollback()
        raise _input_error(exc) from None
    audit.record(db, "redaction_revoke", user_id=user.id, target_type="task", target_id=str(task.id),
                 detail={"category": category})
    result = _redaction(db, locked)
    db.commit()
    return result
