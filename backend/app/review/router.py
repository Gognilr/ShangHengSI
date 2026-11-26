"""审核轮次与问题接口（SPEC 19.5，设计 docs/design/M2.md 第八节）。

路径中的任务、轮次、问题一律用 security/deps.py 的 owned_* 依赖取对象，非本人返回 404。
GET /runs/{rid}/report（导出报告）在 M7 之前的里程碑实现，不在这里。
"""
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app import content_drafts
from app.config import get_settings
from app.db.models import DOC_TYPES, Finding, RequirementVersion, ReviewRun, Task, User
from app.db.session import get_db
from app.review import service
from app.review.contract_compare import compare_contracts, export_contract_comparison
from app.review.requirements import (
    Requirement,
    _builtin_data,
    _matches,
    builtin_version,
    select_builtin,
    select_builtin_any_type,
)
from app.review.service import FindingOut, FindingPatch, RunCreated, RunDetail, RunList
from app.security.errors import AppError
from app.security.permissions import require
from app.security.deps import current_user, load_owned_task, owned_finding, owned_run, owned_task
from app.security.sessions import client_ip

_review = require("use.review")
router = APIRouter()


class _MemoryMultipart(MultiPartParser):
    spool_max_size = 0  # 禁用 SpooledTemporaryFile 自动转存，由请求字节上限约束内存。


async def _contract_uploads(request: Request, action):
    limit = 2 * get_settings().max_upload_mb * 1024 * 1024 + 64 * 1024

    async def bounded_stream():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise AppError(422, 'FILE_TOO_LARGE', '两份文件合计超过上传大小限制')
            yield chunk

    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'multipart/form-data':
        raise AppError(422, 'INVALID_CONTRACT_UPLOAD', '请重新选择两份 Word 稿件')
    try:
        form = await _MemoryMultipart(request.headers, bounded_stream(), max_files=2, max_fields=0).parse()
    except MultiPartException:
        raise AppError(422, 'INVALID_CONTRACT_UPLOAD', '请重新选择两份 Word 稿件') from None
    try:
        baseline, revised = form.get('baseline'), form.get('revised')
        if not isinstance(baseline, UploadFile) or not isinstance(revised, UploadFile):
            raise AppError(422, 'INVALID_CONTRACT_UPLOAD', '请选择基准稿和修改稿')
        return await run_in_threadpool(action, baseline, revised)
    finally:
        await form.close()


@router.post('/review/contract-compare', dependencies=[Depends(_review)])
async def contract_compare(request: Request, response: Response):
    """返回本次两份合同的文字变化，不创建审核任务。"""
    response.headers['Cache-Control'] = 'no-store'
    return await _contract_uploads(request, compare_contracts)


@router.post('/review/contract-compare/export', dependencies=[Depends(_review), Depends(require('use.export'))])
async def contract_compare_export(request: Request):
    """根据本次上传在内存生成 Word，不创建任务或导出记录。"""
    data = await _contract_uploads(request, export_contract_comparison)
    return Response(data, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Cache-Control': 'no-store',
                             'Content-Disposition': f"attachment; filename*=UTF-8''{quote('合同对比表.docx')}",
                             'X-Content-Type-Options': 'nosniff'})


class RunIn(BaseModel):
    use_template: bool = True
    model_id: int | None = Field(None, gt=0)
    expected_material_version: int | None = Field(None, ge=0)


