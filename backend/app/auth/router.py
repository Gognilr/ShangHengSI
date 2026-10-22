"""认证接口：/api/v1/auth/*（SPEC 19.2）。"""
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session as DbSession

from app.admin.alerts import admin_alerts
from app.audit import service as audit
from app.auth.schemas import LoginIn, LoginOut, MeOut, PasswordChangeIn, UserOut
from app.auth.service import authenticate, change_password
from app.db.session import get_db
from app.security.sessions import (
    SESSION_COOKIE,
    AuthContext,
    auth_context,
    clear_session_cookie,
    client_ip,
    delete_session,
    new_session,
    set_session_cookie,
)

router = APIRouter(prefix="/auth")


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, response: Response, db: DbSession = Depends(get_db)) -> LoginOut:
    ip = client_ip(request)
    user = authenticate(db, body.username, body.password, ip)
    # 丢弃浏览器带来的旧会话，登录后总是换发新令牌
    old_token = request.cookies.get(SESSION_COOKIE)
    if old_token:
        delete_session(db, old_token)
    token, row = new_session(db, user.id, ip)
    audit.record(db, "login", user_id=user.id, ip=ip)
    db.commit()
    set_session_cookie(response, token)
    return LoginOut(user=UserOut.of(user), csrf_token=row.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(ctx: AuthContext = Depends(auth_context), db: DbSession = Depends(get_db)) -> Response:
    db.delete(ctx.session)
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/me", response_model=MeOut)
def me(ctx: AuthContext = Depends(auth_context), db: DbSession = Depends(get_db)) -> MeOut:
    # 提醒只给管理员，且须已改密（须改密时先去改密，不查询系统状态）
    alerts = admin_alerts(db) if ctx.user.role == "admin" and not ctx.user.must_change_password else []
    return MeOut(user=UserOut.of(ctx.user), csrf_token=ctx.session.csrf_token, alerts=alerts)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def password(
    body: PasswordChangeIn,
    request: Request,
    ctx: AuthContext = Depends(auth_context),
    db: DbSession = Depends(get_db),
) -> Response:
    change_password(db, ctx, body.old_password, body.new_password, client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
