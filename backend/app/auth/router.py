"""认证接口：/api/v1/auth/*（SPEC 19.2）；登录设备与新 IP 提醒（V31-U5F 7.2）。"""
import re
import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.admin.alerts import admin_alerts
from app.audit import service as audit
from app.auth.schemas import DeviceOut, LoginIn, LoginOut, MeOut, NoticeOut, PasswordChangeIn, UserOut
from app.auth.service import authenticate, change_password
from app.db.models import Session as UserSession
from app.db.base import utcnow
from app.db.session import get_db
from app.security.errors import AppError, NotFoundError
from app.security.permissions import user_permissions
from app.security.sessions import (
    SESSION_COOKIE,
    AuthContext,
    auth_context,
    clear_session_cookie,
    client_ip,
    delete_session,
    delete_user_sessions,
    load_session,
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
    user.last_login_ip = ip
    token, row = new_session(db, user.id, ip, request.headers.get("user-agent"))
    audit.record(db, "login", user_id=user.id, ip=ip)
    db.commit()
    set_session_cookie(response, token)
    return LoginOut(user=UserOut.of(db, user), csrf_token=row.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(ctx: AuthContext = Depends(auth_context), db: DbSession = Depends(get_db)) -> Response:
    db.delete(ctx.session)
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/me", response_model=MeOut)
def me(ctx: AuthContext = Depends(auth_context), db: DbSession = Depends(get_db)) -> MeOut:
    # 提醒按权限点给，且须已改密（须改密时先去改密，不查询系统状态）
    perms = user_permissions(db, ctx.user.id)
    alerts = admin_alerts(db, perms) if not ctx.user.must_change_password else []
    return MeOut(user=UserOut.of(db, ctx.user), csrf_token=ctx.session.csrf_token, alerts=alerts)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def password(
    body: PasswordChangeIn,
    request: Request,
    ctx: AuthContext = Depends(auth_context),
    db: DbSession = Depends(get_db),
) -> Response:
    # 改密成功后其他设备全部退出（auth.service.change_password）
    change_password(db, ctx, body.old_password, body.new_password, client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 登录设备（设置 → 个人） ----

def browser_label(ua: str | None) -> str:
    """把 User-Agent 归纳成“Edge 130 · Windows”这样的说法；认不出时写“未知浏览器”。"""
    ua = ua or ""
    name = None
    for pattern, label in ((r"Edg/(\d+)", "Edge"), (r"OPR/(\d+)", "Opera"), (r"Firefox/(\d+)", "Firefox"),
                           (r"Chrome/(\d+)", "Chrome"), (r"Version/(\d+).*Safari", "Safari")):
        hit = re.search(pattern, ua)
        if hit:
            name = f"{label} {hit.group(1)}"
            break
    system = next((label for key, label in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iOS"),
                                             ("iPad", "iOS"), ("Mac OS", "macOS"), ("Linux", "Linux")) if key in ua), None)
    return " · ".join(x for x in (name or "未知浏览器", system) if x)


@router.get("/sessions", response_model=list[DeviceOut])
def my_sessions(ctx: AuthContext = Depends(auth_context), db: DbSession = Depends(get_db)) -> list[DeviceOut]:
    rows = db.scalars(select(UserSession).where(UserSession.user_id == ctx.user.id, UserSession.expires_at > utcnow())
                      .order_by(UserSession.last_seen_at.desc())).all()
    return [DeviceOut(id=r.id, ip=str(r.ip) if r.ip else None, browser=browser_label(r.user_agent),
                      created_at=r.created_at, last_seen_at=r.last_seen_at,
                      current=r.token_hash == ctx.session.token_hash) for r in rows]


@router.delete("/sessions/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def end_session(sid: str, request: Request, ctx: AuthContext = Depends(auth_context),
                db: DbSession = Depends(get_db)) -> Response:
    """退出此设备；不是本人的会话一律 404。退出本机等同于注销。"""
    try:
        key = uuid.UUID(sid)
    except ValueError:
        raise NotFoundError("SESSION_NOT_FOUND", "登录设备不存在") from None
    row = db.scalar(select(UserSession).where(UserSession.id == key, UserSession.user_id == ctx.user.id))
    if row is None:
        raise NotFoundError("SESSION_NOT_FOUND", "登录设备不存在")
    current = row.token_hash == ctx.session.token_hash
    db.delete(row)
    audit.record(db, "session_end", user_id=ctx.user.id, target_type="user", target_id=str(ctx.user.id),
                 detail={"scope": "one"}, ip=client_ip(request))
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if current:
        clear_session_cookie(response)
    return response


@router.post("/sessions/logout-others", status_code=status.HTTP_204_NO_CONTENT)
def end_other_sessions(request: Request, ctx: AuthContext = Depends(auth_context),
                       db: DbSession = Depends(get_db)) -> Response:
    delete_user_sessions(db, ctx.user.id, keep_token_hash=ctx.session.token_hash)
    ctx.session.notice_ip = ctx.session.notice_at = None
    audit.record(db, "session_end", user_id=ctx.user.id, target_type="user", target_id=str(ctx.user.id),
                 detail={"scope": "others"}, ip=client_ip(request))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/notice", response_model=NoticeOut)
def notice(request: Request, db: DbSession = Depends(get_db)) -> NoticeOut:
    """页面每分钟查一次的新 IP 提醒；只查不顺延会话。"""
    ctx = load_session(db, request.cookies.get(SESSION_COOKIE), touch=False)
    if ctx is None:
        raise AppError(401, "UNAUTHORIZED", "请先登录")
    s = ctx.session
    return NoticeOut(ip=str(s.notice_ip) if s.notice_ip else None, at=s.notice_at)


@router.post("/notice/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_notice(ctx: AuthContext = Depends(auth_context), db: DbSession = Depends(get_db)) -> Response:
    ctx.session.notice_ip = ctx.session.notice_at = None
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
