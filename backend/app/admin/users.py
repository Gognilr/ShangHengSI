"""账号与角色：/api/v1/admin/users*、/api/v1/admin/roles*（SPEC 15.1、19.7；V31-U5F 第七节）。

- 全部接口要求“账号与角色”权限（sys.accounts）；不提供删除账号的接口。
- 人只挑角色（可兼多个），权限点在 app/security/permissions.py 固定，角色勾选权限点。
- 停用账号、重置密码、强制下线时删除其全部会话；新建与重置后须在首次登录时改密。
- “系统管理员”角色至少保留一个启用中的账号；内置角色不能删除，“系统管理员”不能去掉“账号与角色”权限。
  涉及这条的改动先锁定该角色行，防止并发各降一个。
- 不能停用自己或改自己的角色。
- 审计 detail 只放字段名、角色 id、状态，不放密码和显示名。
"""
import re
import uuid
from datetime import datetime
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.audit import service as audit
from app.db.base import utcnow
from app.db.models import Role, User, UserRole
from app.db.models import Session as UserSession
from app.db.session import get_db
from app.security import permissions as P
from app.security.errors import AppError, NotFoundError
from app.security.passwords import MAX_LENGTH, hash_password, validate_password_strength
from app.security.sessions import client_ip, delete_user_sessions

_perm = P.require("sys.accounts")
router = APIRouter(prefix="/admin", dependencies=[Depends(_perm)])

# Q-99：用户名只允许字母、数字和 _ . @ -
_USERNAME = re.compile(r"[A-Za-z0-9_.@-]{1,50}")

Text50 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Text30 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
RoleIds = Annotated[list[int], Field(min_length=1, max_length=20)]


