"""审核要求：公司补充要求、公司说明、版本历史（SPEC 15.3、18.21、18.22、19.7，设计 M7 第六节）。

- 编号 CO-n 取自数据库序列，删除后不复用。
- 补充要求和公司说明的文字每次审核都会发给模型：保存前做出口复检同样的检查（Q-108），命中时拒绝，
  响应不复述命中的文字。
- 每次成功的新增、修改、删除、公司说明保存，在同一事务中追加一条 requirement_versions；
  修改类接口先取事务级 advisory lock，保证版本号连续、条数上限不被并发突破。
- 审计 detail 只放操作类型和版本号，不放补充要求和公司说明的文字。
"""
import uuid
from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession

from app.admin.settings_store import get_setting, set_setting
from app.audit import service as audit
from app.db.base import utcnow
from app.db.models import (
    DOC_TYPES,
    STANCES,
    CompanyRequirement,
    RequirementVersion,
    User,
    company_requirement_code_seq,
)
from app.db.session import get_db
from app.redaction.recheck import recheck, recheck_context
from app.security.deps import require_admin
from app.security.errors import AppError, NotFoundError
from app.security.sessions import client_ip

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

MAX_COMPANY_REQUIREMENTS = 30
_LOCK_KEY = 7_207_001  # 修改审核要求的 advisory lock 键（固定值）
_SENSITIVE_MSG = "内容中含已记住的名称、自定义敏感词或电话、账号、带币种金额等信息，不能保存"


def _scope_list(values: list[str], allowed: list[str]) -> list[str]:
    deduped = list(dict.fromkeys(values))
    if not deduped or any(v != "all" and v not in allowed for v in deduped):
        raise ValueError("scope")
    if "all" in deduped and deduped != ["all"]:
        raise ValueError("scope")
    return deduped


class CompanyReqIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    doc_types: list[str]
    stances: list[str]
    enabled: bool
    sort: int = Field(ge=0, le=9999)

    @field_validator("doc_types")
    @classmethod
    def _doc_types(cls, v: list[str]) -> list[str]:
        return _scope_list(v, DOC_TYPES)

    @field_validator("stances")
    @classmethod
    def _stances(cls, v: list[str]) -> list[str]:
        return _scope_list(v, STANCES)


class CompanyReqRow(BaseModel):
    id: uuid.UUID
    code: str
    text: str
    doc_types: list[str]
    stances: list[str]
    enabled: bool
    sort: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, r: CompanyRequirement) -> "CompanyReqRow":
        return cls(id=r.id, code=r.code, text=r.text, doc_types=list(r.doc_types or []),
                   stances=list(r.stances or []), enabled=r.enabled, sort=r.sort,
                   created_at=r.created_at, updated_at=r.updated_at)


class CompanyReqList(BaseModel):
    items: list[CompanyReqRow]
    version: Optional[int]


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class ProfileOut(BaseModel):
    text: str
    version: Optional[int]


class VersionRow(BaseModel):
    version: int
    created_by_name: Optional[str]
    created_at: datetime
    snapshot: Any


class VersionList(BaseModel):
    items: list[VersionRow]


def _latest_version(db: DbSession) -> Optional[int]:
    return db.scalar(select(func.max(RequirementVersion.version)))


def _lock(db: DbSession) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})


def _reject_sensitive(db: DbSession, value: str) -> None:
    """复用出口复检：真实值列表为空，只查已记住名称、自定义敏感词和电话、账号、金额等格式。"""
    if not value:
        return
    ctx = recheck_context(db, None)
    if recheck(value, real_values=[], terms=ctx.terms):
        raise AppError(422, "REQ_TEXT_SENSITIVE", _SENSITIVE_MSG)


def _new_version(db: DbSession, admin: User) -> int:
    rows = db.scalars(
        select(CompanyRequirement)
        .where(CompanyRequirement.enabled.is_(True))
        .order_by(CompanyRequirement.sort, CompanyRequirement.code)
    ).all()
    snapshot = {
        "requirements": [
            {"code": r.code, "text": r.text, "doc_types": list(r.doc_types or []),
             "stances": list(r.stances or []), "sort": r.sort}
            for r in rows
        ],
        "company_profile": str(get_setting(db, "review.company_profile") or ""),
    }
    version = (_latest_version(db) or 0) + 1
    db.add(RequirementVersion(version=version, snapshot=snapshot, created_by=admin.id, created_at=utcnow()))
    db.flush()
    return version


