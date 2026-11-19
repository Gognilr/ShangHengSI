"""V34-B-02：要求清单的规则抽取、完整性计数、关键参数与模型补充；样本全部虚构。"""
import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.bid.extract import handle_bid
from app.db.models import BidProject, Job, LlmCall, Task
from tests.fixtures.bid.samples import TENDER_STARS, TENDER_SUPPLEMENT_CLAUSE, TENDER_VETOES, tender
from tests.review_support import env, llm  # noqa: F401
from tests.test_bid_projects import BODY, ROOT, drain, owner  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


def project(c, h, sm, policy="local_only", *, supplement=False):
    base = ROOT + "/" + c.post(ROOT, headers=h, json={**BODY, "policy": policy}).json()["id"]
    files = {"files": ("虚构招标文件.docx", tender(supplement=supplement))}
    assert c.post(base + "/files", headers=h, files=files).status_code == 200
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
    assert c.put(base + "/parameters", headers=h, json={**p, "ceiling": "1e30"}).status_code == 422
    assert c.put(base + "/parameters", headers=h, json={**p, "ceiling": "3,600,000.005"}).json()["parameters"]["ceiling"] == "3600000.01"
    assert c.post(base + "/assist/start", headers=h).status_code == 409
    assert c.post(base + "/confirm", headers=h).json()["stage"] == "ready"
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 0


