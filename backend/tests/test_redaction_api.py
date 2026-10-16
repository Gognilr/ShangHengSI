"""M2-13：真实脱敏确认接口、重算、状态限制及 A-06 归属隔离。"""
import uuid
from collections import Counter

import pytest
from sqlalchemy import select

from app.db.models import DocNode, LlmCall, Party, Task
from app.main import API_PREFIX
from app.redaction import engine
from app.security.crypto import decrypt_text
from tests.review_support import REAL_BUYER_NAME, REAL_OUR_NAME, env, ready_task  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def alice(make_user, login):
    user = make_user("alice")
    client, csrf = login("alice")
    return user, client, {"X-CSRF-Token": csrf}


def url(task, suffix):
    return f"{API_PREFIX}/tasks/{task.id}/{suffix}"


def parties(client, task):
    r = client.get(url(task, "parties"))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def payload(items, our_id=None):
    return {"items": [
        {"id": p["id"], "is_our_party": p["id"] == our_id if our_id else p["is_our_party"],
         "country": p["country"], "org_type": p["org_type"]} for p in items
    ]}


def file_id(client, task):
    return client.get(f"{API_PREFIX}/tasks/{task.id}").json()["files"][0]["id"]


def test_get_parties_summary_outbound(db_sessionmaker, alice, ready_task):
    user, c, _ = alice
    task = ready_task(user)
    items = parties(c, task)
    assert {p["name"] for p in items} == {REAL_OUR_NAME, REAL_BUYER_NAME}
    assert sum(p["is_our_party"] for p in items) == 1
    assert all(set(p) == {"id", "code", "name", "role_label", "country", "org_type", "is_our_party"}
               for p in items)
    r = c.get(url(task, "redaction"))
    assert r.status_code == 200
    data = r.json()
    assert data["suspects"] == []
    assert data["summary"] == dict(Counter(i["category"] for i in data["items"]))
    assert data["summary"]["ORG"] >= 2 and data["summary"]["AMOUNT"] >= 1
    assert all(set(i) == {"loc", "char_start", "char_end", "category", "placeholder"}
               and i["char_start"] < i["char_end"] for i in data["items"])
    r = c.get(url(task, "redaction/outbound"))
    assert r.status_code == 200
    text = r.json()["text"]
    assert "【主体说明】" in text and "[F1-" in text
    assert REAL_OUR_NAME not in text and REAL_BUYER_NAME not in text
    assert "我方" in text and "客户_A" in text
    with db_sessionmaker() as db:
        assert text == engine.build_outbound_text(db, db.get(Task, task.id))
        assert list(db.scalars(select(LlmCall))) == []


@pytest.mark.parametrize("reverse", [False, True])
def test_put_rebuilds_codes_map_nodes_and_persists(db_sessionmaker, alice, ready_task, reverse):
    user, c, h = alice
    task = ready_task(user)
    before = parties(c, task)
    buyer = next(p for p in before if p["name"] == REAL_BUYER_NAME)
    body = payload(before, buyer["id"])
    for p in body["items"]:
        if p["id"] == buyer["id"]:
            p.update(country="土耳其", org_type="虚构自定义类型")
        else:
            p.update(country="德国", org_type="货代")
    if reverse:
        body["items"].reverse()
    r = c.put(url(task, "parties"), json=body, headers=h)
    assert r.status_code == 200, r.text
    after = r.json()["items"]
    assert {p["id"] for p in after} == {p["id"] for p in before}
    assert sum(p["is_our_party"] for p in after) == 1
    changed = next(p for p in after if p["id"] == buyer["id"])
    assert (changed["code"], changed["country"], changed["org_type"]) == ("我方", "土耳其", "虚构自定义类型")
    assert parties(c, task) == after
    text = c.get(url(task, "redaction/outbound")).json()["text"]
    assert "我方=买方" in text and "德国，货代" in text
    assert REAL_OUR_NAME not in text and REAL_BUYER_NAME not in text
    with db_sessionmaker() as db:
        assert engine.load_redaction_map(db, task.id)["我方"] == REAL_BUYER_NAME
        assert all(p.confirmed for p in db.scalars(select(Party).where(Party.task_id == task.id)))
        buyer_nodes = [n for n in db.scalars(select(DocNode).where(DocNode.task_id == task.id))
                       if n.text_enc and REAL_BUYER_NAME in decrypt_text(n.text_enc)]
        assert buyer_nodes and all("我方" in n.redacted_text for n in buyer_nodes)
    # 同样的完整列表可重复提交。
    assert c.put(url(task, "parties"), json=body, headers=h).status_code == 200


@pytest.mark.parametrize("mode", ["none", "both", "empty"])
def test_exactly_one_our_party(alice, ready_task, mode):
    user, c, h = alice
    task = ready_task(user)
    before = parties(c, task)
    body = payload(before)
    if mode == "empty":
        body["items"] = []
        expected = "OUR_PARTY_REQUIRED"
    else:
        for p in body["items"]:
            p["is_our_party"] = mode == "both"
        expected = "OUR_PARTY_REQUIRED"
    r = c.put(url(task, "parties"), json=body, headers=h)
    assert (r.status_code, r.json()["code"]) == (422, expected)
    assert parties(c, task) == before


