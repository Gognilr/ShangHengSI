"""V2-C-06 / V31-U5F-07：权限点边界、系统管理员保底、归属隔离与提醒分配。"""
import pytest

from app.admin import settings_store
from app.db.models import BackupRecord, Role, User, UserRole, ReviewRun, Finding, RedactionTerm
from app.main import API_PREFIX
from tests.review_support import env, ready_task  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.mark.parametrize("role", ["sys_admin", "biz_admin", "admin"])
def test_v2c06_role_endpoint_matrix(make_user, login, role):
    user = make_user("role_reader", role=role)
    client, _ = login(user.username)
    sys_paths = ("users", "models", "usage", "settings/backup", "settings/retention",
                 "outbound-logs", "system/status", "audit-logs")
    biz_paths = ("company-requirements", "company-profile", "requirement-versions",
                 "settings/redaction", "terms?kind=custom", "our-parties")
    for paths, allowed in ((sys_paths, role in ("sys_admin", "admin")),
                            (biz_paths, role in ("biz_admin", "admin"))):
        for path in paths:
            response = client.get(f"{API_PREFIX}/admin/{path}")
            assert response.status_code == (200 if allowed else 403), (role, path, response.text)
            if not allowed:
                assert response.json()["code"] == "FORBIDDEN"
    # 改版前的单一角色迁入后权限不变（迁移 0038 与 make_user 用同一对应关系）
    perms = set(client.get(f"{API_PREFIX}/auth/me").json()["user"]["permissions"])
    assert ("sys.accounts" in perms) is (role != "biz_admin")
    assert ("redaction.words" in perms) is (role != "sys_admin")
    assert {"use.review", "use.export"} <= perms


def _builtin(db_sessionmaker, key):
    with db_sessionmaker() as db:
        return db.query(Role).filter_by(builtin=key).one().id


def test_custom_role_permission_points(make_user, login, db_sessionmaker):
    """自定义角色只给“自定义敏感词”：同一接口按种类区分，其他管理接口 403。"""
    admin = make_user("perm_admin", role="admin")
    worker = make_user("perm_worker")
    client, csrf = login(admin.username)
    h = {"X-CSRF-Token": csrf}
    role = client.post(f"{API_PREFIX}/admin/roles", json={"name": "保密员", "permissions": ["redaction.words"]},
                       headers=h).json()
    user_role = _builtin(db_sessionmaker, "user")
    assert client.patch(f"{API_PREFIX}/admin/users/{worker.id}", json={"role_ids": [user_role, role["id"]]},
                        headers=h).status_code == 200
    other, _ = login(worker.username)
    assert other.get(f"{API_PREFIX}/admin/terms?kind=custom").status_code == 200
    assert other.get(f"{API_PREFIX}/admin/terms?kind=learned").status_code == 403
    for path in ("users", "roles", "our-parties", "company-requirements", "models"):
        assert other.get(f"{API_PREFIX}/admin/{path}").status_code == 403, path
    # 角色在用不能删；内置角色不能删；系统管理员不能去掉账号权限
    assert client.delete(f"{API_PREFIX}/admin/roles/{role['id']}", headers=h).json()["code"] == "ROLE_IN_USE"
    assert client.delete(f"{API_PREFIX}/admin/roles/{user_role}", headers=h).json()["code"] == "ROLE_BUILTIN"
    sys_role = _builtin(db_sessionmaker, "sys_admin")
    assert client.put(f"{API_PREFIX}/admin/roles/{sys_role}", json={"name": "系统管理员", "permissions": ["sys.models"]},
                      headers=h).json()["code"] == "SYS_ROLE_KEEPS_ACCOUNTS"


@pytest.mark.parametrize("change", [{"status":"disabled"}, {"role":"biz_admin"}, {"role":"user"}])
def test_v2c06_last_system_admin_even_with_business_admin(make_user, login, db_sessionmaker, change):
    target = make_user("only_sys", role="sys_admin")
    if "role" in change:
        change = {"role_ids": [_builtin(db_sessionmaker, change["role"])]}
    make_user("business", role="biz_admin")
    client, csrf = login("only_sys")
    # 系统管理员不能修改自己的角色。
    assert client.patch(f"{API_PREFIX}/admin/users/{target.id}", json={"role_ids":[_builtin(db_sessionmaker, "user")]},
                        headers={"X-CSRF-Token":csrf}).json()["code"] == "SELF_CHANGE"
    # 另一个被停用的系统管理员不能满足“启用中的至少一个”条件。
    operator = make_user("operator", role="admin")
    with db_sessionmaker() as db:
        db.get(User, operator.id).status = "disabled"
        db.commit()
    # 单元调用真实更新函数，操作人仅用于身份/审计，检验保底逻辑与所有系统管理员行锁。
    from starlette.requests import Request
    from app.admin.users import update_user, UserPatchIn
    from app.security.errors import AppError
    with db_sessionmaker() as db:
        with pytest.raises(AppError) as exc:
            update_user(str(target.id), UserPatchIn(**change), Request({"type":"http","headers":[]}), operator, db)
        assert exc.value.status == 409 and exc.value.code == "LAST_ADMIN"
        db.rollback()
        assert db.get(User,target.id).status == "active"
        assert db.query(UserRole).filter_by(user_id=target.id, role_id=_builtin(db_sessionmaker, "sys_admin")).count() == 1


@pytest.mark.parametrize("role", ["sys_admin", "biz_admin", "admin"])
def test_v2c06_all_admin_roles_still_owner_scoped(make_user, login, db_sessionmaker, ready_task, role):
    owner = make_user("owner")
    reader = make_user("reader", role=role)
    task = ready_task(owner)
    with db_sessionmaker() as db:
        run = ReviewRun(task_id=task.id,run_no=1,status="completed",stance="sales",source="outgoing",
                        main_doc_type="contract",created_by=owner.id)
        db.add(run)
        db.flush()
        finding = Finding(task_id=task.id,run_id=run.id,seq=1,title="虚构问题",category="legal",severity="low",
                          cite_status="missing",problem="虚构说明")
        db.add(finding)
        db.commit()
        rid,fid = run.id,finding.id
    client,csrf = login(reader.username)
    for path in (f"tasks/{task.id}",f"tasks/{task.id}/runs",f"runs/{rid}",f"tasks/{task.id}/chat"):
        assert client.get(f"{API_PREFIX}/{path}").status_code == 404
    assert client.patch(f"{API_PREFIX}/findings/{fid}",json={"handling_status":"adopted"},
                        headers={"X-CSRF-Token":csrf}).status_code == 404


@pytest.mark.parametrize("role,has_system", [("sys_admin",True),("biz_admin",False),("admin",True),("user",False)])
def test_v2c06_alerts_are_assigned_by_role(make_user, login, db_sessionmaker, role, has_system):
    make_user("alerts",role=role)
    with db_sessionmaker() as db:
        from app.db.base import utcnow
        from app.security.crypto import encrypt_text
        db.add(RedactionTerm(kind="custom", category="CUSTOM", term_hash="a"*64,
                             term_enc=encrypt_text("验收"), enabled=True))
        db.add(BackupRecord(trigger="manual",status="failed",error_message="虚构失败",finished_at=utcnow()))
        settings_store.set_setting(db,"retention.review_pending",True,None)
        db.commit()
    client,_ = login("alerts")
    alerts = {item["code"] for item in client.get(f"{API_PREFIX}/auth/me").json()["alerts"]}
    assert ("BACKUP_FAILED" in alerts) is has_system
    assert ("RETENTION_REVIEW" in alerts) is has_system
    assert ("PROMPT_TERM_CONFLICT" in alerts) is (role != "user")
