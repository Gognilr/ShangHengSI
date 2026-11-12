"""V32-A-02：会话发起审核（全部为虚构样本，模型用模拟服务）。

主线一例覆盖：先问答再审核同一代号、只确认一次、审核中不能发送、中性主体说明、
问题定位还原、本地算式；另有 local_only 零外发、日期平移新旧会话各一例。
"""
import json
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, update

from app.db.models import Finding, Job, LlmCall, ReviewRun, Task, TaskFile
from app.llm import gateway
from app.main import API_PREFIX
from app.redaction import engine
from app.review import job as review_job
from app.tasks import pipeline
from tests.mock_llm import build_reply
from tests.review_support import env  # noqa: F401
from tests.test_gateway_stream import Events
from tests.test_tasks_api import files_dir  # noqa: F401

BASE = API_PREFIX + "/assistant/sessions"
SAMPLE = Path(__file__).parent / "fixtures/return_probe/cn_01.docx"
OUR, BUYER = "星河虚构进出口有限公司", "青岚虚构设备1有限公司"


@pytest.fixture(autouse=True)
def no_ner(monkeypatch):
    from app.redaction import ner
    monkeypatch.setattr(ner, "recognize", lambda text: [])


@pytest.fixture
def model(monkeypatch, env):
    """审核请求按 mock_llm 的 normal 规则回 JSON；问答请求流式回复 reply[0]。"""
    seen, reply = [], ["好的。"]

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        if "【审核要求】" in body["messages"][-1]["content"]:
            status, payload = build_reply(body, "normal")
            return httpx.Response(status, json=payload)
        return httpx.Response(200, stream=Events([reply[0]]))

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    return seen, reply


def _job(sm, kind, tid):
    with sm() as db:
        return db.scalar(select(Job).where(Job.type == kind, Job.payload["task_id"].astext == tid)
                         .order_by(Job.id.desc()))


def _session(c, h, sm):
    tid = c.post(BASE, headers=h).json()["id"]
    r = c.post(f"{BASE}/{tid}/files", headers=h, files=[("files", ("虚构合同.docx", SAMPLE.read_bytes()))])
    assert r.status_code == 200, r.text
    with sm() as db:
        job = _job(sm, "parse", tid)
        pipeline.handle_parse(db, job.payload)
        db.execute(update(Job).where(Job.id == job.id).values(status="done"))  # worker 完成后的状态
        db.commit()
    parties = c.get(f"{API_PREFIX}/tasks/{tid}/parties").json()["items"]
    with sm() as db:
        mapping = engine.load_redaction_map(db, uuid.UUID(tid))
    items = [{"id": p["id"], "is_our_party": mapping[p["code"]] == OUR, "country": p["country"],
              "org_type": p["org_type"]} for p in parties]
    assert c.put(f"{API_PREFIX}/tasks/{tid}/parties", headers=h, json={"items": items}).status_code == 200
    r = c.post(f"{BASE}/{tid}/confirm", headers=h)
    assert r.status_code == 200, r.text
    return tid, r.json()


def _code(sm, tid, value):
    with sm() as db:
        return next(k for k, v in engine.load_redaction_map(db, uuid.UUID(tid)).items() if v == value)


