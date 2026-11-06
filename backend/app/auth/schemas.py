"""认证接口的入参与出参（SPEC 19.2）。"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.db.models import User
from app.security.passwords import MAX_LENGTH


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=MAX_LENGTH)


class PasswordChangeIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=MAX_LENGTH)
    # 长度和字符规则由 passwords.validate_password_strength 检查，以便返回明确的中文提示
    new_password: str = Field(max_length=MAX_LENGTH * 2)


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    # 所兼角色的名称与合并后的权限点（V31-U5F-07）；界面按权限点显示菜单与分区
    roles: list[str]
    permissions: list[str]
    must_change_password: bool

    @classmethod
    def of(cls, db: DbSession, user: User) -> "UserOut":
        from app.security.permissions import CODES, user_permissions, user_roles

        perms = user_permissions(db, user.id)
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            roles=[r.name for r in user_roles(db, user.id)],
            permissions=[c for c in CODES if c in perms],
            must_change_password=user.must_change_password,
        )


class DeviceOut(BaseModel):
    id: uuid.UUID
    ip: Optional[str]
    browser: str
    created_at: datetime
    last_seen_at: datetime
    current: bool


class NoticeOut(BaseModel):
    # 本账号刚在别的 IP 登录（V31-U5F 7.2）；没有提醒时为空
    ip: Optional[str] = None
    at: Optional[datetime] = None


class LoginOut(BaseModel):
    user: UserOut
    csrf_token: str


class AlertOut(BaseModel):
    code: str
    message: str


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str
    # 管理员提醒（SPEC 6.7）；普通用户始终为空
    alerts: list[AlertOut] = []
