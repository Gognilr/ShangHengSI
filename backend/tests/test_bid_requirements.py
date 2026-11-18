"""V34-B-02：要求清单的规则抽取、完整性计数、关键参数与模型补充；样本全部虚构。"""
import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.bid.extract import handle_bid
from app.db.models import BidProject, Job, LlmCall, Task
from tests.fixtures.bid.samples import TENDER_STARS, TENDER_VETOES, tender
from tests.review_support import env, llm  # noqa: F401
from tests.test_bid_projects import BODY, ROOT, drain, owner  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


def project(c, h, sm, policy="local_only"):
    base = ROOT + "/" + c.post(ROOT, headers=h, json={**BODY, "policy": policy}).json()["id"]
    assert c.post(base + "/files", headers=h, files={"files": ("虚构招标文件.docx", tender())}).status_code == 200
    drain(sm, base)
    return base


def run_bid_jobs(sm, base):
    tid = base.rsplit("/", 1)[-1]
    with sm() as db:
        for job in db.scalars(select(Job).where(Job.type == "bid", Job.payload["task_id"].astext == tid, Job.status == "queued")).all():
            job.status = "done"
            db.commit()
            handle_bid(db, job.payload)


def test_rules_counter_parameters_and_confirm(owner, db_sessionmaker):
    from app.bid import requirements as R
    header = '★ 专 业：编 制：校 对：审 核：机械会签：日 期：2028.2 注：卖方必须在偏离清单中列明全部技术偏离。'
    hit = R.rule_hits([R.Node('f', 'F1-P1', 'ocr_block', header)])[0]
    assert hit.text == '卖方必须在偏离清单中列明全部技术偏离。' and hit.marked
    assert hit.locs[0]['start'] == 0  # 保留完整来源和原标记覆盖，不改原件
    c, h, _ = owner
    base = project(c, h, db_sessionmaker)
    data = c.get(base + "/requirements").json()
    texts = [i["text"] for i in data["items"]]
    # 原文 ★ 与否决句全部进入清单
    assert data["counts"]["star"] == {"total": TENDER_STARS, "covered": TENDER_STARS, "missing": []}
    assert data["counts"]["veto"]["total"] == TENDER_VETOES and data["counts"]["veto"]["covered"] == TENDER_VETOES
    assert any("测量精度" in t and "0.01" in t for t in texts)  # 表格中的 ★ 整行一条
    assert not any("开具发票" in t for t in texts) and not any("格式范本" in t for t in texts)
    assert any(i["group"] == "format" for i in data["items"])
    p = data["parameters"]
    assert (p["ceiling"], p["validity_days"], p["deposit"], p["consortium"], p["tax_included"]) == ("3800000.00", 90, "50000.00", False, True)
    defaults = [i for i in data["items"] if i["source"] == "default"]
    assert len(defaults) == 7 and not next(i for i in defaults if "联合体" in i["text"])["enabled"]
    assert all(i["method"] != "model" for i in data["items"])  # 只做本地检查：模型判断一律转人工

    # 停用一条 ★，计数器列出差额；手工行在重新抽取后保留
    star = next(i for i in data["items"] if "营业执照" in i["text"])
    data = c.patch(f"{base}/requirements/{star['id']}", headers=h, json={"enabled": False}).json()
    assert data["counts"]["star"]["covered"] == TENDER_STARS - 1 and "营业执照" in data["counts"]["star"]["missing"][0]["text"]
    assert c.post(base + "/requirements", headers=h, json={"group": "signing", "text": "虚构：报价表须加盖骑缝章"}).status_code == 201
    data = c.post(base + "/requirements/extract", headers=h).json()
    assert data["counts"]["star"]["covered"] == TENDER_STARS and any(i["source"] == "manual" for i in data["items"])

    assert c.put(base + "/parameters", headers=h, json={**p, "ceiling": "abc"}).status_code == 422
    assert c.put(base + "/parameters", headers=h, json={**p, "ceiling": "3,600,000.005"}).json()["parameters"]["ceiling"] == "3600000.01"
    assert c.post(base + "/assist/start", headers=h).status_code == 409
    assert c.post(base + "/confirm", headers=h).json()["stage"] == "ready"
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 0