@router.post("/tasks/{id}/runs", status_code=status.HTTP_201_CREATED, response_model=RunCreated, dependencies=[Depends(_review)])
def create_run(
    request: Request,
    body: RunIn | None = Body(None),
    task: Task = Depends(owned_task),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> RunCreated:
    run = service.create_run(db, task, user, client_ip(request),use_template=body.use_template if body else True,
                             model_id=body.model_id if body else None,
                             expected_material_version=body.expected_material_version if body else None)
    return RunCreated(id=run.id, run_no=run.run_no, status=run.status, remembered=task.__dict__.get('_remembered', []))


def _owned_session(id: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> Task:  # noqa: A002
    return load_owned_task(db, user, id, "assistant")


@router.post("/assistant/sessions/{id}/runs", status_code=status.HTTP_201_CREATED, response_model=RunCreated,
             dependencies=[Depends(_review)])
def create_session_run(request: Request, body: service.SessionRunIn, task: Task = Depends(_owned_session),
                       user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> RunCreated:
    """会话发起审核（V32-A-02）：要求“文件审核”权限，只接受工作台会话（purpose=chat）。"""
    run = service.create_session_run(db, task, user, client_ip(request), body)
    return RunCreated(id=run.id, run_no=run.run_no, status=run.status, remembered=task.__dict__.get('_remembered', []))


@router.get("/assistant/sessions/{id}/runs", response_model=RunList)
def list_session_runs(task: Task = Depends(_owned_session), db: DbSession = Depends(get_db)) -> RunList:
    return service.list_runs(db, task)


@router.get("/tasks/{id}/runs", response_model=RunList)
def list_runs(task: Task = Depends(owned_task), db: DbSession = Depends(get_db)) -> RunList:
    return service.list_runs(db, task)


@router.get("/runs/{rid}", response_model=RunDetail)
def get_run(run: ReviewRun = Depends(owned_run), db: DbSession = Depends(get_db)) -> RunDetail:
    return service.run_detail(db, run)


@router.get("/runs/{rid}/sent")
def sent_text(request: Request, run: ReviewRun = Depends(owned_run),
              user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    return service.sent_text(db, run, user, client_ip(request))


@router.patch("/findings/{fid}", response_model=FindingOut)
def patch_finding(
    patch: FindingPatch,
    finding: Finding = Depends(owned_finding),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> FindingOut:
    return service.update_finding(db, finding, user, patch)


@router.put("/findings/{fid}/draft", response_model=FindingOut, dependencies=[Depends(_review)])
def put_finding_draft(
    body: service.DraftIn,
    finding: Finding = Depends(owned_finding),
    user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> FindingOut:
    """讨论稿保存为该问题的修改建议（V32-B-03）：只用于工作台会话的轮次。"""
    return service.save_draft(db, finding, user, body)


@router.post("/findings/{fid}/template-reuse", dependencies=[Depends(_review)])
def share_template_clause(finding: Finding = Depends(owned_finding), db: DbSession = Depends(get_db)):
    db.refresh(finding, with_for_update=True)
    run = db.get(ReviewRun, finding.run_id)
    task = db.get(Task, finding.task_id)
    chosen, _ = service.chosen_text(finding)
    result = content_drafts.publish(db, task, finding.id, chosen, "template",
                                    doc_type=run.main_doc_type, stance=run.stance)
    db.commit()
    return result


@router.delete("/findings/{fid}/template-reuse", dependencies=[Depends(_review)])
def withdraw_template_clause(finding: Finding = Depends(owned_finding), db: DbSession = Depends(get_db)):
    db.refresh(finding, with_for_update=True)
    task = db.get(Task, finding.task_id)
    result = content_drafts.withdraw(db, "template", task.owner_id, source_id=finding.id)
    db.commit()
    return result


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
    sources: list[str]


class RequirementsOut(BaseModel):
    builtin_version: str
    builtin: list[BuiltinRequirementOut]
    company_version: int | None
    company: list[CompanyRequirementOut]


def _builtin_filtered(
    doc_type: str | None, stance: str | None, source: str | None
) -> list[tuple[Requirement, list[str]]]:
    """按给出的条件筛选内置要求（缺少的条件不参与筛选），保持 YAML 顺序；返回 (要求, 适用的文件类型)。"""
    return [
        (Requirement(id=i["id"], source="builtin", dimension=i.get("dimension"), text=i["text"]),
         list(i["doc_types"]))
        for i in _builtin_data()["requirements"]
        if (doc_type is None or _matches(i["doc_types"], doc_type))
        and (stance is None or _matches(i["stances"], stance))
        and (source is None or _matches(i["sources"], source))
    ]


@router.get("/requirements", response_model=RequirementsOut)
def list_requirements(
    stance: Literal["sales", "purchase", "other"] | None = Query(None),
    source: Literal["outgoing", "incoming"] | None = Query(None),
    doc_type: str | None = Query(None),
    _user: User = Depends(current_user),
    db: DbSession = Depends(get_db),
) -> RequirementsOut:
    """任何登录用户可查看；不涉及任务归属。doc_type 不传或为空表示全部类型。"""
    doc_type = doc_type or None
    if doc_type is not None and doc_type not in DOC_TYPES:
        raise AppError(422, "VALIDATION_ERROR", "文件类型取值不合法")
    if stance is None or source is None:
        # Q-113：设置页列出全部内置要求，缺少的立场、来源不参与筛选（公司补充要求同样不按立场筛选）
        builtin = _builtin_filtered(doc_type, stance, source)
    elif doc_type is None:
        builtin = select_builtin_any_type(stance, source)
    else:
        builtin = [(r, None) for r in select_builtin(doc_type, stance, source)]
    return RequirementsOut(
        builtin_version=builtin_version(),
        builtin=[BuiltinRequirementOut(id=r.id, dimension=r.dimension, text=r.text, doc_types=types or [doc_type])
                 for r, types in builtin],
        company_version=db.scalar(select(func.max(RequirementVersion.version))),
        company=[CompanyRequirementOut(code=c.code, text=c.text, doc_types=list(c.doc_types or []),
                                       stances=list(c.stances or []), sources=list(c.sources or []))
                 for c in service.company_rows(db, doc_type=doc_type, stance=stance, source=source)],
    )
