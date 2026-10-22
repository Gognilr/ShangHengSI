"""账号管理接口（SPEC 15.1、19.7，设计 M7 第四节）。全部内容虚构。"""
import threading

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, User
from app.db.models import Session as UserSession
from app.main import API_PREFIX
from tests.conftest import DEFAULT_PASSWORD

USERS = f"{API_PREFIX}/admin/users"
ME = f"{API_PREFIX}/auth/me"
NEW_PASSWORD = "Fresh5678word"


def _hdr(csrf):
    return {"X-CSRF-Token": csrf}


@pytest.fixture
def admin(make_user, login):
    make_user("boss", role="admin")
    return login("boss")


def _create(c, csrf, **over):
    body = {"username": "carol", "display_name": "卡罗尔", "password": NEW_PASSWORD, "role": "user"}
    body.update(over)
    return c.post(USERS, json=body, headers=_hdr(csrf))


def _row(sm, username):
    with sm() as s:
        return s.scalar(select(User).where(User.username == username))


def _audits(sm, action="user_update"):
    with sm() as s:
        return [r.detail for r in s.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))]


# ---------------------------------------------------------------- 权限


def test_non_admin_forbidden_and_anonymous_unauthorized(make_user, login, api):
    make_user("alice")
    c, csrf = login("alice")
    assert c.get(USERS).status_code == 403
    assert _create(c, csrf).status_code == 403
    assert c.patch(f"{USERS}/{'0' * 8}-0000-0000-0000-{'0' * 12}", json={}, headers=_hdr(csrf)).status_code == 403
    assert api.get(USERS).status_code == 401


# ---------------------------------------------------------------- 列表与新增


def test_list_sorted_by_created_and_has_fields(admin, make_user):
    c, _ = admin
    make_user("zed")
    items = c.get(USERS).json()["items"]
    assert [i["username"] for i in items] == ["boss", "zed"]
    assert set(items[0]) == {
        "id", "username", "display_name", "role", "status", "must_change_password", "last_login_at", "created_at",
    }
    assert "password_hash" not in items[0]


def test_create_user_must_change_password_and_audit(admin, db_sessionmaker, login):
    c, csrf = admin
    r = _create(c, csrf)
    assert r.status_code == 201
    body = r.json()
    assert body["must_change_password"] is True
    assert body["role"] == "user" and body["status"] == "active"
    # 新账号可以登录，但须先改密
    c2, csrf2 = login("carol", NEW_PASSWORD)
    assert c2.get(f"{API_PREFIX}/tasks").json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    details = _audits(db_sessionmaker, "user_create")
    assert details == [{"role": "user"}]
    assert NEW_PASSWORD not in str(_audits(db_sessionmaker, "user_create"))


@pytest.mark.parametrize(
    "over",
    [
        {"username": ""},
        {"username": "a" * 51},
        {"username": "has space"},
        {"username": "中文名"},
        {"username": "a/b"},
        {"display_name": ""},
        {"display_name": "   "},
        {"display_name": "x" * 51},
        {"role": "root"},
        {"extra": 1},
    ],
)
def test_create_validation(admin, over):
    c, csrf = admin
    r = _create(c, csrf, **over)
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("username", ["a", "a" * 50, "Bob_1.x@corp-2"])
def test_create_username_boundaries_ok(admin, username):
    c, csrf = admin
    assert _create(c, csrf, username=username).status_code == 201


def test_create_strips_whitespace(admin):
    c, csrf = admin
    r = _create(c, csrf, username="  dave  ", display_name="  戴夫 ")
    assert r.status_code == 201
    assert r.json()["username"] == "dave" and r.json()["display_name"] == "戴夫"


@pytest.mark.parametrize("pw", ["short1", "onlyletters", "12345678", "a" * 300])
def test_create_weak_password(admin, pw):
    c, csrf = admin
    r = _create(c, csrf, password=pw)
    assert r.status_code == 422
    assert r.json()["code"] in {"PASSWORD_TOO_WEAK", "VALIDATION_ERROR"}
    if len(pw) < 200:
        assert r.json()["code"] == "PASSWORD_TOO_WEAK"


def test_create_duplicate_username(admin):
    c, csrf = admin
    assert _create(c, csrf).status_code == 201
    r = _create(c, csrf)
    assert r.status_code == 409
    assert r.json()["code"] == "USERNAME_EXISTS"
    assert _create(c, csrf, username="boss").json()["code"] == "USERNAME_EXISTS"


