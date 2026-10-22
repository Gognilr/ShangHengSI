"""认证接口的入参与出参（SPEC 19.2）。"""
import uuid

from pydantic import BaseModel, Field

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
    role: str
    must_change_password: bool

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            must_change_password=user.must_change_password,
        )


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
