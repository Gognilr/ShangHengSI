"""M7-09：A-06 对管理接口的补充（SPEC 17.2、23.1，设计 M7 第十二节）。全部内容虚构。

普通用户完成一轮审核并追问一次后，管理员调用全部 /admin/* 读取接口：
- 除“已记住的名称”列表（SPEC 15.4 明确允许管理员看到名称明文，Q-21）外，响应中不出现该任务对照表里的任何真实值；
- 外发记录详情只有脱敏后的内容（出口复检零命中）；
- 管理员访问该任务的任务、轮次、问题、对话、报告接口一律 404。
"""
import pytest
from sqlalchemy import select

from app.db.models import Finding, LlmCall
from app.main import API_PREFIX
from app.redaction.engine import load_real_values
from app.redaction.recheck import recheck
from tests.review_support import REAL_BUYER_NAME, REAL_OUR_NAME, env, llm, ready_task  # noqa: F401
from tests.test_chat import QUESTION, chat_reply, code_of, reviewed

pytestmark = pytest.mark.usefixtures("env")

ADMIN_READS = [
    "/admin/users",
    "/admin/settings/model",
    "/admin/usage",
    "/admin/company-requirements",
    "/admin/company-profile",
    "/admin/requirement-versions",
    "/admin/settings/redaction",
    "/admin/terms?kind=custom",
    "/admin/settings/backup",
    "/admin/backup/records",
    "/admin/settings/retention",
    "/admin/outbound-logs",
    "/admin/system/status",
    "/auth/me",
]


@pytest.fixture
def world(db_sessionmaker, make_user, login, ready_task, llm):  # noqa: F811
    user = make_user("alice")
    c, csrf = login("alice")
    h = {"X-CSRF-Token": csrf}
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    buyer = code_of(db_sessionmaker, task.id, REAL_BUYER_NAME)
    llm(handler=chat_reply(f"{buyer} 的要求对回款不利，建议坚持即期付款。", []))
    r = c.post(f"{API_PREFIX}/tasks/{task.id}/chat", json={"message": QUESTION}, headers=h)
    assert r.status_code == 200, r.text
    with db_sessionmaker() as db:
        real = [v for v in load_real_values(db, task.id) if len(v) >= 4]
        finding = db.scalar(select(Finding).where(Finding.task_id == task.id))
        run_id = finding.run_id
        call_ids = list(db.scalars(select(LlmCall.id).order_by(LlmCall.id)))
    assert REAL_BUYER_NAME in real and REAL_OUR_NAME in real
    make_user("root", role="admin")
    admin, admin_csrf = login("root")
    return {"task": task, "run_id": run_id, "finding_id": finding.id, "real": real, "calls": call_ids,
            "admin": admin, "admin_h": {"X-CSRF-Token": admin_csrf}}


def _assert_clean(body: str, real: list[str], where: str) -> None:
    leaked = [v for v in real if v in body]
    assert not leaked, f"{where} 泄露了 {len(leaked)} 个真实值"


def test_admin_reads_contain_no_task_content(world):
    a, real = world["admin"], world["real"]
    for path in ADMIN_READS:
        r = a.get(f"{API_PREFIX}{path}")
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        _assert_clean(r.text, real, path)
    # 外发记录：列表只有元数据，详情只有脱敏后的内容
    listed = a.get(f"{API_PREFIX}/admin/outbound-logs").json()
    assert listed["total"] == len(world["calls"]) >= 2
    assert {i["purpose"] for i in listed["items"]} >= {"review", "chat"}
    for cid in world["calls"]:
        r = a.get(f"{API_PREFIX}/admin/outbound-logs/{cid}")
        assert r.status_code == 200
        _assert_clean(r.text, real, f"outbound-logs/{cid}")
        detail = r.json()
        for text in (detail["request_text"], detail["response_text"]):
            assert not recheck(text or "", real_values=real, terms=[])


def test_learned_names_are_the_only_place_admin_sees_names(world):
    """SPEC 15.4 / Q-21：已记住的名称按原样显示，这是管理接口中唯一出现名称明文的地方。"""
    items = world["admin"].get(f"{API_PREFIX}/admin/terms?kind=learned").json()["items"]
    shown = {i["term"] for i in items}
    assert shown <= set(world["real"])  # 只有名称本身，不含正文、问题或对话


def test_admin_gets_404_on_task_endpoints(world):
    a, h = world["admin"], world["admin_h"]
    t, run, finding = world["task"].id, world["run_id"], world["finding_id"]
    gets = [f"/tasks/{t}", f"/tasks/{t}/runs", f"/tasks/{t}/chat", f"/tasks/{t}/redaction",
            f"/tasks/{t}/local-findings", f"/tasks/{t}/report", f"/runs/{run}", f"/runs/{run}/report"]
    for path in gets:
        r = a.get(f"{API_PREFIX}{path}")
        assert r.status_code == 404, f"GET {path} -> {r.status_code}"
    posts = [(f"/tasks/{t}/chat", {"message": "第4条是否合理？"}), (f"/tasks/{t}/chat/preview", {"message": "x"})]
    for path, body in posts:
        assert a.post(f"{API_PREFIX}{path}", json=body, headers=h).status_code == 404, path
    assert a.patch(f"{API_PREFIX}/findings/{finding}", json={"handling_status": "adopted"},
                   headers=h).status_code == 404
    assert a.delete(f"{API_PREFIX}/tasks/{t}", headers=h).status_code == 404