def test_create_requires_csrf(admin):
    c, _ = admin
    r = c.post(USERS, json={"username": "x", "display_name": "x", "password": NEW_PASSWORD, "role": "user"})
    assert r.status_code == 403


# ---------------------------------------------------------------- 修改


def test_patch_display_name_role_status_and_audit(admin, make_user, db_sessionmaker):
    c, csrf = admin
    u = make_user("alice")
    r = c.patch(f"{USERS}/{u.id}", json={"display_name": "艾丽丝", "role": "admin"}, headers=_hdr(csrf))
    assert r.status_code == 200
    assert r.json()["display_name"] == "艾丽丝" and r.json()["role"] == "admin"
    r = c.patch(f"{USERS}/{u.id}", json={"status": "disabled"}, headers=_hdr(csrf))
    assert r.json()["status"] == "disabled"
    details = _audits(db_sessionmaker)
    assert details == [
        {"fields": ["display_name", "role"], "role": "admin"},
        {"fields": ["status"], "status": "disabled"},
    ]
    assert "艾丽丝" not in str(details)


def test_patch_without_change_writes_no_audit(admin, make_user, db_sessionmaker):
    c, csrf = admin
    u = make_user("alice")
    r = c.patch(f"{USERS}/{u.id}", json={"role": "user", "status": "active", "display_name": "alice"}, headers=_hdr(csrf))
    assert r.status_code == 200
    assert _audits(db_sessionmaker) == []


@pytest.mark.parametrize("body", [{"role": "root"}, {"status": "gone"}, {"display_name": ""}, {"username": "x"}])
def test_patch_validation(admin, make_user, body):
    c, csrf = admin
    u = make_user("alice")
    r = c.patch(f"{USERS}/{u.id}", json=body, headers=_hdr(csrf))
    assert r.status_code == 422


