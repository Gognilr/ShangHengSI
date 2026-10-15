"""权限依赖（SPEC 17.2）。

- 所有任务相关接口必须用 owned_* 依赖取对象：task.owner_id 不是当前用户时返回 404。
  管理员也一样，没有任何例外；“不存在”与“不是你的”对外完全相同，不暴露对象是否存在。
- 以 run_id、finding_id 访问时，先查出所属任务再校验归属。
- 路径中的 id 不是合法 UUID 时同样返回 404，不返回 422。
- 设置类接口（/api/v1/admin/*）的路由整体挂 require_admin，非管理员返回 403。

依赖的参数名与 SPEC 19 节的路径参数一致：/tasks/{id}、/tasks/{id}/files/{fid}、/runs/{rid}、/findings/{fid}。
"""
import uuid

from fastapi import Depends
from sqlalchemy.orm import Session as DbSession

from app.db.models import Finding, ReviewRun, Task, TaskFile, User
from app.db.session import get_db
from app.security.errors import AppError, NotFoundError
from app.security.sessions import AuthContext, require_auth


def current_user(ctx: AuthContext = Depends(require_auth)) -> User:
    """已登录且无须改密的当前用户。"""
    return ctx.user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise AppError(403, "FORBIDDEN", "需要管理员权限")
    return user


def _parse_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def load_owned_task(db: DbSession, user: User, task_id: str) -> Task:
    """按 id 取当前用户自己的任务，否则 404。供依赖和服务层共用。"""
    tid = _parse_id(task_id)
    task = db.get(Task, tid) if tid is not None else None
    if task is None or task.owner_id != user.id:
        raise NotFoundError("TASK_NOT_FOUND", "任务不存在")
    return task


def owned_task(id: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> Task:  # noqa: A002
    return load_owned_task(db, user, id)


def owned_task_file(
    fid: str, task: Task = Depends(owned_task), db: DbSession = Depends(get_db)
) -> TaskFile:
    """/tasks/{id}/files/{fid}：文件必须属于该任务（任务已校验归属）。"""
    file_id = _parse_id(fid)
    f = db.get(TaskFile, file_id) if file_id is not None else None
    if f is None or f.task_id != task.id:
        raise NotFoundError("FILE_NOT_FOUND", "文件不存在")
    return f


def owned_run(rid: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> ReviewRun:
    run_id = _parse_id(rid)
    run = db.get(ReviewRun, run_id) if run_id is not None else None
    if run is None:
        raise NotFoundError("RUN_NOT_FOUND", "审核轮次不存在")
    task = db.get(Task, run.task_id)
    if task is None or task.owner_id != user.id:
        raise NotFoundError("RUN_NOT_FOUND", "审核轮次不存在")
    return run


def owned_finding(fid: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> Finding:
    finding_id = _parse_id(fid)
    finding = db.get(Finding, finding_id) if finding_id is not None else None
    if finding is None:
        raise NotFoundError("FINDING_NOT_FOUND", "问题不存在")
    task = db.get(Task, finding.task_id)
    if task is None or task.owner_id != user.id:
        raise NotFoundError("FINDING_NOT_FOUND", "问题不存在")
    return finding
