"""V34-B-07：整套虚构场景按 golden 标注核对线索与等级；Word 报告的内容与语气；历史与搜索整合。样本全部虚构。"""
import io
import json
from pathlib import Path

import pytest
import yaml
from docx import Document

from app.db.models import LlmCall
from tests.fixtures.bid.samples import BIDDERS, PASSWORD, scenario_uploads, tender_with_logo
from tests.review_support import env  # noqa: F401
from tests.test_bid_checks import _jobs
from tests.test_bid_projects import BODY, ROOT, drain, owner  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")
GOLDEN = yaml.safe_load((Path(__file__).parent / "fixtures/bid/scenario_golden.yaml").read_text(encoding="utf-8"))


def scenario(c, h, sm):
    """招标文件 + 三家投标文件（含同标识 PDF 与带密码 PDF），完成本地检查；返回 (项目地址, 三家 id)。"""
    base = ROOT + "/" + c.post(ROOT, headers=h, json=BODY).json()["id"]
    c.post(base + "/files", headers=h, files={"files": ("虚构招标文件.docx", tender_with_logo())})
    drain(sm, base)
    uploads = scenario_uploads()
    r = c.post(base + "/files", headers=h, files=[("files", (name, data)) for _i, name, data in uploads],
               data={"groups": json.dumps([BIDDERS[i] for i, _n, _d in uploads], ensure_ascii=False)})
    assert r.status_code == 200, r.text
    drain(sm, base)
    locked = next(f for f in c.get(base).json()["files"] if f["needs_password"])
    assert c.put(f"{base}/files/{locked['id']}/password", headers=h, json={"password": PASSWORD}).status_code == 200
    drain(sm, base)
    assert c.post(base + "/confirm", headers=h).json()["stage"] == "ready"
    assert c.post(base + "/check", headers=h).json()["stage"] == "checking"
    _jobs(sm, base, "check")
    project = c.get(base).json()
    assert project["stage"] == "checked"
    ids = {b["name"]: b["id"] for b in project["bidders"]}
    return base, [ids[n] for n in BIDDERS]


def _keys(items, ids):
    index = {v: k for k, v in enumerate(ids)}
    return [(i["kind"], i["type"], i["level"], sorted(index[b] for b in i["bidder_ids"])) for i in items]