@pytest.mark.parametrize("uid", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_user_not_found(admin, uid):
    c, csrf = admin
    assert c.patch(f"{USERS}/{uid}", json={"role": "user"}, headers=_hdr(csrf)).json()["code"] == "USER_NOT_FOUND"
    r = c.post(f"{USERS}/{uid}/reset-password", json={"new_password": NEW_PASSWORD}, headers=_hdr(csrf))
    assert r.status_code == 404 and r.json()["code"] == "USER_NOT_FOUND"


def test_disable_deletes_sessions_and_blocks_access(admin, make_user, login, db_sessionmaker):
    c, csrf = admin
    u = make_user("alice")
    c2, _ = login("alice")
    assert c2.get(ME).status_code == 200
    c.patch(f"{USERS}/{u.id}", json={"status": "disabled"}, headers=_hdr(csrf))
    with db_sessionmaker() as s:
        assert s.scalars(select(UserSession).where(UserSession.user_id == u.id)).all() == []
    assert c2.get(ME).status_code == 401


def test_enable_clears_lock(admin, make_user, db_sessionmaker):
    c, csrf = admin
    u = make_user("alice", status="disabled")
    with db_sessionmaker() as s:
        row = s.get(User, u.id)
        row.failed_login_count = 4
        from app.db.base import utcnow
        from datetime import timedelta
        row.locked_until = utcnow() + timedelta(minutes=10)
        s.commit()
    r = c.patch(f"{USERS}/{u.id}", json={"status": "active"}, headers=_hdr(csrf))
    assert r.status_code == 200
    row = _row(db_sessionmaker, "alice")
    assert row.failed_login_count == 0 and row.locked_until is None


def test_self_change_rejected_but_display_name_allowed(admin, db_sessionmaker):
    c, csrf = admin
    me = _row(db_sessionmaker, "boss")
    for body in ({"role": "user"}, {"status": "disabled"}):
        r = c.patch(f"{USERS}/{me.id}", json=body, headers=_hdr(csrf))
        assert r.status_code == 409 and r.json()["code"] == "SELF_CHANGE"
    r = c.patch(f"{USERS}/{me.id}", json={"display_name": "老板", "role": "admin"}, headers=_hdr(csrf))
    assert r.status_code == 200 and r.json()["display_name"] == "老板"


def test_admin_can_demote_another_admin_when_still_one_left(make_user, login):
    make_user("boss", role="admin")
    other = make_user("admin2", role="admin")
    c, csrf = login("boss")
    assert c.patch(f"{USERS}/{other.id}", json={"role": "user"}, headers=_hdr(csrf)).status_code == 200


def _direct_patch(sm, target_id, body, operator):
    """绕过路由依赖直接调用处理函数：操作者是一个与库中状态不一致的管理员（模拟并发下的过期会话）。"""
    from starlette.requests import Request

    from app.admin.users import UserPatchIn, update_user

    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    with sm() as s:
        return update_user(str(target_id), UserPatchIn(**body), request, operator, s)


@pytest.mark.parametrize("body", [{"status": "disabled"}, {"role": "user"}])
def test_last_admin_error_when_no_other_active_admin(make_user, db_sessionmaker, body):
    solo = make_user("solo", role="admin")
    make_user("off", role="admin", status="disabled")  # 停用的管理员不算
    stale_operator = User(id=__import__("uuid").uuid4(), username="x", display_name="x", role="admin")
    with pytest.raises(Exception) as ei:
        _direct_patch(db_sessionmaker, solo.id, body, stale_operator)
    assert ei.value.code == "LAST_ADMIN" and ei.value.status == 409
    row = _row(db_sessionmaker, "solo")
    assert row.role == "admin" and row.status == "active"


def test_concurrent_demotion_only_one_succeeds(make_user, login):
    """两位启用的管理员同时互相降级：只有一次成功，另一次是 LAST_ADMIN（409）或权限已失效（403）。"""
    a = make_user("adm_a", role="admin")
    b = make_user("adm_b", role="admin")
    ca, csrf_a = login("adm_a")
    cb, csrf_b = login("adm_b")
    results = {}
    barrier = threading.Barrier(2)

    def run(name, client, csrf, target):
        barrier.wait()
        results[name] = client.patch(f"{USERS}/{target.id}", json={"role": "user"}, headers=_hdr(csrf))

    t1 = threading.Thread(target=run, args=("a", ca, csrf_a, b))
    t2 = threading.Thread(target=run, args=("b", cb, csrf_b, a))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    codes = sorted(r.status_code for r in results.values())
    assert codes[0] == 200 and codes[1] in (403, 409), [r.text for r in results.values()]
    other = next(r for r in results.values() if r.status_code != 200)
    if other.status_code == 409:
        assert other.json()["code"] == "LAST_ADMIN"


# ---------------------------------------------------------------- 重置密码


def test_reset_password(admin, make_user, login, db_sessionmaker):
    c, csrf = admin
    u = make_user("alice")
    c2, _ = login("alice")
    with db_sessionmaker() as s:
        row = s.get(User, u.id)
        row.failed_login_count = 3
        s.commit()
    r = c.post(f"{USERS}/{u.id}/reset-password", json={"new_password": NEW_PASSWORD}, headers=_hdr(csrf))
    assert r.status_code == 204
    assert c2.get(ME).status_code == 401
    row = _row(db_sessionmaker, "alice")
    assert row.must_change_password is True and row.failed_login_count == 0 and row.locked_until is None
    # 旧密码不再有效，新密码可登录且须改密
    assert login("alice", NEW_PASSWORD)[0].get(f"{API_PREFIX}/tasks").json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    details = _audits(db_sessionmaker)
    assert details == [{"fields": ["password"]}]


def test_reset_password_weak_and_old_password_invalid(admin, make_user, new_client):
    c, csrf = admin
    u = make_user("alice")
    r = c.post(f"{USERS}/{u.id}/reset-password", json={"new_password": "weak"}, headers=_hdr(csrf))
    assert r.status_code == 422 and r.json()["code"] == "PASSWORD_TOO_WEAK"
    c.post(f"{USERS}/{u.id}/reset-password", json={"new_password": NEW_PASSWORD}, headers=_hdr(csrf))
    r = new_client().post(f"{API_PREFIX}/auth/login", json={"username": "alice", "password": DEFAULT_PASSWORD})
    assert r.status_code == 401


def test_audit_never_contains_passwords(admin, make_user, db_sessionmaker):
    c, csrf = admin
    u = make_user("alice")
    _create(c, csrf)
    c.post(f"{USERS}/{u.id}/reset-password", json={"new_password": NEW_PASSWORD}, headers=_hdr(csrf))
    c.patch(f"{USERS}/{u.id}", json={"display_name": "艾丽丝"}, headers=_hdr(csrf))
    with db_sessionmaker() as s:
        dump = " ".join(f"{r.action} {r.target_type} {r.target_id} {r.detail}" for r in s.scalars(select(AuditLog)))
    for secret in (NEW_PASSWORD, DEFAULT_PASSWORD, "艾丽丝"):
        assert secret not in dump