def _load(db: DbSession, rid: str) -> CompanyRequirement:
    try:
        key = uuid.UUID(rid)
    except ValueError:
        raise NotFoundError("REQUIREMENT_NOT_FOUND", "公司补充要求不存在") from None
    row = db.get(CompanyRequirement, key)
    if row is None:
        raise NotFoundError("REQUIREMENT_NOT_FOUND", "公司补充要求不存在")
    return row


def _audit(db: DbSession, admin: User, request: Request, *, target_type: str, target_id: Optional[str],
           op: str, version: int) -> None:
    audit.record(db, "setting_update", user_id=admin.id, target_type=target_type, target_id=target_id,
                 detail={"op": op, "version": version}, ip=client_ip(request))


@router.get("/company-requirements", response_model=CompanyReqList)
def list_company_requirements(db: DbSession = Depends(get_db)) -> CompanyReqList:
    rows = db.scalars(select(CompanyRequirement).order_by(CompanyRequirement.sort, CompanyRequirement.code)).all()
    return CompanyReqList(items=[CompanyReqRow.of(r) for r in rows], version=_latest_version(db))


@router.post("/company-requirements", response_model=CompanyReqRow, status_code=status.HTTP_201_CREATED)
def create_company_requirement(
    body: CompanyReqIn, request: Request, admin: User = Depends(require_admin), db: DbSession = Depends(get_db)
) -> CompanyReqRow:
    _lock(db)
    if db.scalar(select(func.count()).select_from(CompanyRequirement)) >= MAX_COMPANY_REQUIREMENTS:
        raise AppError(409, "COMPANY_REQ_LIMIT", "公司补充要求最多 30 条")
    _reject_sensitive(db, body.text)
    now = utcnow()
    row = CompanyRequirement(
        code=f"CO-{db.scalar(select(company_requirement_code_seq.next_value()))}",
        text=body.text, doc_types=body.doc_types, stances=body.stances, enabled=body.enabled, sort=body.sort,
        created_by=admin.id, created_at=now, updated_at=now,
    )
    db.add(row)
    db.flush()
    version = _new_version(db, admin)
    _audit(db, admin, request, target_type="company_requirement", target_id=row.code, op="create", version=version)
    db.commit()
    return CompanyReqRow.of(row)


@router.put("/company-requirements/{rid}", response_model=CompanyReqRow)
def update_company_requirement(
    rid: str, body: CompanyReqIn, request: Request, admin: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
) -> CompanyReqRow:
    _lock(db)
    row = _load(db, rid)
    if body.text != row.text:
        _reject_sensitive(db, body.text)
    row.text, row.doc_types, row.stances = body.text, body.doc_types, body.stances
    row.enabled, row.sort = body.enabled, body.sort
    db.flush()
    version = _new_version(db, admin)
    _audit(db, admin, request, target_type="company_requirement", target_id=row.code, op="update", version=version)
    db.commit()
    return CompanyReqRow.of(row)


@router.delete("/company-requirements/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company_requirement(
    rid: str, request: Request, admin: User = Depends(require_admin), db: DbSession = Depends(get_db)
) -> Response:
    _lock(db)
    row = _load(db, rid)
    code = row.code
    db.delete(row)
    db.flush()
    version = _new_version(db, admin)
    _audit(db, admin, request, target_type="company_requirement", target_id=code, op="delete", version=version)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/company-profile", response_model=ProfileOut)
def get_company_profile(db: DbSession = Depends(get_db)) -> ProfileOut:
    return ProfileOut(text=str(get_setting(db, "review.company_profile") or ""), version=_latest_version(db))


@router.put("/company-profile", response_model=ProfileOut)
def put_company_profile(
    body: ProfileIn, request: Request, admin: User = Depends(require_admin), db: DbSession = Depends(get_db)
) -> ProfileOut:
    _lock(db)
    _reject_sensitive(db, body.text)
    set_setting(db, "review.company_profile", body.text, admin.id)
    version = _new_version(db, admin)
    _audit(db, admin, request, target_type="company_profile", target_id=None, op="update", version=version)
    db.commit()
    return ProfileOut(text=body.text, version=version)


@router.get("/requirement-versions", response_model=VersionList)
def list_versions(db: DbSession = Depends(get_db)) -> VersionList:
    rows = db.execute(
        select(RequirementVersion, User.display_name)
        .outerjoin(User, User.id == RequirementVersion.created_by)
        .order_by(RequirementVersion.version.desc())
    ).all()
    return VersionList(items=[
        VersionRow(version=v.version, created_by_name=name, created_at=v.created_at, snapshot=v.snapshot)
        for v, name in rows
    ])