class UserRow(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    role_ids: list[int]
    status: str
    must_change_password: bool
    last_login_at: Optional[datetime]
    last_login_ip: Optional[str]
    online: int
    created_at: datetime


class UserList(BaseModel):
    items: list[UserRow]


def _rows(db: DbSession, users: list[User]) -> list[UserRow]:
    ids = [u.id for u in users]
    links: dict = {}
    for uid, rid in db.execute(select(UserRole.user_id, UserRole.role_id).where(UserRole.user_id.in_(ids))
                               .order_by(UserRole.role_id)):
        links.setdefault(uid, []).append(rid)
    online = dict(db.execute(select(UserSession.user_id, func.count()).where(
        UserSession.user_id.in_(ids), UserSession.expires_at > utcnow()).group_by(UserSession.user_id)).all())
    return [UserRow(id=u.id, username=u.username, display_name=u.display_name, role_ids=links.get(u.id, []),
                    status=u.status, must_change_password=u.must_change_password, last_login_at=u.last_login_at,
                    last_login_ip=str(u.last_login_ip) if u.last_login_ip else None, online=online.get(u.id, 0),
                    created_at=u.created_at) for u in users]


class UserCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Text50
    display_name: Text50
    # 长度和字符规则由 validate_password_strength 检查，以便返回 PASSWORD_TOO_WEAK
    password: str = Field(max_length=MAX_LENGTH * 2)
    role_ids: RoleIds

    @field_validator("username")
    @classmethod
    def _username_chars(cls, v: str) -> str:
        if not _USERNAME.fullmatch(v):
            raise ValueError("invalid username")
        return v


class UserPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[Text50] = None
    role_ids: Optional[RoleIds] = None
    status: Optional[Literal["active", "disabled"]] = None


class ResetPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(max_length=MAX_LENGTH * 2)


def _load_user(db: DbSession, uid: str) -> User:
    try:
        key = uuid.UUID(uid)
    except ValueError:
        raise NotFoundError("USER_NOT_FOUND", "账号不存在") from None
    user = db.get(User, key)
    if user is None:
        raise NotFoundError("USER_NOT_FOUND", "账号不存在")
    return user


def _check_roles(db: DbSession, ids: list[int]) -> set[int]:
    wanted = set(ids)
    found = set(db.scalars(select(Role.id).where(Role.id.in_(wanted))))
    if found != wanted:
        raise AppError(422, "ROLE_NOT_FOUND", "所选角色不存在，请刷新后重试")
    return wanted


def _lock_sys_role(db: DbSession) -> Role:
    roles = P.ensure_builtin_roles(db)
    return db.scalars(select(Role).where(Role.id == roles[P.ROLE_SYS].id).with_for_update()
                      .execution_options(populate_existing=True)).one()


def _active_sys_admins(db: DbSession, role: Role, *, excluding: uuid.UUID | None = None) -> int:
    stmt = select(func.count()).select_from(UserRole).join(User, User.id == UserRole.user_id).where(
        UserRole.role_id == role.id, User.status == "active")
    if excluding is not None:
        stmt = stmt.where(User.id != excluding)
    return db.scalar(stmt) or 0


@router.get("/users", response_model=UserList)
def list_users(db: DbSession = Depends(get_db)) -> UserList:
    rows = db.scalars(select(User).order_by(User.created_at, User.username)).all()
    return UserList(items=_rows(db, list(rows)))


@router.post("/users", response_model=UserRow, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreateIn,
    request: Request,
    admin: User = Depends(_perm),
    db: DbSession = Depends(get_db),
) -> UserRow:
    validate_password_strength(body.password)
    role_ids = _check_roles(db, body.role_ids)
    exists = db.scalar(select(User.id).where(User.username == body.username))
    if exists is not None:
        raise AppError(409, "USERNAME_EXISTS", "用户名已存在")
    now = utcnow()
    user = User(
        username=body.username,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        status="active",
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "USERNAME_EXISTS", "用户名已存在") from None
    db.add_all(UserRole(user_id=user.id, role_id=rid) for rid in sorted(role_ids))
    audit.record(db, "user_create", user_id=admin.id, target_type="user", target_id=str(user.id),
                 detail={"role_ids": sorted(role_ids)}, ip=client_ip(request))
    db.commit()
    return _rows(db, [user])[0]


@router.patch("/users/{uid}", response_model=UserRow)
def update_user(
    uid: str,
    body: UserPatchIn,
    request: Request,
    admin: User = Depends(_perm),
    db: DbSession = Depends(get_db),
) -> UserRow:
    user = _load_user(db, uid)
    sys_role = _lock_sys_role(db)
    db.refresh(user)
    current = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id)))
    new_roles = _check_roles(db, body.role_ids) if body.role_ids is not None else None
    if new_roles == current:
        new_roles = None
    new_status = body.status if body.status is not None and body.status != user.status else None

    if user.id == admin.id and (new_roles is not None or new_status is not None):
        raise AppError(409, "SELF_CHANGE", "不能停用自己或修改自己的角色")

    # 去掉一个启用中账号的“系统管理员”或停用它时，确认之后还有别的启用中的系统管理员。
    loses_sys = sys_role.id in current and user.status == "active" and (
        (new_roles is not None and sys_role.id not in new_roles) or new_status == "disabled")
    if loses_sys and not _active_sys_admins(db, sys_role, excluding=user.id):
        raise AppError(409, "LAST_ADMIN", "不能停用或降级最后一个启用中的系统管理员")

    fields: list[str] = []
    detail: dict = {}
    if body.display_name is not None and body.display_name != user.display_name:
        user.display_name = body.display_name
        fields.append("display_name")
    if new_roles is not None:
        db.execute(delete(UserRole).where(UserRole.user_id == user.id))
        db.add_all(UserRole(user_id=user.id, role_id=rid) for rid in sorted(new_roles))
        fields.append("roles")
        detail["role_ids"] = sorted(new_roles)
    if new_status is not None:
        user.status = new_status
        fields.append("status")
        detail["status"] = new_status
        if new_status == "disabled":
            delete_user_sessions(db, user.id)
        else:
            user.failed_login_count = 0
            user.locked_until = None
    if fields:
        audit.record(db, "user_update", user_id=admin.id, target_type="user", target_id=str(user.id),
                     detail={"fields": fields, **detail}, ip=client_ip(request))
    db.commit()
    return _rows(db, [user])[0]


@router.post("/users/{uid}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    uid: str,
    body: ResetPasswordIn,
    request: Request,
    admin: User = Depends(_perm),
    db: DbSession = Depends(get_db),
) -> Response:
    user = _load_user(db, uid)
    validate_password_strength(body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    delete_user_sessions(db, user.id)
    audit.record(db, "user_update", user_id=admin.id, target_type="user", target_id=str(user.id),
                 detail={"fields": ["password"]}, ip=client_ip(request))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{uid}/logout", status_code=status.HTTP_204_NO_CONTENT)
def force_logout(uid: str, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)) -> Response:
    """强制下线：删除此人全部会话（V31-U5F 7.2）。对自己用时保留当前会话。"""
    user = _load_user(db, uid)
    keep = request.state.auth.session.token_hash if user.id == admin.id else None
    delete_user_sessions(db, user.id, keep_token_hash=keep)
    audit.record(db, "session_end", user_id=admin.id, target_type="user", target_id=str(user.id),
                 detail={"scope": "forced"}, ip=client_ip(request))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 角色 ----

