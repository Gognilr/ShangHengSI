"""V32-B-01：工作台界面用的读取入口（虚构样本）——合并历史、会话的只读脱敏结果、owner 校验。"""
import pytest
import uuid
from sqlalchemy import select

from app.db.models import Party
from app.main import API_PREFIX
from tests.review_support import env  # noqa: F401
from tests.test_session_review import BASE, _session, no_ner  # noqa: F401
from tests.test_tasks_api import files_dir  # noqa: F401


def test_history_merges_sessions_and_reviews_and_redaction_reads(files_dir, env, make_user, login, db_sessionmaker):
    user, other = make_user("ws_reader"), make_user("ws_other")
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    tid, detail = _session(c, h, db_sessionmaker)  # 已确认（active）的工作台会话
    meeting = c.post(BASE, headers=h, json={"purpose": "meeting"}).json()["id"]

    page = c.get(f"{BASE}/history").json()
    rows = {r["id"]: r for r in page["items"]}
    assert tid in rows and rows[tid]["kind"] == "session" and rows[tid]["file_count"] == 1
    assert meeting not in rows  # 会议纪要、翻译不在历史记录里
    assert c.get(f"{BASE}/history", params={"q": "虚构合同"}).json()["total"] == 1
    assert c.get(f"{BASE}/history", params={"q": "不存在的名字"}).json()["total"] == 0

    # 确认页预览按所选立场与文件生成，与实际发送一致
    fid = detail["files"][0]["id"]
    sales = c.get(f"{BASE}/{tid}/outbound", params={"mode": "review", "stance": "sales", "file_ids": [fid]}).json()["text"]
    purchase = c.get(f"{BASE}/{tid}/outbound", params={"mode": "review", "stance": "purchase"}).json()["text"]
    assert "卖方" in sales.split("\n")[0] and "买方" in purchase.split("\n")[0]

    # 已确认的会话：GET /redaction 只读放行，修改类接口仍 409
    r = c.get(f"{API_PREFIX}/tasks/{tid}/redaction")
    assert r.status_code == 200 and r.json()["summary"]
    assert c.post(f"{API_PREFIX}/tasks/{tid}/redaction/manual", headers=h,
                  json={"loc": "F1-P1", "char_start": 0, "char_end": 1, "category": "CUSTOM"}).status_code == 409
    # 本人的会议纪要、翻译也可只读（确认页要读，V321-E-08）；别人的会话与历史一律 404 / 看不到
    assert c.get(f"{API_PREFIX}/tasks/{meeting}/redaction").status_code == 200
    c2, _ = login(other.username)
    assert c2.get(f"{API_PREFIX}/tasks/{meeting}/redaction").status_code == 404
    assert c2.get(f"{API_PREFIX}/tasks/{tid}/redaction").status_code == 404
    assert c2.get(f"{BASE}/history").json()["total"] == 0


def test_confirmed_session_selects_buyer_then_previews_and_starts(files_dir, env, make_user, login, db_sessionmaker):
    user = make_user("ws_buyer")
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    tid, _ = _session(c, h, db_sessionmaker)
    with db_sessionmaker() as db:
        rows = db.scalars(select(Party).where(Party.task_id == uuid.UUID(tid))).all()
        for row in rows:
            row.is_our_party = False
        buyer_id = str(next(row for row in rows if row.role_label == "买方").id)
        db.commit()

    parties = c.get(f"{API_PREFIX}/tasks/{tid}/parties").json()["items"]
    items = [{"id": p["id"], "is_our_party": p["id"] == buyer_id,
              "country": p["country"] or "国家未知", "org_type": p["org_type"] or "类型未知"} for p in parties]
    r = c.put(f"{API_PREFIX}/tasks/{tid}/parties", headers=h, json={"items": items})
    assert r.status_code == 200, r.text
    buyer = next(p for p in r.json()["items"] if p["id"] == buyer_id)
    preview = c.get(f"{BASE}/{tid}/outbound", params={"mode": "review", "stance": "purchase"})
    assert preview.status_code == 200
    assert f'{buyer["code"]}：我方，买方' in preview.json()["text"].split("\n")[0]
    run = c.post(f"{BASE}/{tid}/runs", headers=h, json={"stance": "purchase", "source": "outgoing",
                 "expected_material_version": preview.json()["material_version"]})
    assert run.status_code == 201, run.text