def test_model_supplement_redacts_and_validates_quotes(owner, db_sessionmaker, llm):
    from app.admin.settings_store import set_setting

    c, h, _ = owner
    with db_sessionmaker() as db:
        set_setting(db, "redaction.date_shift", True, None)
        db.commit()
    base = project(c, h, db_sessionmaker, policy="ai", supplement=True)
    tender_view = c.get(base + "/tender").json()
    clause = next(n for n in tender_view["nodes"] if n["text"] == TENDER_SUPPLEMENT_CLAUSE)
    clause_loc = clause["loc"]
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
        prefix = f"[{clause_loc}] "
        outbound_quote = next(line[len(prefix):] for line in sent[-1].splitlines() if line.startswith(prefix))
        assert "2028年6月15日" in outbound_quote and "比例为 2%" in outbound_quote
        assert "[[AMT_" in outbound_quote and "20,000.00" not in outbound_quote
        assert "（=合同总价的" not in sent[-1]
        content = json.dumps({"items": [
            {"group": "validity", "text": outbound_quote, "loc": clause_loc, "quote": outbound_quote},
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
    assert [i["text"] for i in added] == [TENDER_SUPPLEMENT_CLAUSE, "报价须为含税价"]
    assert len(data["items"]) == before + 2
    assert added[0]["locs"] == [{"file_id": clause["file_id"], "loc": clause_loc, "start": 0,
                                 "end": len(TENDER_SUPPLEMENT_CLAUSE)}]
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


def test_legacy_supplement_preview_must_be_prepared_again(owner, db_sessionmaker, llm):
    from app.db import jobs
    from app.redaction import engine

    c, h, _ = owner
    base = project(c, h, db_sessionmaker, policy="ai", supplement=True)
    task_id = uuid.UUID(base.rsplit("/", 1)[-1])
    sent = llm()
    with db_sessionmaker() as db:
        engine.redact_task(db, db.get(Task, task_id))  # 旧版本用审核脱敏
        db.get(BidProject, task_id).extract_state = "awaiting"
        db.commit()

    legacy_nodes = c.get(base + "/tender", params={"view": "ai"}).json()["nodes"]
    legacy_quote = next(node["text"] for node in legacy_nodes if "样机递交截止日期为" in node["text"])
    assert "2028年6月15日" not in legacy_quote and "（=合同总价的" in legacy_quote
    old = c.get(base + "/requirements").json()
    assert old["extract_state"] == "failed" and old["receipt"] is None
    assert c.post(base + "/assist/confirm", headers=h).json()["code"] == "EXTRACT_STALE"
    with db_sessionmaker() as db:
        db.get(BidProject, task_id).extract_state = "extracting"
        jobs.enqueue(db, "bid", {"task_id": str(task_id), "stage": "extract"})
        db.commit()
    run_bid_jobs(db_sessionmaker, base)
    assert c.get(base + "/requirements").json()["extract_state"] == "failed" and not sent

    assert c.post(base + "/assist/start", headers=h).json()["extract_state"] == "redacting"
    run_bid_jobs(db_sessionmaker, base)
    assert c.get(base + "/requirements").json()["receipt"]["calls"] == 1


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


def test_rules_directives_numbered_notes_and_same_text_merged():
    """V34-B-08c：虚构改写自公开招标文件的写法——指令词、编号注释、括号补充、同文合并、付款条款与大写保证金。"""
    from app.bid import requirements as R
    note = ("编 制：校 对：日 期：2029.3 注：1、卖方必须在偏离清单中注明全部偏离。偏离须经设计方书面批准 "
            "2、卖方应在数据表中标明最小稳定流量。 3、法兰连接标准为HG/T 20592 4、所有设备不带配对法兰")
    nodes = [R.Node("f1", "F1-PG1-T1-R8-C1", "table_cell", note), R.Node("f2", "F2-PG1-T1-R8-C1", "table_cell", note),
             R.Node("f3", "F3-P5", "paragraph", "投标文件要求：标书需提供资质证书、营业执照。标书中请明确完成时间。"),
             R.Node("f3", "F3-P6", "paragraph", "报价请注明：虚构项目总计报价 *** 元。（注明发票税率）"),
             R.Node("f3", "F3-P7", "paragraph", "付款方式：每笔付款前需开具增值税专用发票。"),
             R.Node("f3", "F3-P8", "paragraph", "招标保证金：叁万元整。请在开标前电汇投标保证金，无需现金。")]
    hits = R.rule_hits(nodes)
    texts = [h.text for h in hits]
    assert sum("偏离清单" in t for t in texts) == 1 and len(next(h for h in hits if "偏离清单" in h.text).locs) == 2  # 两份数据表合并
    assert any(t.startswith("2、卖方应在数据表") for t in texts) and not any("批准 2、" in t for t in texts)  # 编号处断句
    assert any("法兰连接标准" in t for t in texts) and any("不带配对法兰" in t for t in texts)  # 同一注释的其余编号条目
    assert any("需提供资质证书" in t for t in texts) and "标书中请明确完成时间。" in texts
    assert any(t.endswith("（注明发票税率）") for t in texts)  # 括号补充并入上一条
    assert not any("开具增值税" in t for t in texts)  # 中标后的付款条款不取
    assert R.prefill(nodes)[0]["deposit"] == "30000.00"  # 大写保证金


def test_supplement_near_duplicates_merge_and_snippet_ids_become_pages():
    """V34-B-08c：模型补充的近似重复只并入出处；理由中的片段编号换成页码。虚构文字。"""
    from types import SimpleNamespace

    from app.bid.extract import _is_description, _same_requirement
    from app.bid.model import snippet_refs
    from app.security import crypto
    row = SimpleNamespace(enabled=True, text_enc=crypto.encrypt_text("卖方须返回详细的公用工程消耗表，用电消耗应包含所有附属设备的用电消耗。"))
    assert _same_requirement("卖方须返回详细的公用工程消耗表，其中用电消耗需包含所有附属设备的用电消耗。", [row]) is row
    assert _same_requirement("工作点应在最佳效率点流量的70%～120%内。", [row]) is None
    assert _same_requirement("所有设备不带配对法兰及紧固件。", [row]) is None
    # 工况、环境数据是说明，不是要求；有要求用语的技术条目照列
    assert _is_description("substantive", "P-901A/B泵操作介质为轻质油，环境温度最低/最高为-10/40℃。")
    assert not _is_description("substantive", "P-901A/B泵应适用于爆炸危险2区。")
    assert not _is_description("signing", "商务标书、技术标书各一份。")
    text = snippet_refs("C24将电机单独报价，[C5]列在表一；位号P-102A、GB/T3836、C9不变", {"C24": {"page": 10}, "C5": {"page": 3}})
    assert text == "第 10 页将电机单独报价，第 3 页列在表一；位号P-102A、GB/T3836、C9不变"
