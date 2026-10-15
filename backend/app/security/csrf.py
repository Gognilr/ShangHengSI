"""CSRF 校验（SPEC 17.1）。

登录时下发 CSRF 令牌，前端在修改类请求（POST / PUT / PATCH / DELETE）的 X-CSRF-Token 头中携带，
后端与会话中保存的值比对。本依赖挂在 /api/v1 总路由上，对其下所有接口生效，新增接口无需单独处理。

只有登录接口免校验：登录前还没有会话和令牌，依靠 Cookie 的 SameSite=Lax 防护（open_questions Q-05）。
"""
import hmac

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from app.db.session import get_db
from app.security.errors import AppError
from app.security.sessions import resolve_auth

CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 与 main.API_PREFIX + auth 路由保持一致；main 依赖本模块，这里不能反向导入
EXEMPT_PATHS = frozenset({"/api/v1/auth/login"})


def csrf_guard(request: Request, db: DbSession = Depends(get_db)) -> None:
    if request.method not in UNSAFE_METHODS or request.url.path in EXEMPT_PATHS:
        return
    # 修改类请求必须已登录；未登录返回 401
    ctx = resolve_auth(request, db)
    sent = request.headers.get(CSRF_HEADER, "")
    if not hmac.compare_digest(sent.encode("utf-8"), ctx.session.csrf_token.encode("utf-8")):
        raise AppError(403, "CSRF_FAILED", "请求校验失败，请刷新页面后重试")
