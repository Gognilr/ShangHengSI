"""A-06：任一用户（含管理员）通过接口访问他人的任务、轮次、问题、对话，全部返回 404（SPEC 17.2、23.1）。

真实的任务、轮次、问题、对话接口在 M2–M6 实现。这里先用只在测试中挂载的路由验证 security/deps.py
中的归属校验依赖（open_questions Q-03）：路由与 SPEC 19 节的路径一一对应，只是放在 /api/v1/_test 前缀下，
避免与以后的真实接口冲突。真实接口接入后，各里程碑需对真实路径重新运行同样的检查。
"""
import uuid

import pytest
from fastapi import APIRouter, Depends

from app.db.base import utcnow
from app.db.models import Finding, ReviewRun, Task, TaskFile, User
from app.main import API_PREFIX
from app.security.csrf import csrf_guard
from app.security.deps import (
    current_user,
    owned_finding,
    owned_run,
    owned_task,
    owned_task_file,
    require_admin,
)

BASE = f"{API_PREFIX}/_test"


@pytest.fixture
def routes(db_app):
    """按 SPEC 19 节的路径挂载测试路由，每个路由只返回取到的对象 id。"""
    r = APIRouter()

    def task_ok(task: Task = Depends(owned_task)):
        return {"id": str(task.id)}

    def file_ok(f: TaskFile = Depends(owned_task_file)):
        return {"id": str(f.id)}

    def run_ok(run: ReviewRun = Depends(owned_run)):
        return {"id": str(run.id)}

    def finding_ok(finding: Finding = Depends(owned_finding)):
        return {"id": str(finding.id)}

    for method in ("GET", "PATCH", "DELETE"):
        r.add_api_route("/tasks/{id}", task_ok, methods=[method])
    r.add_api_route("/tasks/{id}/runs", task_ok, methods=["GET", "POST"])
    r.add_api_route("/tasks/{id}/chat", task_ok, methods=["GET", "POST"])
    r.add_api_route("/tasks/{id}/chat/preview", task_ok, methods=["POST"])
    r.add_api_route("/tasks/{id}/parties", task_ok, methods=["GET", "PUT"])
    r.add_api_route("/tasks/{id}/redaction", task_ok, methods=["GET"])
    r.add_api_route("/tasks/{id}/redaction/outbound", task_ok, methods=["GET"])
    r.add_api_route("/tasks/{id}/local-findings", task_ok, methods=["GET"])
    r.add_api_route("/tasks/{id}/files/{fid}", file_ok, methods=["PATCH"])
    r.add_api_route("/tasks/{id}/files/{fid}/nodes", file_ok, methods=["GET"])
    r.add_api_route("/tasks/{id}/files/{fid}/raw", file_ok, methods=["GET"])
    r.add_api_route("/runs/{rid}", run_ok, methods=["GET"])
    r.add_api_route("/runs/{rid}/report", run_ok, methods=["GET"])
    r.add_api_route("/findings/{fid}", finding_ok, methods=["PATCH"])

    admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    @admin.get("/ping")
    def admin_ping(user: User = Depends(current_user)):
        return {"admin": user.username}

    @admin.put("/ping")
    def admin_put():
        return {"ok": True}

    db_app.include_router(r, prefix=BASE, dependencies=[Depends(csrf_guard)])
    db_app.include_router(admin, prefix=BASE, dependencies=[Depends(csrf_guard)])


@pytest.fixture
def world(routes, make_user, db_sessionmaker):
    """alice 有一个任务（含文件、轮次、问题）；bob 是另一个普通用户，也有自己的任务和文件；carl 是管理员。"""
    alice = make_user("alice")
    bob = make_user("bob")
    carl = make_user("carl", role="admin")
    now = utcnow()

    def make_task(owner: User) -> dict:
        with db_sessionmaker() as s:
            task = Task(owner_id=owner.id, title="虚构合同.docx", stance="sales", source="incoming",
                        policy="ai", status="reviewed", created_at=now, updated_at=now)
            s.add(task)
            s.flush()
            f = TaskFile(task_id=task.id, seq=1, is_main=True, original_name="虚构合同.docx", ext="docx",
                         size_bytes=1, sha256="0" * 64, storage_key=uuid.uuid4().hex, created_at=now)
            run = ReviewRun(task_id=task.id, run_no=1, status="completed", stance="sales", source="incoming",
                            main_doc_type="contract", created_by=owner.id, queued_at=now)
            s.add_all([f, run])
            s.flush()
            finding = Finding(run_id=run.id, task_id=task.id, seq=1, title="虚构问题", category="legal",
                              severity="high", cite_status="verified", problem="虚构说明")
            s.add(finding)
            s.commit()
            return {"task": str(task.id), "file": str(f.id), "run": str(run.id), "finding": str(finding.id)}

    return {"alice": alice, "bob": bob, "carl": carl, "a": make_task(alice), "b": make_task(bob)}


