"""V32-B-01：工作台界面用的读取入口（虚构样本）——合并历史、会话的只读脱敏结果、owner 校验。"""
import pytest

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

    # 已确认的会话：GET /redaction 只读放行，修改类接口仍 409
    r = c.get(f"{API_PREFIX}/tasks/{tid}/redaction")
    assert r.status_code == 200 and r.json()["summary"]
    assert c.post(f"{API_PREFIX}/tasks/{tid}/redaction/manual", headers=h,
                  json={"loc": "F1-P1", "char_start": 0, "char_end": 1, "category": "CUSTOM"}).status_code == 409
    # 会议纪要不开放；别人的会话与历史一律 404 / 看不到
    assert c.get(f"{API_PREFIX}/tasks/{meeting}/redaction").status_code == 404
    c2, _ = login(other.username)
    assert c2.get(f"{API_PREFIX}/tasks/{tid}/redaction").status_code == 404
    assert c2.get(f"{BASE}/history").json()["total"] == 0
