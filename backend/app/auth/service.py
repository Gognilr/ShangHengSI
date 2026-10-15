"""登录校验、锁定与改密（SPEC 6.1、6.6、17.1）。

- 连续 5 次密码错误锁定 15 分钟；成功登录清零。锁定期间不校验密码，也不累计次数。
- 用户名不存在与密码错误返回同样的结果，并做一次同样耗时的哈希校验。
- 失败计数在抛出错误前提交，防止并发请求绕过锁定（读用户行时加 FOR UPDATE）。
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.audit import service as audit
from app.db.base import utcnow
from app.db.models import User
from app.security.errors import AppError
from app.security.passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.security.sessions import AuthContext, delete_user_sessions

MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)


def _invalid_credentials() -> AppError:
    return AppError(401, "INVALID_CREDENTIALS", "用户名或密码错误")


def authenticate(db: DbSession, username: str, password: str, ip: str | None) -> User:
    """校验用户名和密码，成功返回用户（未提交）；失败时记录审计、提交后抛出 401。"""
    user = db.execute(select(User).where(User.username == username).with_for_update()).scalar_one_or_none()
    now = utcnow()

    if user is None:
        dummy_verify(password)
        audit.record(db, "login_failed", detail={"reason": "unknown_user"}, ip=ip)
        db.commit()
        raise _invalid_credentials()

    if user.locked_until is not None and user.locked_until > now:
        audit.record(db, "login_failed", user_id=user.id, detail={"reason": "locked"}, ip=ip)
        db.commit()
        raise AppError(401, "ACCOUNT_LOCKED", "密码错误次数过多，账号已锁定 15 分钟，请稍后再试")

    if not verify_password(user.password_hash, password):
        user.failed_login_count += 1
        reason = "wrong_password"
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = now + LOCK_DURATION
            user.failed_login_count = 0
            reason = "locked_now"
        audit.record(db, "login_failed", user_id=user.id, detail={"reason": reason}, ip=ip)
        db.commit()
        raise _invalid_credentials()

    if user.status != "active":
        audit.record(db, "login_failed", user_id=user.id, detail={"reason": "disabled"}, ip=ip)
        db.commit()
        raise AppError(401, "ACCOUNT_DISABLED", "账号已停用，请联系管理员")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def change_password(db: DbSession, ctx: AuthContext, old_password: str, new_password: str, ip: str | None) -> None:
    """修改本人密码。成功后清除“须改密”标记，并让本人的其他会话失效（当前会话保留）。"""
    user = ctx.user
    if not verify_password(user.password_hash, old_password):
        raise AppError(422, "WRONG_OLD_PASSWORD", "原密码不正确")
    validate_password_strength(new_password)
    if verify_password(user.password_hash, new_password):
        raise AppError(422, "PASSWORD_UNCHANGED", "新密码不能与原密码相同")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    delete_user_sessions(db, user.id, keep_token_hash=ctx.session.token_hash)
    audit.record(db, "password_change", user_id=user.id, target_type="user", target_id=str(user.id), ip=ip)
    db.commit()