def _text(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts += [cell.text for cell in row.cells]
    return "\n".join(parts)


def test_scenario_matches_golden(owner, db_sessionmaker):
    """预埋线索全部报出且等级正确，招标文件里就有的内容不报（基线剔除）。"""
    c, h, _ = owner
    base, ids = scenario(c, h, db_sessionmaker)
    found = _keys([i for i in c.get(base + "/check").json()["items"]], ids)
    want = [(g["kind"], g["type"], g["level"], g["bidders"]) for g in GOLDEN["scenario"]]
    assert sorted(found) == sorted(want)
    for g in GOLDEN["absent"]:
        assert not any(k == g["kind"] and t == g["type"] and b == g["bidders"] for k, t, _lv, b in found), g


def test_report_content_tone_and_scope(owner, db_sessionmaker):
    c, h, user = owner
    base, ids = scenario(c, h, db_sessionmaker)
    result = c.get(base + "/check").json()
    by = {(i["type"], tuple(i["bidder_ids"])): i for i in result["items"]}
    excluded = by[("cert_expired", (ids[2],))]
    verified = by[("validity", (ids[1],))]
    assert c.patch(f"{base}/items/{excluded['id']}", headers=h, json={"review_status": "excluded"}).status_code == 200
    assert c.patch(f"{base}/items/{verified['id']}", headers=h, json={"review_status": "verified", "note": "已对照原件投标函第 2 页"}).status_code == 200
    with db_sessionmaker() as db:  # 模型核对已完成的项目：方法说明写明模型名称，且不外发名称
        task_id = base.rsplit("/", 1)[-1]
        from app.db.models import BidProject, Task
        task = db.get(Task, task_id)
        task.policy = "ai"
        db.get(BidProject, task.id).model_state = "done"
        db.add(LlmCall(user_id=user.id, task_id=task.id, purpose="bid", model_name="虚构模型-X", status="sent"))
        db.commit()

    r = c.get(base + "/report", headers=h)
    assert r.status_code == 200 and "wordprocessingml" in r.headers["content-type"]
    text = _text(r.content)
    for name in BIDDERS:
        assert name in text  # 投标人用真实名称
    assert "标书初审疑点报告" in text and "不构成对串通投标或否决投标的认定" in text and "虚构模型-X" in text
    assert "经人工核实：投标有效期 60 天" in text and "已对照原件投标函第 2 页" in text  # 核实后的口吻与备注
    assert "疑似：投标总价 4,180,000.00 高于最高限价" in text  # 未核实的否决项保持“疑似”
    assert "证书有效期至 2025-06-30" not in text  # 已排除的不进正文
    assert "只有这两家共有的相同句" in text and "3,800,000.00" in text
    assert "要求响应矩阵（只列有问题的行）" in text and "疑似不响应" in text
    assert PASSWORD not in text and "password" not in text.lower()
    docs = Document(io.BytesIO(r.content))
    assert any(run.font.highlight_color for t in docs.tables for row in t.rows for cell in row.cells
               for p in cell.paragraphs for run in p.runs)  # 并排对照里相同文字高亮

    only = c.get(base + "/report", headers=h, params={"scope": "clues"})
    clue_text = _text(only.content)
    assert "标书初审串标线索报告" in clue_text and "相似段落" in clue_text and "第四十条（四）" in clue_text
    assert "要求响应矩阵" not in clue_text and "各投标人问题清单" not in clue_text and "报价分析" not in clue_text
    assert c.get(base + "/report", headers=h, params={"scope": "x"}).status_code == 422

    local = ROOT + "/" + c.post(ROOT, headers=h, json=BODY).json()["id"]  # 未完成初审的项目不能导出
    assert c.get(local + "/report", headers=h).status_code == 409
    assert c.get(ROOT + "/00000000-0000-0000-0000-000000000000/report", headers=h).status_code == 404


def test_report_requires_export_permission(make_user, login, db_sessionmaker):
    """只有 use.bid、没有 use.export 的账号不能导出。"""
    from app.db.models import UserRole
    from tests.test_bid_projects import grant
    user = make_user("bid_only")
    with db_sessionmaker() as db:
        db.query(UserRole).filter(UserRole.user_id == user.id).delete()
        db.commit()
    grant(db_sessionmaker, user)
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    base = ROOT + "/" + c.post(ROOT, headers=h, json=BODY).json()["id"]
    assert c.get(base + "/report", headers=h).status_code == 403


def test_history_type_filter_and_search(owner, db_sessionmaker):
    c, h, _ = owner
    base, _ids = scenario(c, h, db_sessionmaker)
    tid = base.rsplit("/", 1)[-1]
    c.post("/api/v1/assistant/sessions", headers=h, json={"purpose": "chat"})
    rows = c.get("/api/v1/assistant/sessions/history").json()["items"]
    bid = next(i for i in rows if i["id"] == tid)
    assert bid["kind"] == "bid" and bid["stage"] == "checked" and bid["high_count"] == 4 and bid["pending_count"] > 0
    assert bid["file_count"] == 10 and not bid["reviewing"]
    only = c.get("/api/v1/assistant/sessions/history", params={"type": "bid"}).json()
    assert [i["id"] for i in only["items"]] == [tid]
    assert all(i["kind"] != "bid" for i in c.get("/api/v1/assistant/sessions/history", params={"type": "review"}).json()["items"])
    assert c.get("/api/v1/assistant/sessions/history", params={"q": "投标文件0-0"}).json()["items"][0]["id"] == tid  # 按文件名搜
    found = c.post("/api/v1/search", headers=h, json={"q": "虚构设备"}).json()
    assert [b["id"] for b in found["bids"]] == [tid]
    assert c.get("/api/v1/assistant/sessions/history", params={"type": "x"}).status_code == 422


def test_history_and_search_hide_bid_without_permission(make_user, login, db_sessionmaker):
    """没有 use.bid 的账号看不到标书项目（即使是自己名下的）。"""
    user = make_user("no_bid_perm")
    with db_sessionmaker() as db:
        from app.db.models import BidProject, Task
        task = Task(owner_id=user.id, kind="bid", title="虚构设备采购项目", stance="purchase", source="incoming", policy="local_only", status="draft")
        db.add(task)
        db.flush()
        db.add(BidProject(task_id=task.id, opening_date=__import__("datetime").date(2026, 11, 16)))
        db.commit()
    c, csrf = login(user.username)
    assert c.get("/api/v1/assistant/sessions/history").json()["items"] == []
    assert c.post("/api/v1/search", headers={"X-CSRF-Token": csrf}, json={"q": "虚构设备"}).json()["bids"] == []