def test_session_chat_then_review_shares_codes(files_dir, model, make_user, login, db_sessionmaker):
    seen, reply = model
    user = make_user("workspace")
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    tid, detail = _session(c, h, db_sessionmaker)
    confirmed_at = detail["files"][0]["confirmed_at"]
    buyer, our = _code(db_sessionmaker, tid, BUYER), _code(db_sessionmaker, tid, OUR)
    assert our.startswith("[[ORG_") and buyer.startswith("[[ORG_")  # 会话不分配 [[OUR]]

    # 先问答
    assert c.post(f"{BASE}/{tid}/messages", headers=h, json={"message": "买方是谁？"}).status_code == 201
    from app.assistant.job import handle_assist
    with db_sessionmaker() as db:
        handle_assist(db, _job(db_sessionmaker, "assist", tid).payload)
    chat_text = json.dumps(seen[-1]["messages"], ensure_ascii=False)
    assert buyer in chat_text and BUYER not in chat_text

    # 再审核：材料版本对上才能发起；审核排队时不能发送消息
    version = c.get(f"{BASE}/{tid}").json()["material_version"]
    body = {"stance": "sales", "source": "outgoing", "expected_material_version": version - 1}
    assert c.post(f"{BASE}/{tid}/runs", headers=h, json=body).json()["code"] == "MATERIAL_CHANGED"
    r = c.post(f"{BASE}/{tid}/runs", headers=h, json={**body, "expected_material_version": version})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    busy = c.post(f"{BASE}/{tid}/messages", headers=h, json={"message": "再问一句"})
    assert busy.status_code == 409 and busy.json()["code"] == "SESSION_BUSY"
    assert c.get(f"{BASE}/{tid}").json()["review_running"] is True
    with db_sessionmaker() as db:
        review_job.run_review(db, uuid.UUID(rid))
    review_text = seen[-1]["messages"][-1]["content"]  # 只看外发的用户消息（系统提示里的代号示例不算）
    assert buyer in review_text and BUYER not in review_text and OUR not in review_text
    assert f"{our}：我方，卖方" in review_text and "[[OUR]]" not in review_text and "[我方]" not in review_text

    # 状态与确认：会话仍是 active，文件只确认过一次
    after = c.get(f"{BASE}/{tid}").json()
    assert after["status"] == "active" and after["review_running"] is False
    assert after["files"][0]["confirmed_at"] == confirmed_at
    assert [i["id"] for i in c.get(f"{BASE}/{tid}/runs").json()["items"]] == [rid]

    # 回程：问题定位还原为“文件名 + 位置”，文字中的中性代号还原为真实名称
    run = c.get(f"{API_PREFIX}/runs/{rid}").json()
    assert run["status"] == "completed" and run["findings"]
    finding = run["findings"][0]
    assert finding["location"].startswith("虚构合同.docx")
    with db_sessionmaker() as db:
        row = db.get(ReviewRun, uuid.UUID(rid))
        assert row.file_ids == [after["files"][0]["id"]] and row.material_version == version
        db.execute(update(Finding).where(Finding.run_id == row.id).values(problem=f"{buyer}应在签约后付款"))
        db.commit()
        mapping = engine.load_redaction_map(db, row.task_id)
    assert c.get(f"{API_PREFIX}/runs/{rid}").json()["findings"][0]["problem"] == f"{BUYER}应在签约后付款"

    # 审核后的问答：本地算式用同一套金额代号计算
    parts = [k for k, v in mapping.items() if k.startswith("[[AMT_") and ("60,000.00" in v or "40,000.00" in v)]
    assert len(parts) == 2, mapping.keys()
    reply[0] = f"【算式】合计 = {parts[0]} + {parts[1]}"
    assert c.post(f"{BASE}/{tid}/messages", headers=h, json={"message": "核对总价"}).status_code == 201
    with db_sessionmaker() as db:
        handle_assist(db, _job(db_sessionmaker, "assist", tid).payload)
    last = c.get(f"{BASE}/{tid}/messages").json()["items"][-1]
    assert last["calcs"][0]["result"].replace(",", "") == "100000.00 CNY"


def test_local_only_session_never_calls_model(files_dir, model, make_user, login, db_sessionmaker):
    user = make_user("workspace_local")
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    tid, _ = _session(c, h, db_sessionmaker)
    with db_sessionmaker() as db:
        db.get(Task, uuid.UUID(tid)).policy = "local_only"
        db.commit()
    r = c.post(f"{BASE}/{tid}/runs", headers=h, json={"stance": "sales", "source": "outgoing"})
    assert r.status_code == 409 and r.json()["code"] == "LOCAL_ONLY"
    assert c.get(f"{BASE}/{tid}/local-findings").status_code == 200  # 本地校验照常可看
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 0
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 0
    assert model[0] == []


def test_date_shift_new_session_only(files_dir, model, make_user, login, db_sessionmaker):
    from app.db.models import DocNode
    user = make_user("workspace_dates")
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    new = c.post(BASE, headers=h).json()["id"]
    old = c.post(BASE, headers=h).json()["id"]
    with db_sessionmaker() as db:
        # 旧会话：对照表非空且没有平移键（V3.2 之前建的会话）
        engine.write_full_map(db, uuid.UUID(old), {"[[ORG_9]]": "虚构旧公司"})
        db.commit()
        assert int(engine._load_full_map(db, uuid.UUID(new))["@date_shift_days"]) != 0
    text = "交货日期为2026年3月5日。"
    for tid in (new, old):
        with db_sessionmaker() as db:
            task = db.get(Task, uuid.UUID(tid))
            f = TaskFile(task_id=task.id, seq=1, is_main=True, original_name="虚构.docx", ext="docx", size_bytes=1,
                         sha256="0" * 64, storage_key=uuid.uuid4().hex, parser_version="t")
            db.add(f); db.flush()
            from app.security import crypto
            db.add(DocNode(task_id=task.id, file_id=f.id, loc="F1-P1", ord=1, kind="paragraph", part="body",
                           text_enc=crypto.encrypt_text(text)))
            db.flush()
            engine.redact_task(db, task, style="neutral", pin=True)
            db.commit()
            node = db.scalar(select(DocNode).where(DocNode.task_id == task.id))
            if tid == new:
                assert "2026年3月5日" not in node.redacted_text and engine.load_date_pairs(db, task.id)
            else:
                assert node.redacted_text == text and "@date_shift_days" not in engine._load_full_map(db, task.id)
