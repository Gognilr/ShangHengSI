"""脱敏设置：数量脱敏开关、自定义敏感词、已记住的名称（SPEC 9.10、15.4、19.7，设计 M7 第七节）。

- 词条文字加密存储（term_enc）；列表搜索时取出该 kind 的全部行解密后在内存中按规范形式匹配（Q-110）。
  解密出的文字只进响应，不写日志、不进审计。
- 修改开关、新增或删除词条只影响之后的解析与重算，已在第二步的任务不自动重算。
"""
import uuid
from datetime import datetime
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.admin.settings_store import get_setting, set_setting
from app.audit import service as audit
from app.db.base import utcnow
from app.db.models import RedactionTerm, User
from app.db.session import get_db
from app.redaction import parties
from app.redaction.engine import term_hash
from app.redaction.recheck import recheck
from app.security import crypto
from app.security.deps import require_biz_admin
from app.security.errors import AppError, NotFoundError
from app.security.sessions import client_ip

router = APIRouter(prefix="/admin", dependencies=[Depends(require_biz_admin)])

PAGE_SIZE = 20


class MaskQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mask_quantity: bool


class TermRow(BaseModel):
    id: uuid.UUID
    kind: str
    term: str
    category: str
    is_our_party: bool
    enabled: bool
    hit_count: int
    last_hit_at: Optional[datetime]
    created_by_name: Optional[str]
    created_at: datetime


class TermPage(BaseModel):
    items: list[TermRow]
    total: int
    page: int
    page_size: int


class TermIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]


class TermPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class OurPartyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]


@router.get("/our-parties")
def list_our_parties(db: DbSession = Depends(get_db)) -> dict:
    rows = db.execute(select(RedactionTerm, User.display_name).outerjoin(User, User.id == RedactionTerm.created_by)
                      .where(RedactionTerm.kind == "learned", RedactionTerm.is_our_party.is_(True))
                      .order_by(RedactionTerm.created_at.desc(), RedactionTerm.id)).all()
    return {"items": [_row(t, name) for t, name in rows]}


@router.post("/our-parties", response_model=TermRow, status_code=201)
def create_our_party(body: OurPartyIn, request: Request, admin: User = Depends(require_biz_admin),
                     db: DbSession = Depends(get_db)) -> TermRow:
    from app.review.prompt import fixed_prompt_text
    if any(r.category == "MAP" for r in recheck(fixed_prompt_text(db), real_values=[body.name], terms=[])):
        raise AppError(422, "TERM_IN_PROMPT", "该名称在审核提示中也会出现，请填写具体的公司名称")
    digest = term_hash(body.name)
    row = db.scalar(select(RedactionTerm).where(RedactionTerm.kind == "learned", RedactionTerm.term_hash == digest).with_for_update())
    if row is None:
        row = RedactionTerm(kind="learned", category="ORG", term_hash=digest, term_enc=crypto.encrypt_text(body.name),
                            is_our_party=True, enabled=True, hit_count=0, created_by=admin.id, created_at=utcnow())
        db.add(row)
    else:
        row.category, row.is_our_party, row.created_by = "ORG", True, admin.id
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "TERM_EXISTS", "该名称已存在，请刷新后重试") from None
    _audit(db, admin, request, row, op="our_party")
    db.commit()
    return _row(row, admin.display_name)


@router.delete("/our-parties/{tid}", status_code=204)
def delete_our_party(tid: str, request: Request, admin: User = Depends(require_biz_admin),
                     db: DbSession = Depends(get_db)) -> Response:
    row = _load(db, tid)
    if row.kind != "learned" or not row.is_our_party:
        raise NotFoundError("TERM_NOT_FOUND", "我方主体不存在")
    _audit(db, admin, request, row, op="delete")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


def _row(t: RedactionTerm, name: Optional[str]) -> TermRow:
    return TermRow(
        id=t.id, kind=t.kind, term=crypto.decrypt_text(t.term_enc), category=t.category,
        is_our_party=t.is_our_party, enabled=t.enabled, hit_count=t.hit_count, last_hit_at=t.last_hit_at,
        created_by_name=name, created_at=t.created_at,
    )


def _load(db: DbSession, tid: str) -> RedactionTerm:
    try:
        key = uuid.UUID(tid)
    except ValueError:
        raise NotFoundError("TERM_NOT_FOUND", "词条不存在") from None
    row = db.get(RedactionTerm, key)
    if row is None:
        raise NotFoundError("TERM_NOT_FOUND", "词条不存在")
    return row


