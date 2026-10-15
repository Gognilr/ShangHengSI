"""认证、会话、CSRF、登录锁定与审计测试（SPEC 6.1、6.6、17.1、17.7、19.2）。"""
import hashlib
import logging
import re

import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text

from app import cli
from app.config import get_settings
from app.db import session as db_session_module
from app.db.models import AuditLog, User
from app.db.models import Session as UserSession
from app.main import API_PREFIX
from app.security.csrf import EXEMPT_PATHS, csrf_guard
from app.security.sessions import SESSION_COOKIE, AuthContext, delete_user_sessions, require_auth
from tests.conftest import DEFAULT_PASSWORD

LOGIN = f"{API_PREFIX}/auth/login"
LOGOUT = f"{API_PREFIX}/auth/logout"
ME = f"{API_PREFIX}/auth/me"
PASSWORD = f"{API_PREFIX}/auth/password"


def _audit_actions(sm, user_id=None):
    with sm() as s:
        q = select(AuditLog.action, AuditLog.detail).order_by(AuditLog.id)
        if user_id is not None:
            q = q.where(AuditLog.user_id == user_id)
        return [(a, d) for a, d in s.execute(q)]


def _session_count(sm, user_id) -> int:
    with sm() as s:
        return s.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id))


def _user(sm, username) -> User:
    with sm() as s:
        return s.scalar(select(User).where(User.username == username))


# ---------------------------------------------------------------- 登录与会话