def test_model_supplement_redacts_and_validates_quotes(owner, db_sessionmaker, llm):
    c, h, _ = owner
    base = project(c, h, db_sessionmaker, policy="ai")
    tender_view = c.get(base + "/tender").json()
    loc = next(n["loc"] for n in tender_view["nodes"] if "密封" in n["text"])
    before = len(c.get(base + "/requirements").json()["items"])
    assert c.post(base + "/assist/start", headers=h).json()["extract_state"] == "redacting"
    run_bid_jobs(db_sessionmaker, base)
    data = c.get(base + "/requirements").json()
    assert data["extract_state"] == "awaiting" and data["receipt"]["calls"] == 1
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body["messages"][1]["content"])
        content = json.dumps({"items": [
            {"group": "signing", "text": "投标文件须密封递交", "loc": loc, "quote": "投标文件未按要求密封的"},  # 与规则行重叠，不重复
            {"group": "qualification", "text": "测量精度要求", "loc": "不存在的位置", "quote": "测量精度"},  # 位置无效，丢弃
            {"group": "pricing", "text": "报价须为含税价", "loc": next(n["loc"] for n in tender_view["nodes"] if "含税价" in n["text"]),
             "quote": "报价为含税价"}]}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    llm(handler=handler)
    assert c.post(base + "/assist/confirm", headers=h).json()["extract_state"] == "extracting"
    busy = c.patch(base, headers=h, json=BODY)
    assert busy.status_code == 409 and "补充要求" in busy.json()["message"]
    assert c.post(base + "/requirements/extract", headers=h).json()["code"] == "EXTRACT_BUSY"
    run_bid_jobs(db_sessionmaker, base)
    data = c.get(base + "/requirements").json()
    assert data["extract_state"] == "done"
    added = [i for i in data["items"] if i["source"] == "model"]
    assert [i["text"] for i in added] == ["报价须为含税价"] and len(data["items"]) == before + 1
    assert "松青示范仪器" not in sent[0] or "[" in sent[0]  # 只发脱敏后的招标文件
    with db_sessionmaker() as db:
        call = db.scalars(select(LlmCall).where(LlmCall.task_id == uuid.UUID(base.rsplit("/", 1)[-1]))).one()
        assert call.purpose == "bid"
        assert sent[0] in c.get(base + "/sent").json()[0]["text"]
        call.request_text = None  # 外发正文保留期已到
        db.commit()
    closed = c.post(base + "/assist/cancel", headers=h).json()
    assert closed["extract_state"] is None and closed["sent_count"] == 1
    assert c.get(base).json()["sent_count"] == c.get(base + "/model").json()["sent_count"] == 1
    records = c.get(base + "/sent").json()
    assert len(records) == 1 and records[0]["text"] is None


def test_switch_to_local_invalidates_receipt_and_normalizes_requirements(owner, db_sessionmaker, llm):
    c, h, _ = owner
    base = project(c, h, db_sessionmaker, policy="ai")
    sent = llm()
    assert c.post(base + "/assist/start", headers=h).json()["extract_state"] == "redacting"
    busy = c.patch(base, headers=h, json=BODY)
    assert busy.status_code == 409 and "脱敏" in busy.json()["message"]
    run_bid_jobs(db_sessionmaker, base)
    before = c.get(base + "/requirements").json()
    assert before["receipt"] and any(row["method"] == "model" for row in before["items"])

    assert c.patch(base, headers=h, json=BODY).json()["policy"] == "local_only"
    data = c.get(base + "/requirements").json()
    assert data["extract_state"] is None and data["receipt"] is None
    assert data["version"] == before["version"] + 1
    assert all(row["method"] != "model" for row in data["items"])
    assert c.post(base + "/assist/confirm", headers=h).json()["code"] == "LOCAL_ONLY"

    added = c.post(base + "/requirements", headers=h, json={"group": "signing", "text": "虚构人工核对要求", "method": "model"})
    assert added.status_code == 201
    row = next(row for row in added.json()["items"] if row["source"] == "manual")
    assert row["method"] == "manual"
    patched = c.patch(f"{base}/requirements/{row['id']}", headers=h, json={"method": "model", "enabled": False})
    assert patched.status_code == 200
    assert next(i for i in patched.json()["items"] if i["id"] == row["id"])["method"] == "manual"

    assert c.patch(base, headers=h, json={**BODY, "policy": "ai"}).status_code == 200
    assert c.post(base + "/assist/confirm", headers=h).json()["code"] == "EXTRACT_STATE"
    assert not sent


@pytest.mark.parametrize(("stage", "state_field", "state"), [
    ("redact", "extract_state", "redacting"),
    ("extract", "extract_state", "extracting"),
    ("snippets", "model_state", "preparing"),
    ("model", "model_state", "running"),
])
def test_old_queued_model_work_finishes_under_local_policy(stage, state_field, state, owner, db_sessionmaker, llm):
    from app.db import jobs
    from app.security import crypto

    c, h, _ = owner
    base = ROOT + "/" + c.post(ROOT, headers=h, json={**BODY, "policy": "ai"}).json()["id"]
    task_id = uuid.UUID(base.rsplit("/", 1)[-1])
    sent = llm()
    with db_sessionmaker() as worker:
        cached = worker.get(Task, task_id)
        worker.commit()
        with db_sessionmaker() as db:
            legacy = db.get(BidProject, task_id)
            setattr(legacy, state_field, state)
            legacy.model_enc = crypto.encrypt_text('{}')
            jobs.enqueue(db, "bid", {"task_id": str(task_id), "stage": stage})
            db.get(Task, task_id).policy = "local_only"  # 模拟旧版本允许切换后留下的排队作业
            db.commit()
        assert cached.policy == "ai"
        job = jobs.claim(worker, "test-bid-policy", ("bid",))
        assert job is not None
        handle_bid(worker, job.payload)
        jobs.complete(worker, job.id)
        worker.refresh(job)
        assert job.status == "done"
        stopped = worker.get(BidProject, task_id, populate_existing=True)
        assert stopped.extract_state is None and stopped.model_state is None and stopped.model_enc is None
        assert cached.policy == "local_only" and not sent


def test_legacy_job_cannot_cross_policy_reenable(owner, db_sessionmaker):
    from app.db import jobs
    from app.worker import process_job

    c, h, _ = owner
    base = ROOT + "/" + c.post(ROOT, headers=h, json=BODY).json()["id"]
    task_id = base.rsplit("/", 1)[-1]
    with db_sessionmaker() as db:
        jobs.enqueue(db, "bid", {"task_id": task_id, "stage": "model"})
        db.commit()

    enable = {**BODY, "policy": "ai"}
    reply = c.patch(base, headers=h, json=enable)
    assert reply.status_code == 409 and reply.json()["code"] == "MODEL_JOBS_PENDING"
    with db_sessionmaker() as db:
        job = jobs.claim(db, "test-bid-policy", ("bid",))
        assert job is not None
        job_id, payload = job.id, job.payload
    reply = c.patch(base, headers=h, json=enable)
    assert reply.status_code == 409 and reply.json()["code"] == "MODEL_JOBS_PENDING"
    process_job(db_sessionmaker, job_id, "bid", payload)
    assert c.patch(base, headers=h, json=enable).json()["policy"] == "ai"
    assert c.post(base + "/model/confirm", headers=h).json()["code"] == "MODEL_STATE"
