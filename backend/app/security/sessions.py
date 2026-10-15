"""登录会话（SPEC 17.1、18.4）。

- 会话令牌是随机值，只放在 HttpOnly、Secure、SameSite=Lax 的 Cookie 里；数据库只存令牌的 SHA-256。
- 空闲 8 小时过期：每次有效请求把 expires_at 顺延 8 小时。
- 账号被停用时，其全部会话立即删除；即使漏删，校验时发现账号不是 active 也会删除并拒绝。
- 令牌和 CSRF 令牌不得写入日志。
"""
import hashlib
import ipaddress
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session as DbSession

from app.db.base import utcnow
from app.db.models import Session as UserSession
from app.db.models import User
from app.db.session import get_db
from app.security.errors import AppError

SESSION_COOKIE = "aireview_session"
IDLE_TIMEOUT = timedelta(hours=8)


@dataclass
class AuthContext:
    user: User
    session: UserSession


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_ip(request: Request) -> str | None:
    """客户端地址（open_questions Q-11）。

    app 容器不对外暴露端口，请求只能经 web 容器（Caddy）转发；Caddy 默认不信任客户端自带的
    X-Forwarded-For，会写入真实的对端地址，所以取该头最右边一项。不是合法 IP 时记为空。
    """
    forwarded = request.headers.get("x-forwarded-for")
    raw = forwarded.split(",")[-1].strip() if forwarded else (request.client.host if request.client else "")
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def new_session(db: DbSession, user_id: uuid.UUID, ip: str | None) -> tuple[str, UserSession]:
    """创建会话，返回（令牌明文，会话行）。令牌明文只用于写 Cookie，不落库。"""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    row = UserSession(
        token_hash=hash_token(token),
        user_id=user_id,
        csrf_token=secrets.token_hex(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + IDLE_TIMEOUT,
        ip=ip,
    )
    db.add(row)
    return token, row


def delete_session(db: DbSession, token: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))


def delete_user_sessions(db: DbSession, user_id: uuid.UUID, *, keep_token_hash: str | None = None) -> None:
    """删除某用户的全部会话（停用账号、重置密码时调用）；keep_token_hash 指定要保留的当前会话。"""
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token_hash is not None:
        stmt = stmt.where(UserSession.token_hash != keep_token_hash)
    db.execute(stmt)


def load_session(db: DbSession, token: str | None) -> AuthContext | None:
    """按令牌取有效会话并顺延过期时间；过期、账号不存在或已停用时删除会话并返回 None。"""
    if not token:
        return None
    row = db.get(UserSession, hash_token(token))
    if row is None:
        return None
    now = utcnow()
    if row.expires_at <= now:
        db.delete(row)
        db.commit()
        return None
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        delete_user_sessions(db, row.user_id)
        db.commit()
        return None
    row.last_seen_at = now
    row.expires_at = now + IDLE_TIMEOUT
    db.commit()
    return AuthContext(user=user, session=row)


def resolve_auth(request: Request, db: DbSession) -> AuthContext:
    """取当前请求的登录信息，同一请求内只查一次。未登录时返回 401。"""
    cached = getattr(request.state, "auth", None)
    if cached is not None:
        return cached
    ctx = load_session(db, request.cookies.get(SESSION_COOKIE))
    if ctx is None:
        raise AppError(401, "UNAUTHORIZED", "请先登录")
    request.state.auth = ctx
    return ctx


def auth_context(request: Request, db: DbSession = Depends(get_db)) -> AuthContext:
    """已登录即可，不检查是否需要改密。只用于 /auth/me、/auth/password、/auth/logout。"""
    return resolve_auth(request, db)


def require_auth(ctx: AuthContext = Depends(auth_context)) -> AuthContext:
    """已登录，且不处于“首次登录须改密”状态。其余所有需要登录的接口都用这个依赖。"""
    if ctx.user.must_change_password:
        raise AppError(403, "PASSWORD_CHANGE_REQUIRED", "请先修改密码")
    return ctx


def set_session_cookie(response: Response, token: str) -> None:
    # 不设 Max-Age：浏览器关闭即失效；服务端另有 8 小时空闲过期
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=True, samesite="lax", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