def test_login_sets_secure_cookie_and_stores_only_hash(api, make_user, db_sessionmaker):
    user = make_user("alice")
    r = api.post(LOGIN, json={"username": "alice", "password": DEFAULT_PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "user"
    assert body["user"]["must_change_password"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", body["csrf_token"])

    set_cookie = r.headers["set-cookie"]
    assert set_cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie.replace("Lax", "lax")
    token = api.cookies.get(SESSION_COOKIE)

    with db_sessionmaker() as s:
        rows = s.scalars(select(UserSession)).all()
    assert len(rows) == 1
    assert rows[0].token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert rows[0].token_hash != token
    assert rows[0].csrf_token == body["csrf_token"]

    u = _user(db_sessionmaker, "alice")
    assert u.last_login_at is not None
    assert ("login", {}) in _audit_actions(db_sessionmaker, user.id)


def test_me_requires_login(api):
    r = api.get(ME)
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_me_returns_user_and_csrf(login, make_user):
    make_user("bob", role="admin")
    c, csrf = login("bob")
    r = c.get(ME)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "bob"
    assert body["user"]["role"] == "admin"
    assert body["csrf_token"] == csrf
    assert body["alerts"] == []


def test_login_replaces_old_session(login, make_user, db_sessionmaker):
    user = make_user("carol")
    c, _ = login("carol")
    old = c.cookies.get(SESSION_COOKIE)
    r = c.post(LOGIN, json={"username": "carol", "password": DEFAULT_PASSWORD})
    assert r.status_code == 200
    assert c.cookies.get(SESSION_COOKIE) != old
    assert _session_count(db_sessionmaker, user.id) == 1


def test_wrong_password_and_unknown_user_look_the_same(api, make_user, db_sessionmaker):
    user = make_user("dave")
    r1 = api.post(LOGIN, json={"username": "dave", "password": "Wrong1234"})
    r2 = api.post(LOGIN, json={"username": "nobody", "password": "Wrong1234"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json() == {"code": "INVALID_CREDENTIALS", "message": "用户名或密码错误"}
    assert SESSION_COOKIE not in api.cookies

    assert _user(db_sessionmaker, "dave").failed_login_count == 1
    actions = _audit_actions(db_sessionmaker)
    assert ("login_failed", {"reason": "wrong_password"}) in actions
    assert ("login_failed", {"reason": "unknown_user"}) in actions
    # 未知用户名的失败记录不带用户，也不记录尝试的用户名
    with db_sessionmaker() as s:
        unknown = s.scalar(select(AuditLog).where(AuditLog.detail["reason"].astext == "unknown_user"))
    assert unknown.user_id is None
    assert "nobody" not in str(unknown.detail)
    assert user.id is not None


def test_overlong_input_rejected_without_echo(api):
    long_password = "a1" * 200
    r = api.post(LOGIN, json={"username": "x", "password": long_password})
    assert r.status_code == 422
    assert long_password not in r.text


# ---------------------------------------------------------------- 锁定


def test_five_failures_lock_account_for_15_minutes(api, make_user, db_sessionmaker):
    make_user("erin")
    for _ in range(5):
        r = api.post(LOGIN, json={"username": "erin", "password": "Wrong1234"})
        assert r.json()["code"] == "INVALID_CREDENTIALS"

    u = _user(db_sessionmaker, "erin")
    assert u.locked_until is not None
    with db_sessionmaker() as s:
        minutes = s.scalar(
            text("SELECT extract(epoch FROM locked_until - now()) / 60 FROM users WHERE username = 'erin'")
        )
    assert 14 < float(minutes) <= 15

    # 锁定期间，即使密码正确也不能登录
    r = api.post(LOGIN, json={"username": "erin", "password": DEFAULT_PASSWORD})
    assert r.status_code == 401
    assert r.json()["code"] == "ACCOUNT_LOCKED"
    assert ("login_failed", {"reason": "locked_now"}) in _audit_actions(db_sessionmaker, u.id)
    assert ("login_failed", {"reason": "locked"}) in _audit_actions(db_sessionmaker, u.id)

    # 锁定时间过后可以正常登录，计数清零
    with db_sessionmaker() as s:
        s.execute(text("UPDATE users SET locked_until = now() - interval '1 second' WHERE username = 'erin'"))
        s.commit()
    r = api.post(LOGIN, json={"username": "erin", "password": DEFAULT_PASSWORD})
    assert r.status_code == 200
    u = _user(db_sessionmaker, "erin")
    assert u.locked_until is None
    assert u.failed_login_count == 0


def test_success_resets_failure_count(api, make_user, db_sessionmaker):
    make_user("frank")
    for _ in range(4):
        api.post(LOGIN, json={"username": "frank", "password": "Wrong1234"})
    assert api.post(LOGIN, json={"username": "frank", "password": DEFAULT_PASSWORD}).status_code == 200
    for _ in range(4):
        api.post(LOGIN, json={"username": "frank", "password": "Wrong1234"})
    u = _user(db_sessionmaker, "frank")
    assert u.locked_until is None
    assert u.failed_login_count == 4


# ---------------------------------------------------------------- 空闲过期


def test_idle_session_expires(login, make_user, db_sessionmaker):
    user = make_user("gina")
    c, _ = login("gina")
    with db_sessionmaker() as s:
        s.execute(text("UPDATE sessions SET expires_at = now() - interval '1 second'"))
        s.commit()
    assert c.get(ME).status_code == 401
    assert _session_count(db_sessionmaker, user.id) == 0


def test_activity_extends_session_by_8_hours(login, make_user, db_sessionmaker):
    make_user("hank")
    c, _ = login("hank")
    with db_sessionmaker() as s:
        s.execute(text("UPDATE sessions SET expires_at = now() + interval '1 hour'"))
        s.commit()
    assert c.get(ME).status_code == 200
    with db_sessionmaker() as s:
        hours = s.scalar(text("SELECT extract(epoch FROM expires_at - now()) / 3600 FROM sessions"))
    assert 7.9 < float(hours) <= 8


# ---------------------------------------------------------------- CSRF


def test_login_path_is_the_only_csrf_exemption():
    assert EXEMPT_PATHS == {LOGIN}


def test_modifying_requests_need_csrf_token(login, make_user, db_sessionmaker):
    user = make_user("ivy")
    c, csrf = login("ivy")

    r = c.post(LOGOUT)
    assert r.status_code == 403
    assert r.json() == {"code": "CSRF_FAILED", "message": "请求校验失败，请刷新页面后重试"}

    r = c.post(LOGOUT, headers={"X-CSRF-Token": "0" * 64})
    assert r.status_code == 403

    r = c.post(PASSWORD, json={"old_password": DEFAULT_PASSWORD, "new_password": "New12345pw"})
    assert r.status_code == 403
    assert _user(db_sessionmaker, "ivy").must_change_password is False  # 未被修改

    assert _session_count(db_sessionmaker, user.id) == 1
    r = c.post(LOGOUT, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    assert _session_count(db_sessionmaker, user.id) == 0
    assert c.get(ME).status_code == 401


def test_csrf_token_of_another_session_rejected(login, make_user):
    make_user("jack")
    make_user("kate")
    c1, _ = login("jack")
    _, csrf2 = login("kate")
    assert c1.post(LOGOUT, headers={"X-CSRF-Token": csrf2}).status_code == 403


def test_unauthenticated_modifying_request_is_401(api):
    r = api.post(LOGOUT, headers={"X-CSRF-Token": "x"})
    assert r.status_code == 401


# ---------------------------------------------------------------- 首次登录强制改密


@pytest.fixture
def protected_route(db_app):
    """模拟一个普通业务接口：需要登录且不处于须改密状态。"""
    router = APIRouter()

    @router.get("/_t/protected")
    def protected(ctx: AuthContext = Depends(require_auth)):
        return {"user": ctx.user.username}

    @router.post("/_t/protected")
    def protected_post(ctx: AuthContext = Depends(require_auth)):
        return {"ok": True}

    db_app.include_router(router, prefix=API_PREFIX, dependencies=[Depends(csrf_guard)])
    return f"{API_PREFIX}/_t/protected"


def test_must_change_password_blocks_other_endpoints(login, make_user, protected_route, db_sessionmaker):
    make_user("lily", must_change_password=True)
    c, csrf = login("lily")
    assert c.get(ME).json()["user"]["must_change_password"] is True

    r = c.get(protected_route)
    assert r.status_code == 403
    assert r.json()["code"] == "PASSWORD_CHANGE_REQUIRED"

    r = c.post(PASSWORD, json={"old_password": DEFAULT_PASSWORD, "new_password": "Fresh9876pw"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    assert c.get(ME).json()["user"]["must_change_password"] is False
    assert c.get(protected_route).json() == {"user": "lily"}
    assert c.post(protected_route, headers={"X-CSRF-Token": csrf}).status_code == 200


# ---------------------------------------------------------------- 改密


def test_change_password_rules(login, make_user):
    make_user("mike")
    c, csrf = login("mike")
    h = {"X-CSRF-Token": csrf}

    r = c.post(PASSWORD, json={"old_password": "Wrong1234", "new_password": "Fresh9876pw"}, headers=h)
    assert (r.status_code, r.json()["code"]) == (422, "WRONG_OLD_PASSWORD")

    r = c.post(PASSWORD, json={"old_password": DEFAULT_PASSWORD, "new_password": "short1"}, headers=h)
    assert (r.status_code, r.json()["code"]) == (422, "PASSWORD_TOO_WEAK")

    r = c.post(PASSWORD, json={"old_password": DEFAULT_PASSWORD, "new_password": "onlyletters"}, headers=h)
    assert r.json()["code"] == "PASSWORD_TOO_WEAK"

    r = c.post(PASSWORD, json={"old_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD}, headers=h)
    assert (r.status_code, r.json()["code"]) == (422, "PASSWORD_UNCHANGED")


def test_change_password_invalidates_other_sessions(login, make_user, new_client, db_sessionmaker):
    user = make_user("nina")
    c1, csrf1 = login("nina")
    c2, _ = login("nina")
    assert _session_count(db_sessionmaker, user.id) == 2

    r = c1.post(PASSWORD, json={"old_password": DEFAULT_PASSWORD, "new_password": "Fresh9876pw"},
                headers={"X-CSRF-Token": csrf1})
    assert r.status_code == 204
    assert c1.get(ME).status_code == 200
    assert c2.get(ME).status_code == 401
    assert ("password_change", {}) in _audit_actions(db_sessionmaker, user.id)

    fresh = new_client()
    assert fresh.post(LOGIN, json={"username": "nina", "password": DEFAULT_PASSWORD}).status_code == 401
    assert fresh.post(LOGIN, json={"username": "nina", "password": "Fresh9876pw"}).status_code == 200


# ---------------------------------------------------------------- 停用账号


def test_disabled_user_cannot_login(api, make_user):
    make_user("olga", status="disabled")
    r = api.post(LOGIN, json={"username": "olga", "password": DEFAULT_PASSWORD})
    assert (r.status_code, r.json()["code"]) == (401, "ACCOUNT_DISABLED")
    r = api.post(LOGIN, json={"username": "olga", "password": "Wrong1234"})
    assert r.json()["code"] == "INVALID_CREDENTIALS"


def test_disabling_user_deletes_sessions(login, make_user, db_sessionmaker):
    user = make_user("paul")
    c1, _ = login("paul")
    c2, _ = login("paul")
    # 停用账号的接口在 M7 实现，它必须调用 delete_user_sessions
    with db_sessionmaker() as s:
        s.get(User, user.id).status = "disabled"
        delete_user_sessions(s, user.id)
        s.commit()
    assert _session_count(db_sessionmaker, user.id) == 0
    assert c1.get(ME).status_code == 401
    assert c2.get(ME).status_code == 401


def test_session_of_disabled_user_rejected_even_if_not_deleted(login, make_user, db_sessionmaker):
    user = make_user("quinn")
    c, _ = login("quinn")
    with db_sessionmaker() as s:
        s.execute(text("UPDATE users SET status = 'disabled' WHERE username = 'quinn'"))
        s.commit()
    assert c.get(ME).status_code == 401
    assert _session_count(db_sessionmaker, user.id) == 0


# ---------------------------------------------------------------- 客户端地址、日志


def test_client_ip_taken_from_last_forwarded_hop(api, make_user, db_sessionmaker):
    user = make_user("rita")
    r = api.post(LOGIN, json={"username": "rita", "password": DEFAULT_PASSWORD},
                 headers={"X-Forwarded-For": "6.6.6.6, 192.168.10.23"})
    assert r.status_code == 200
    with db_sessionmaker() as s:
        assert str(s.scalar(select(UserSession.ip))) == "192.168.10.23"
        assert str(s.scalar(select(AuditLog.ip).where(AuditLog.user_id == user.id))) == "192.168.10.23"


def test_invalid_forwarded_header_stored_as_null(api, make_user, db_sessionmaker):
    make_user("sam")
    api.post(LOGIN, json={"username": "sam", "password": DEFAULT_PASSWORD},
             headers={"X-Forwarded-For": "not-an-ip"})
    with db_sessionmaker() as s:
        assert s.scalar(select(UserSession.ip)) is None


def test_logs_never_contain_passwords_or_tokens(api, make_user, caplog):
    make_user("tom")
    caplog.set_level(logging.DEBUG)
    api.post(LOGIN, json={"username": "tom", "password": "Wrong1234xyz"})
    r = api.post(LOGIN, json={"username": "tom", "password": DEFAULT_PASSWORD})
    csrf = r.json()["csrf_token"]
    token = api.cookies.get(SESSION_COOKIE)
    api.get(ME)
    api.post(PASSWORD, json={"old_password": DEFAULT_PASSWORD, "new_password": "Fresh9876pw"},
             headers={"X-CSRF-Token": csrf})
    for secret in ("Wrong1234xyz", DEFAULT_PASSWORD, "Fresh9876pw", token, csrf):
        assert secret not in caplog.text


# ---------------------------------------------------------------- create-admin 命令


@pytest.fixture
def cli_db(pg_url, db_engine, monkeypatch):
    """让 cli 使用测试库。"""
    monkeypatch.setenv("DATABASE_URL", pg_url)
    for f in (get_settings, db_session_module.get_engine, db_session_module.get_sessionmaker):
        f.cache_clear()
    yield
    db_session_module.get_engine().dispose()
    for f in (get_settings, db_session_module.get_engine, db_session_module.get_sessionmaker):
        f.cache_clear()


def test_create_admin_command(cli_db, api, db_sessionmaker, capsys):
    assert cli.main(["create-admin", "--username", "root_admin"]) == 0
    out = capsys.readouterr().out
    password = re.search(r"只显示这一次，首次登录须修改）：(\S+)", out).group(1)

    u = _user(db_sessionmaker, "root_admin")
    assert u.role == "admin"
    assert u.must_change_password is True
    assert u.display_name == "管理员"
    assert password not in u.password_hash
    with db_sessionmaker() as s:
        log = s.scalar(select(AuditLog).where(AuditLog.action == "user_create"))
    assert log.detail == {"role": "admin", "via": "cli"}
    assert password not in str(log.detail)

    r = api.post(LOGIN, json={"username": "root_admin", "password": password})
    assert r.status_code == 200
    assert r.json()["user"]["must_change_password"] is True


def test_create_admin_refuses_duplicate(cli_db, make_user, capsys):
    make_user("taken")
    assert cli.main(["create-admin", "--username", "taken"]) == 1
    assert "已存在" in capsys.readouterr().err


def test_random_password_is_strong():
    from app.security.passwords import is_strong_password

    passwords = {cli.random_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(is_strong_password(p) and len(p) == 16 for p in passwords)