# (方法, 路径模板)。{task} {file} {run} {finding} 由测试数据替换
TASK_ENDPOINTS = [
    ("GET", "/tasks/{task}"),
    ("PATCH", "/tasks/{task}"),
    ("DELETE", "/tasks/{task}"),
    ("GET", "/tasks/{task}/runs"),
    ("POST", "/tasks/{task}/runs"),
    ("GET", "/tasks/{task}/chat"),
    ("POST", "/tasks/{task}/chat"),
    ("POST", "/tasks/{task}/chat/preview"),
    ("GET", "/tasks/{task}/parties"),
    ("PUT", "/tasks/{task}/parties"),
    ("GET", "/tasks/{task}/redaction"),
    ("GET", "/tasks/{task}/redaction/outbound"),
    ("GET", "/tasks/{task}/local-findings"),
    ("PATCH", "/tasks/{task}/files/{file}"),
    ("GET", "/tasks/{task}/files/{file}/nodes"),
    ("GET", "/tasks/{task}/files/{file}/raw"),
    ("GET", "/runs/{run}"),
    ("GET", "/runs/{run}/report"),
    ("PATCH", "/findings/{finding}"),
]


def _path(template: str, ids: dict) -> str:
    return BASE + template.format(**ids)


def _call(client, csrf, method, path):
    return client.request(method, path, headers={"X-CSRF-Token": csrf})


@pytest.mark.parametrize("method,template", TASK_ENDPOINTS)
def test_owner_can_access(world, login, method, template):
    c, csrf = login("alice")
    r = _call(c, csrf, method, _path(template, world["a"]))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("intruder", ["bob", "carl"])
@pytest.mark.parametrize("method,template", TASK_ENDPOINTS)
def test_others_including_admin_get_404(world, login, intruder, method, template):
    """A-06：其他普通用户和管理员访问 alice 的对象，一律 404。"""
    c, csrf = login(intruder)
    r = _call(c, csrf, method, _path(template, world["a"]))
    assert r.status_code == 404, f"{intruder} {method} {template} -> {r.status_code}"


@pytest.mark.parametrize("method,template", TASK_ENDPOINTS)
def test_foreign_object_looks_exactly_like_missing_object(world, login, method, template):
    """他人的对象与不存在的对象，响应完全相同，不暴露对象是否存在。"""
    c, csrf = login("bob")
    foreign_ids = dict(world["a"])
    missing_ids = {k: str(uuid.uuid4()) for k in world["a"]}
    # 文件类接口：任务都用 bob 自己的，只比较“别人的文件”与“不存在的文件”
    if "{file}" in template:
        foreign_ids["task"] = missing_ids["task"] = world["b"]["task"]
    foreign = _call(c, csrf, method, _path(template, foreign_ids))
    missing = _call(c, csrf, method, _path(template, missing_ids))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


@pytest.mark.parametrize("method,template", TASK_ENDPOINTS)
def test_malformed_ids_are_404(world, login, method, template):
    c, csrf = login("alice")
    bad = {k: "not-a-uuid" for k in world["a"]}
    r = _call(c, csrf, method, _path(template, bad))
    assert r.status_code == 404


def test_file_of_another_task_rejected_under_own_task(world, login):
    """用自己的任务 id 加别人任务的文件 id，也必须 404。"""
    c, csrf = login("alice")
    ids = {"task": world["a"]["task"], "file": world["b"]["file"]}
    for method, template in TASK_ENDPOINTS:
        if "{file}" in template:
            assert _call(c, csrf, method, _path(template, ids)).status_code == 404


def test_owner_still_gets_404_for_others(world, login):
    """bob 能访问自己的，但访问 alice 的同类对象 404。"""
    c, csrf = login("bob")
    assert _call(c, csrf, "GET", _path("/runs/{run}", world["b"])).status_code == 200
    assert _call(c, csrf, "GET", _path("/runs/{run}", world["a"])).status_code == 404


@pytest.mark.parametrize("method,template", TASK_ENDPOINTS)
def test_unauthenticated_gets_401(world, api, method, template):
    r = api.request(method, _path(template, world["a"]))
    assert r.status_code == 401


def test_must_change_password_user_cannot_reach_task_endpoints(world, login, db_sessionmaker):
    with db_sessionmaker() as s:
        s.get(User, world["alice"].id).must_change_password = True
        s.commit()
    c, csrf = login("alice")
    r = _call(c, csrf, "GET", _path("/tasks/{task}", world["a"]))
    assert (r.status_code, r.json()["code"]) == (403, "PASSWORD_CHANGE_REQUIRED")


# ---------------------------------------------------------------- 管理接口


def test_admin_endpoints_forbidden_for_users(world, login, api):
    c, csrf = login("bob")
    r = c.get(f"{BASE}/admin/ping")
    assert (r.status_code, r.json()["code"]) == (403, "FORBIDDEN")
    assert _call(c, csrf, "PUT", f"{BASE}/admin/ping").status_code == 403
    assert api.get(f"{BASE}/admin/ping").status_code == 401


def test_admin_endpoints_allowed_for_admin(world, login):
    c, csrf = login("carl")
    assert c.get(f"{BASE}/admin/ping").json() == {"admin": "carl"}
    assert _call(c, csrf, "PUT", f"{BASE}/admin/ping").status_code == 200


def test_admin_modifying_requests_still_need_csrf(world, login):
    c, _ = login("carl")
    assert c.put(f"{BASE}/admin/ping").status_code == 403
