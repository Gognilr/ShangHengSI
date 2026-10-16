"""审核轮次与问题接口（SPEC 19.5，设计 docs/design/M2.md 第八节）。

路径中的任务、轮次、问题一律用 security/deps.py 的 owned_* 依赖取对象，非本人返回 404。
GET /runs/{rid}/report（导出报告）在 M7 之前的里程碑实现，不在这里。
"""
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session as DbSession

from app.db.models import Finding, ReviewRun, Task, User
from app.db.session import get_db
from app.review import service
from app.review.service import FindingOut, FindingPatch, RunCreated, RunDetail, RunList
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