def _audit(db: DbSession, admin: User, request: Request, term: RedactionTerm, **detail) -> None:
    audit.record(db, "setting_update", user_id=admin.id, target_type="term", target_id=str(term.id),
                 detail={"kind": term.kind, **detail}, ip=client_ip(request))


@router.get("/settings/redaction", response_model=MaskQuantity)
def get_redaction(db: DbSession = Depends(get_db)) -> MaskQuantity:
    return MaskQuantity(mask_quantity=bool(get_setting(db, "redaction.mask_quantity")))


@router.put("/settings/redaction", response_model=MaskQuantity)
def put_redaction(
    body: MaskQuantity, request: Request, admin: User = Depends(require_biz_admin), db: DbSession = Depends(get_db)
) -> MaskQuantity:
    if bool(get_setting(db, "redaction.mask_quantity")) != body.mask_quantity:
        set_setting(db, "redaction.mask_quantity", body.mask_quantity, admin.id)
        audit.record(db, "setting_update", user_id=admin.id, target_type="setting", target_id="redaction",
                     detail={"keys": ["redaction.mask_quantity"]}, ip=client_ip(request))
        db.commit()
    return body


@router.get("/terms", response_model=TermPage)
def list_terms(
    kind: Literal["learned", "custom"],
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1, le=100000),
    db: DbSession = Depends(get_db),
) -> TermPage:
    rows = db.execute(
        select(RedactionTerm, User.display_name)
        .outerjoin(User, User.id == RedactionTerm.created_by)
        .where(RedactionTerm.kind == kind)
        .order_by(RedactionTerm.created_at.desc(), RedactionTerm.id)
    ).all()
    items = [_row(t, name) for t, name in rows]
    needle = parties.normalize(q.strip())
    if needle:
        items = [r for r in items if needle in parties.normalize(r.term)]
    start = (page - 1) * PAGE_SIZE
    return TermPage(items=items[start:start + PAGE_SIZE], total=len(items), page=page, page_size=PAGE_SIZE)


@router.post("/terms", response_model=TermRow, status_code=status.HTTP_201_CREATED)
def create_term(
    body: TermIn, request: Request, admin: User = Depends(require_biz_admin), db: DbSession = Depends(get_db)
) -> TermRow:
    digest = term_hash(body.term)
    if db.scalar(select(RedactionTerm.id).where(RedactionTerm.kind == "custom", RedactionTerm.term_hash == digest)):
        raise AppError(409, "TERM_EXISTS", "该敏感词已存在")
    # 若出口复检规则下它出现在固定提示文字中，设为敏感词后每次审核都会被拦截，先拒绝（复用 M5-02 的判断）
    from app.review.prompt import fixed_prompt_text  # 避免循环导入

    if any(r.category == "MAP" for r in recheck(fixed_prompt_text(db), real_values=[body.term], terms=[])):
        raise AppError(422, "TERM_IN_PROMPT",
                       "该文字在审核提示中也会出现，设为敏感词后将无法发送，请只填写具体的名称或编号")
    row = RedactionTerm(
        kind="custom", term_enc=crypto.encrypt_text(body.term), term_hash=digest, category="CUSTOM",
        is_our_party=False, enabled=True, hit_count=0, created_by=admin.id, created_at=utcnow(),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "TERM_EXISTS", "该敏感词已存在") from None
    _audit(db, admin, request, row, op="create")
    db.commit()
    return _row(row, admin.display_name)


@router.patch("/terms/{tid}", response_model=TermRow)
def patch_term(
    tid: str, body: TermPatch, request: Request, admin: User = Depends(require_biz_admin),
    db: DbSession = Depends(get_db),
) -> TermRow:
    row = _load(db, tid)
    if row.kind != "custom":
        raise AppError(409, "TERM_NOT_EDITABLE", "已记住的名称不能停用，只能删除")
    if row.enabled != body.enabled:
        row.enabled = body.enabled
        _audit(db, admin, request, row, op="update", enabled=body.enabled)
        db.commit()
    name = db.scalar(select(User.display_name).where(User.id == row.created_by)) if row.created_by else None
    return _row(row, name)


@router.delete("/terms/{tid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_term(
    tid: str, request: Request, admin: User = Depends(require_biz_admin), db: DbSession = Depends(get_db)
) -> Response:
    row = _load(db, tid)
    _audit(db, admin, request, row, op="delete")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
