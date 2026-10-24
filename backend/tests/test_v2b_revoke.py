"""V2-B-05：第五节 #1～8，真实接口与模拟网关，全部使用虚构文字。"""
import io
import json
import uuid

import pytest
from docx import Document
from sqlalchemy import select, text

from app.db.models import AuditLog, RedactionItem, RedactionTerm, Task
from app.redaction.engine import _load_full_map
from app.security import crypto
from tests.review_support import env, llm, ready_task  # noqa: F401
from tests.test_user_note_redaction import alice, complete, task_url  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


def make_task(ready_task, user, note=None):
    doc = Document()
    for value in ("卖方：星河虚构进出口有限公司", "联系人：周示例", "周示例负责验收。", "联系人：陈虚明",
                  "账号：6222000011112222", "总价：人民币 98000 元", "星际探针"):
        doc.add_paragraph(value)
    data = io.BytesIO()
    doc.save(data)
    return ready_task(user, files=[("虚构合同.docx", data.getvalue())], user_note=note)


def items(c, task):
    return c.get(task_url(task, "/redaction")).json()


def revoke(c, h, task, item):
    return c.post(task_url(task, "/redaction/revoke"), headers=h,
                  json={k: item[k] for k in ("loc", "char_start", "char_end")})


def test_01_revoke_person_everywhere_encrypted_audit(db_sessionmaker, alice, ready_task, caplog):
    user, c, h = alice
    task = make_task(ready_task, user, "联系人：周示例")
    target = next(i for i in items(c, task)["items"] if i["loc"] == "F1-P2")
    r = revoke(c, h, task, target)
    assert r.status_code == 200, r.text
    assert not any(i["loc"] in ("F1-P2", "F1-P3", "NOTE") for i in r.json()["items"])
    assert not any(s["text"] == "周示例" for s in r.json()["suspects"])
    with db_sessionmaker() as db:
        row = db.execute(text("SELECT text_enc, loc, category, created_by FROM redaction_exclusions WHERE task_id=:id"), {"id": task.id}).one()
        assert crypto.decrypt_text(row.text_enc) == "周示例" and "周示例".encode() not in row.text_enc
        assert (row.loc, row.category, row.created_by) == ("F1-P2", "PERSON", user.id)
        audits = list(db.scalars(select(AuditLog).where(AuditLog.action == "redaction_revoke")))
        assert len(audits) == 1 and audits[0].detail == {"category": "PERSON"}
        assert "周示例" not in _load_full_map(db, task.id).values()
    assert "周示例" not in caplog.text


@pytest.mark.parametrize("category", ["OUR", "ACCOUNT", "AMOUNT"])
def test_02_forbidden_categories(alice, ready_task, category):
    user, c, h = alice
    task = make_task(ready_task, user)
    target = next(i for i in items(c, task)["items"] if i["category"] == category)
    r = revoke(c, h, task, target)
    assert (r.status_code, r.json()["code"]) == (422, "NOT_REVOCABLE")


def test_03_manual_term_removed(db_sessionmaker, alice, ready_task):
    user, c, h = alice
    task = make_task(ready_task, user)
    r = c.post(task_url(task, "/redaction/manual"), headers=h,
               json={"loc": "F1-P7", "char_start": 0, "char_end": 4, "category": "CUSTOM"})
    assert r.status_code == 200, r.text
    target = next(i for i in r.json()["items"] if i["loc"] == "F1-P7")
    r = revoke(c, h, task, target)
    assert r.status_code == 200 and not any(i["loc"] == "F1-P7" for i in r.json()["items"])
    with db_sessionmaker() as db:
        assert db.execute(text("SELECT count(*) FROM redaction_exclusions")).scalar() == 0