@pytest.mark.parametrize("mode", ["duplicate", "missing", "foreign", "unknown", "country", "type", "blank", "bool"])
def test_invalid_party_input_is_atomic(alice, ready_task, mode):
    user, c, h = alice
    task = ready_task(user)
    before = parties(c, task)
    body = payload(before)
    if mode == "duplicate":
        body["items"].append(body["items"][0].copy())
    elif mode == "missing":
        body["items"].pop()
    elif mode == "foreign":
        other = ready_task(user)
        body["items"][0]["id"] = parties(c, other)[0]["id"]
    elif mode == "unknown":
        body["items"][0]["id"] = str(uuid.uuid4())
    elif mode == "country":
        body["items"][0]["country"] = "虚" * 51
    elif mode == "type":
        body["items"][0]["org_type"] = "虚" * 21
    elif mode == "blank":
        body["items"][0]["country"] = "  "
    else:
        body["items"][0]["is_our_party"] = "false"
    r = c.put(url(task, "parties"), json=body, headers=h)
    assert (r.status_code, r.json()["code"]) == (422, "VALIDATION_ERROR")
    assert parties(c, task) == before


@pytest.mark.parametrize("state", ["parsing", "parse_failed", "reviewing", "reviewed", "completed_local"])
def test_mutations_only_awaiting_confirm(db_sessionmaker, alice, ready_task, state):
    user, c, h = alice
    task = ready_task(user)
    before = parties(c, task)
    fid = file_id(c, task)
    with db_sessionmaker() as db:
        db.get(Task, task.id).status = state
        db.commit()
    for r in [c.put(url(task, "parties"), json=payload(before), headers=h),
              c.patch(url(task, f"files/{fid}"), json={"doc_type": "letter"}, headers=h)]:
        assert (r.status_code, r.json()["code"]) == (409, "TASK_STATE_CONFLICT")
    assert parties(c, task) == before
    assert c.get(f"{API_PREFIX}/tasks/{task.id}").json()["files"][0]["doc_type_source"] == "auto"


@pytest.mark.parametrize("doc_type", ["contract", "quotation", "order", "inquiry", "letter"])
def test_patch_file_type(alice, ready_task, doc_type):
    user, c, h = alice
    task = ready_task(user)
    fid = file_id(c, task)
    before = c.get(f"{API_PREFIX}/tasks/{task.id}").json()["files"][0]
    r = c.patch(url(task, f"files/{fid}"), json={"doc_type": doc_type}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == dict(before, doc_type=doc_type, doc_type_source="user")
    assert c.get(f"{API_PREFIX}/tasks/{task.id}").json()["files"][0] == r.json()


@pytest.mark.parametrize("body", [{}, {"doc_type": None}, {"doc_type": "other"}])
def test_patch_invalid_type(alice, ready_task, body):
    user, c, h = alice
    task = ready_task(user)
    r = c.patch(url(task, f"files/{file_id(c, task)}"), json=body, headers=h)
    assert (r.status_code, r.json()["code"]) == (422, "VALIDATION_ERROR")


@pytest.mark.parametrize("role", ["user", "admin"])
def test_a06_other_user_all_real_endpoints(make_user, login, alice, ready_task, role):
    owner, c, _ = alice
    task = ready_task(owner)
    body = payload(parties(c, task))
    fid = file_id(c, task)
    make_user("intruder", role=role)
    stranger, csrf = login("intruder")
    h = {"X-CSRF-Token": csrf}
    for tid in [str(task.id), str(uuid.uuid4()), "invalid-id"]:
        base = f"{API_PREFIX}/tasks/{tid}"
        responses = [stranger.get(base + "/parties"), stranger.get(base + "/redaction"),
                     stranger.get(base + "/redaction/outbound"),
                     stranger.put(base + "/parties", json=body, headers=h),
                     stranger.patch(base + f"/files/{fid}", json={"doc_type": "letter"}, headers=h)]
        for r in responses:
            assert (r.status_code, r.json()["code"]) == (404, "TASK_NOT_FOUND")


def test_cross_task_file_and_csrf(alice, ready_task):
    user, c, h = alice
    task = ready_task(user)
    other = ready_task(user)
    for fid in [file_id(c, other), str(uuid.uuid4()), "invalid-id"]:
        r = c.patch(url(task, f"files/{fid}"), json={"doc_type": "letter"}, headers=h)
        assert (r.status_code, r.json()["code"]) == (404, "FILE_NOT_FOUND")
    r = c.put(url(task, "parties"), json=payload(parties(c, task)))
    assert (r.status_code, r.json()["code"]) == (403, "CSRF_FAILED")
    r = c.patch(url(task, f"files/{file_id(c, task)}"), json={"doc_type": "letter"})
    assert (r.status_code, r.json()["code"]) == (403, "CSRF_FAILED")


def test_errors_and_logs_do_not_echo_input(alice, ready_task, caplog):
    user, c, h = alice
    task = ready_task(user)
    body = payload(parties(c, task))
    secret = "虚构敏感名称金额888888密钥secret"
    body["items"][0]["country"] = secret * 4
    r = c.put(url(task, "parties"), json=body, headers=h)
    assert r.status_code == 422
    assert secret not in r.text + caplog.text
    assert REAL_OUR_NAME not in caplog.text and REAL_BUYER_NAME not in caplog.text
