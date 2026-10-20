"""审核轮次与问题接口（SPEC 19.5，设计 docs/design/M2.md 第八节）。

路径中的任务、轮次、问题一律用 security/deps.py 的 owned_* 依赖取对象，非本人返回 404。
GET /runs/{rid}/report（导出报告）在 M7 之前的里程碑实现，不在这里。
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.db.models import DOC_TYPES, Finding, RequirementVersion, ReviewRun, Task, User
from app.db.session import get_db
from app.review import service
from app.review.requirements import builtin_version, select_builtin, select_builtin_any_type
from app.review.service import FindingOut, FindingPatch, RunCreated, RunDetail, RunList
from app.security.errors import AppError
from app.security.deps import current_user, owned_finding, owned_run, owned_task
from app.security.sessions import client_ip

router = APIRouter()


@router.post("/tasks/{id}/runs", status_code=status.HTTP_201_CREATED, response_model=RunCreated)
def create_run(
    request: Request,
    task: Task = Depends(owned_task),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> RunCreated:
    run = service.create_run(db, task, user, client_ip(request))
    return RunCreated(id=run.id, run_no=run.run_no, status=run.status)


@router.get("/tasks/{id}/runs", response_model=RunList)
def list_runs(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> RunList:
    return service.list_runs(db, task)


@router.get("/runs/{rid}", response_model=RunDetail)
def get_run(run: ReviewRun = Depends(owned_run), db: DbSession = Depends(get_db)) -> RunDetail:
    return service.run_detail(db, run)


@router.patch("/findings/{fid}", response_model=FindingOut)
def patch_finding(
    patch: FindingPatch,
    finding: Finding = Depends(owned_finding),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> FindingOut:
    return service.update_finding(db, finding, user, patch)


# ---------------------------------------------------------------- 审核要求（抽屉，M5-06）


class BuiltinRequirementOut(BaseModel):
    id: str
    dimension: str | None
    text: str
    doc_types: list[str]


class CompanyRequirementOut(BaseModel):
    code: str
    text: str
    doc_types: list[str]
    stances: list[str]


class RequirementsOut(BaseModel):
    builtin_version: str
    builtin: list[BuiltinRequirementOut]
    company_version: int | None
    company: list[CompanyRequirementOut]


@router.get("/requirements", response_model=RequirementsOut)
def list_requirements(
    stance: Literal["sales", "purchase", "other"],
    source: Literal["outgoing", "incoming"],
    doc_type: str | None = Query(None),
    _user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> RequirementsOut:
    """任何登录用户可查看；不涉及任务归属。doc_type 不传或为空表示全部类型。"""
    doc_type = doc_type or None
    if doc_type is not None and doc_type not in DOC_TYPES:
        raise AppError(422, "VALIDATION_ERROR", "文件类型取值不合法")
    if doc_type is None:
        builtin = select_builtin_any_type(stance, source)
    else:
        builtin = [(r, None) for r in select_builtin(doc_type, stance, source)]
    return RequirementsOut(
        builtin_version=builtin_version(),
        builtin=[BuiltinRequirementOut(id=r.id, dimension=r.dimension, text=r.text, doc_types=types or [doc_type])
                 for r, types in builtin],
        company_version=db.scalar(select(func.max(RequirementVersion.version))),
        company=[CompanyRequirementOut(code=c.code, text=c.text, doc_types=list(c.doc_types or []),
                                       stances=list(c.stances or []))
                 for c in service.company_rows(db, doc_type=doc_type, stance=stance)],
    )