def test_04_confirm_sends_original_not_learned(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = make_task(ready_task, user)
    target = next(i for i in items(c, task)["items"] if i["loc"] == "F1-P2")
    assert revoke(c, h, task, target).status_code == 200
    _, requests = complete(db_sessionmaker, c, h, task, llm)
    assert any("周示例" in json.dumps(json.loads(r.content), ensure_ascii=False) for r in requests)
    with db_sessionmaker() as db:
        assert "周示例" not in {crypto.decrypt_text(t.term_enc) for t in db.scalars(select(RedactionTerm))}


@pytest.mark.parametrize("role", ["user", "admin"])
def test_05_owner_isolation(alice, ready_task, make_user, login, role):
    user, c, h = alice
    task = make_task(ready_task, user)
    target = next(i for i in items(c, task)["items"] if i["loc"] == "F1-P2")
    make_user("bob", role=role)
    other, csrf = login("bob")
    assert revoke(other, {"X-CSRF-Token": csrf}, task, target).status_code == 404


@pytest.mark.parametrize("changed", [True, False])
def test_07_08_note_origin_cleanup(db_sessionmaker, alice, ready_task, changed):
    user, c, h = alice
    task = make_task(ready_task, user, "联系人：周示例")
    initial = items(c, task)["items"]
    assert revoke(c, h, task, next(i for i in initial if i["loc"] == "NOTE")).status_code == 200
    assert revoke(c, h, task, next(i for i in items(c, task)["items"] if i["loc"] == "F1-P4")).status_code == 200
    note = "联系人：周示例，需核对" if changed else "联系人：周示例"
    r = c.patch(task_url(task), headers=h, json={"user_note": note})
    assert r.status_code == 200, r.text
    with db_sessionmaker() as db:
        locs = set(db.execute(text("SELECT loc FROM redaction_exclusions WHERE task_id=:id"), {"id": task.id}).scalars())
        assert locs == ({"F1-P4"} if changed else {"NOTE", "F1-P4"})
    found = items(c, task)["items"]
    assert any(i["loc"] == "F1-P2" for i in found) == changed
    assert not any(i["loc"] == "F1-P4" for i in found)


def test_exact_span_and_task_state(db_sessionmaker, alice, ready_task):
    user, c, h = alice
    task = make_task(ready_task, user)
    target = next(i for i in items(c, task)["items"] if i["loc"] == "F1-P2")
    wrong = {**target, "char_end": target["char_end"] - 1}
    r = revoke(c, h, task, wrong)
    assert (r.status_code, r.json()["code"]) == (422, "NOT_REVOCABLE")
    with db_sessionmaker() as db:
        db.get(Task, task.id).status = "reviewing"
        db.commit()
    r = revoke(c, h, task, target)
    assert (r.status_code, r.json()["code"]) == (409, "TASK_STATE_CONFLICT")


def test_csrf_and_repeated_request(alice, ready_task):
    user, c, h = alice
    task = make_task(ready_task, user)
    target = next(i for i in items(c, task)["items"] if i["loc"] == "F1-P2")
    assert revoke(c, {}, task, target).status_code == 403
    assert revoke(c, h, task, target).status_code == 200
    r = revoke(c, h, task, target)
    assert (r.status_code, r.json()["code"]) == (422, "NOT_REVOCABLE")


def test_remembered_person_not_revocable(db_sessionmaker, alice, ready_task):
    from app.db.base import utcnow
    from app.redaction.engine import term_hash
    user, c, h = alice
    with db_sessionmaker() as db:
        db.add(RedactionTerm(kind="learned", category="PERSON", term_enc=crypto.encrypt_text("周示例"),
                            term_hash=term_hash("周示例"), is_our_party=False, enabled=True,
                            created_by=user.id, created_at=utcnow()))
        db.commit()
    task = make_task(ready_task, user)
    target = next(i for i in items(c, task)["items"] if i["loc"] == "F1-P2")
    r = revoke(c, h, task, target)
    assert (r.status_code, r.json()["code"]) == (422, "NOT_REVOCABLE")