class PermissionOut(BaseModel):
    code: str
    name: str
    group: str


class RoleRow(BaseModel):
    id: int
    name: str
    builtin: Optional[str]
    permissions: list[str]
    users: int


class RoleList(BaseModel):
    items: list[RoleRow]
    permissions: list[PermissionOut]


class RoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Text30
    permissions: list[str] = Field(max_length=len(P.CODES))

    @field_validator("permissions")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        if any(code not in P.CODES for code in v):
            raise ValueError("unknown permission")
        return [code for code in P.CODES if code in v]


def _role_rows(db: DbSession) -> list[RoleRow]:
    counts = dict(db.execute(select(UserRole.role_id, func.count()).group_by(UserRole.role_id)).all())
    return [RoleRow(id=r.id, name=r.name, builtin=r.builtin, permissions=list(r.permissions), users=counts.get(r.id, 0))
            for r in db.scalars(select(Role).order_by(Role.builtin.is_(None), Role.id))]


def _load_role(db: DbSession, rid: int) -> Role:
    role = db.get(Role, rid)
    if role is None:
        raise NotFoundError("ROLE_NOT_FOUND", "角色不存在")
    return role


def _name_taken(db: DbSession, name: str, rid: int | None = None) -> None:
    stmt = select(Role.id).where(Role.name == name)
    if rid is not None:
        stmt = stmt.where(Role.id != rid)
    if db.scalar(stmt) is not None:
        raise AppError(409, "ROLE_EXISTS", "角色名称已存在")


@router.get("/roles", response_model=RoleList)
def list_roles(db: DbSession = Depends(get_db)) -> RoleList:
    P.ensure_builtin_roles(db)
    db.commit()
    return RoleList(items=_role_rows(db),
                    permissions=[PermissionOut(code=c, name=n, group=g) for c, n, g in P.PERMISSIONS])


@router.post("/roles", response_model=RoleRow, status_code=status.HTTP_201_CREATED)
def create_role(body: RoleIn, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)) -> RoleRow:
    _name_taken(db, body.name)
    now = utcnow()
    role = Role(name=body.name, permissions=body.permissions, created_at=now, updated_at=now)
    db.add(role)
    db.flush()
    audit.record(db, "role_create", user_id=admin.id, target_type="role", target_id=str(role.id),
                 detail={"permissions": body.permissions}, ip=client_ip(request))
    db.commit()
    return next(r for r in _role_rows(db) if r.id == role.id)


@router.put("/roles/{rid}", response_model=RoleRow)
def update_role(rid: int, body: RoleIn, request: Request, admin: User = Depends(_perm),
                db: DbSession = Depends(get_db)) -> RoleRow:
    if _lock_sys_role(db).id == rid and "sys.accounts" not in body.permissions:
        raise AppError(409, "SYS_ROLE_KEEPS_ACCOUNTS", "“系统管理员”必须保留“账号与角色”权限，否则没有人能管理账号")
    role = _load_role(db, rid)
    _name_taken(db, body.name, rid)
    role.name, role.permissions, role.updated_at = body.name, body.permissions, utcnow()
    audit.record(db, "role_update", user_id=admin.id, target_type="role", target_id=str(role.id),
                 detail={"permissions": body.permissions}, ip=client_ip(request))
    db.commit()
    return next(r for r in _role_rows(db) if r.id == role.id)


@router.delete("/roles/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(rid: int, request: Request, admin: User = Depends(_perm), db: DbSession = Depends(get_db)) -> Response:
    role = _load_role(db, rid)
    if role.builtin is not None:
        raise AppError(409, "ROLE_BUILTIN", "内置角色不能删除，可以改名或调整权限")
    used = db.scalar(select(func.count()).select_from(UserRole).where(UserRole.role_id == rid)) or 0
    if used:
        raise AppError(409, "ROLE_IN_USE", f"还有 {used} 个账号使用该角色，先在账号里改掉再删除")
    db.delete(role)
    audit.record(db, "role_delete", user_id=admin.id, target_type="role", target_id=str(rid), ip=client_ip(request))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
