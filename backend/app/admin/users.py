"""账号管理：/api/v1/admin/users*（SPEC 15.1、19.7，设计 M7 第四节）。

- 全部接口经 require_admin；不提供删除账号的接口。
- 停用账号、重置密码时删除其全部会话；新建与重置后须在首次登录时改密。
- 最后一个启用中的管理员不能被停用或降级（读全部管理员行时加 FOR UPDATE，防止并发各降一个）。
- 审计 detail 只放字段名、角色、状态，不放密码和显示名。
"""
import re
import uuid
from datetime import datetime
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.audit import service as audit
from app.db.base import utcnow
from app.db.models import User
from app.db.session import get_db
from app.security.deps import require_admin
from app.security.errors import AppError, NotFoundError
from app.security.passwords import MAX_LENGTH, hash_password, validate_password_strength
from app.security.sessions import client_ip, delete_user_sessions

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Q-99：用户名只允许字母、数字和 _ . @ -
_USERNAME = re.compile(r"[A-Za-z0-9_.@-]{1,50}")

Text50 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class UserRow(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    status: str
    must_change_password: bool
    last_login_at: Optional[datetime]
    created_at: datetime

    @classmethod
    def of(cls, u: User) -> "UserRow":
        return cls(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            role=u.role,
            status=u.status,
            must_change_password=u.must_change_password,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )


class UserList(BaseModel):
    items: list[UserRow]


class UserCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Text50
    display_name: Text50
    # 长度和字符规则由 validate_password_strength 检查，以便返回 PASSWORD_TOO_WEAK
    password: str = Field(max_length=MAX_LENGTH * 2)
    role: Literal["user", "admin"]

    @field_validator("username")
    @classmethod
    def _username_chars(cls, v: str) -> str:
        if not _USERNAME.fullmatch(v):
            raise ValueError("invalid username")
        return v


class UserPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[Text50] = None
    role: Optional[Literal["user", "admin"]] = None
    status: Optional[Literal["active", "disabled"]] = None


class ResetPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(max_length=MAX_LENGTH * 2)


def _load_user(db: DbSession, uid: str, *, lock: bool = False) -> tuple[User, list[User]]:
    """取目标账号；lock=True 时把目标和全部管理员行按 id 顺序一次锁定（顺序一致，避免并发时死锁）。"""
    try:
        key = uuid.UUID(uid)
    except ValueError:
        raise NotFoundError("USER_NOT_FOUND", "账号不存在") from None
    if not lock:
        user = db.get(User, key)
        if user is None:
            raise NotFoundError("USER_NOT_FOUND", "账号不存在")
        return user, []
    rows = db.scalars(
        select(User)
        .where((User.role == "admin") | (User.id == key))
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    user = next((u for u in rows if u.id == key), None)
    if user is None:
        raise NotFoundError("USER_NOT_FOUND", "账号不存在")
    return user, [u for u in rows if u.role == "admin"]


@router.get("/users", response_model=UserList)
def list_users(db: DbSession = Depends(get_db)) -> UserList:
    rows = db.scalars(select(User).order_by(User.created_at, User.username)).all()
    return UserList(items=[UserRow.of(u) for u in rows])


@router.post("/users", response_model=UserRow, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreateIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
) -> UserRow:
    validate_password_strength(body.password)
    exists = db.scalar(select(User.id).where(User.username == body.username))
    if exists is not None:
        raise AppError(409, "USERNAME_EXISTS", "用户名已存在")
    now = utcnow()
    user = User(
        username=body.username,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        role=body.role,
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
    audit.record(
        db,
        "user_create",
        user_id=admin.id,
        target_type="user",
        target_id=str(user.id),
        detail={"role": user.role},
        ip=client_ip(request),
    )
    db.commit()
    return UserRow.of(user)


@router.patch("/users/{uid}", response_model=UserRow)
def update_user(
    uid: str,
    body: UserPatchIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
) -> UserRow:
    user, admins = _load_user(db, uid, lock=True)
    is_self = user.id == admin.id
    new_role = body.role if body.role is not None and body.role != user.role else None
    new_status = body.status if body.status is not None and body.status != user.status else None

    if is_self and (new_role is not None or new_status is not None):
        raise AppError(409, "SELF_CHANGE", "不能停用自己或修改自己的角色")

    # 停用或降级一个启用中的管理员时，确认之后还有别的启用中的管理员
    demotes = user.role == "admin" and user.status == "active" and (
        new_role == "user" or new_status == "disabled"
    )
    if demotes:
        if not any(a.id != user.id and a.status == "active" for a in admins):
            raise AppError(409, "LAST_ADMIN", "不能停用或降级最后一个启用中的管理员")

    fields: list[str] = []
    detail: dict = {}
    if body.display_name is not None and body.display_name != user.display_name:
        user.display_name = body.display_name
        fields.append("display_name")
    if new_role is not None:
        user.role = new_role
        fields.append("role")
        detail["role"] = new_role
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
        audit.record(
            db,
            "user_update",
            user_id=admin.id,
            target_type="user",
            target_id=str(user.id),
            detail={"fields": fields, **detail},
            ip=client_ip(request),
        )
    db.commit()
    return UserRow.of(user)


@router.post("/users/{uid}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    uid: str,
    body: ResetPasswordIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
) -> Response:
    user, _ = _load_user(db, uid)
    validate_password_strength(body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    delete_user_sessions(db, user.id)
    audit.record(
        db,
        "user_update",
        user_id=admin.id,
        target_type="user",
        target_id=str(user.id),
        detail={"fields": ["password"]},
        ip=client_ip(request),
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
