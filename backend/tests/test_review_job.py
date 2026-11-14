"""review 处理函数（M2-12）：成功、部分成功、网关拦截（T-07）、各类失败、不外发（A-04）、任务被删除。需要 TEST_DATABASE_URL。"""
import json

import httpx
import pytest
from sqlalchemy import func, select

from app import worker
from app.admin import settings_store
from app.db import jobs
from app.db.models import (
    CompanyRequirement,
    DocNode,
    Finding,
    Job,
    LlmCall,
    Party,
    RequirementResult,
    ReviewRun,
    Task,
)
from app.redaction import engine
from app.review import job, service
from app.security import crypto
from tests.mock_llm import build_content, build_reply
from tests.review_support import configure_model, REAL_BUYER_NAME, REAL_OUR_NAME, env, llm, ready_task  # noqa: F401


@pytest.fixture(autouse=True)
def single_requirement_group(monkeypatch):
    """原有单次与长文件分段用例保留原断言；四组并发的真实链路另见 test_review_dimensions。"""
    monkeypatch.setattr(job, "group_requirements", lambda requirements: [requirements])


def test_t45_review_refs_snapshot_and_dropped(db_sessionmaker,owner,ready_task,llm,monkeypatch):
    from app.laws import retrieve
    task=ready_task(owner)
    with db_sessionmaker() as db:
        settings_store.set_setting(db,'laws.refs_enabled',True,None);db.commit()
    monkeypatch.setattr(retrieve.engine,'embed',lambda texts,kind:[([1.0]+[0.0]*1023) for _ in texts])
    monkeypatch.setattr(retrieve,'search',lambda *a:[
        dict(id=1,title='示例供货法',article_no='第三条',text='应依约交付。',score=0.9),
        dict(id=2,title='示例供货法',article_no='第四条',text=REAL_OUR_NAME,score=0.8)])
    def mutate(output,messages): output['findings'][0]['law_refs']=['LAW1','LAW99']
    requests=llm(handler=custom_reply(mutate))
    run=new_run(db_sessionmaker,task,owner);run_job(db_sessionmaker,run.id)
    assert len(requests)==1
    message=json.loads(requests[0].content)['messages'][1]['content']
    assert '[LAW1]《示例供货法》第三条：应依约交付。' in message and REAL_OUR_NAME not in message
    with db_sessionmaker() as db:
        f=db.scalar(select(Finding).where(Finding.run_id==run.id))
        assert f.law_refs==[dict(title='示例供货法',article_no='第三条',text='应依约交付。')]
        assert db.get(ReviewRun,run.id).coverage['law_refs']==dict(enabled=True,sent=1,dropped=1,chars=len('《示例供货法》第三条：应依约交付。'))
        assert service.run_detail(db,db.get(ReviewRun,run.id)).findings[0].law_refs==f.law_refs


def test_t45_each_chunk_restarts_refs(db_sessionmaker,owner,ready_task,llm,monkeypatch):
    from app.laws import retrieve
    task=chunked_task(db_sessionmaker,owner,ready_task)
    seen=[]
    with db_sessionmaker() as db:
        settings_store.set_setting(db,'laws.refs_enabled',True,None);db.commit()
    original=retrieve.prepare
    def prepare(db,ctx,chunk,nodes):
        seen.append(chunk.index)
        ctx.law_refs=[dict(ref='LAW1',title='示例供货法',article_no='第三条',text='应依约交付。')]
    monkeypatch.setattr(retrieve,'prepare',prepare)
    requests=llm('normal')
    run=new_run(db_sessionmaker,task,owner);run_job(db_sessionmaker,run.id)
    assert len(seen)>=2 and len(seen)==len(requests)
    for request in requests:
        message=json.loads(request.content)['messages'][1]['content']
        assert message.count('[LAW1]')==1 and '[LAW2]' not in message


@pytest.fixture
def owner(make_user):
    return make_user("alice")


def new_run(db_sessionmaker, task: Task, owner) -> ReviewRun:
    """走真实的确认发送逻辑建轮次（含预检）。"""
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        return service.create_run(db, t, owner, None)


def run_job(db_sessionmaker, run_id) -> None:
    with db_sessionmaker() as db:
        job.run_review(db, run_id)


def get_run(db_sessionmaker, run_id) -> ReviewRun:
    with db_sessionmaker() as db:
        return db.get(ReviewRun, run_id)


def test_compact_reply_persists_and_returns_existing_detail(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)

    def compact(output, messages):
        rows = output["requirement_results"]
        output["ok"] = [r["req_id"] for r in rows if r["verdict"] == "ok"]
        output["na"] = []
        output["requirement_results"] = [r for r in rows if r["verdict"] != "ok"]

    requests = llm(handler=custom_reply(compact))
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    assert len(requests) == 1
    with db_sessionmaker() as db:
        current = db.get(ReviewRun, run.id)
        assert current.status == "completed"
        detail = service.run_detail(db, current)
        assert detail.requirement_results[0].verdict == "issue"
        assert all(r.verdict == "ok" and r.note is None for r in detail.requirement_results[1:])
        assert len(detail.findings) == 1


@pytest.mark.parametrize("status", ["completed", "partial", "blocked", "failed"])
def test_v2c_all_terminal_runs_update_activity(db_sessionmaker, owner, ready_task, llm, status):
    from datetime import timedelta
    from app.db.base import utcnow
    task = ready_task(owner, user_note="请检查付款条款")
    if status == "partial":
        llm(handler=custom_reply(lambda output, messages: output["requirement_results"].pop()))
    else:
        llm("normal")
    run = new_run(db_sessionmaker, task, owner)
    before = utcnow() - timedelta(days=5)
    with db_sessionmaker() as db:
        db.get(Task, task.id).last_activity_at = before
        if status == "blocked":
            from app.db.models import DocNode
            db.scalar(select(DocNode).where(DocNode.task_id == task.id)).redacted_text = "联系电话 13800138000"
        elif status == "failed":
            from app.admin.settings_store import set_setting
            from app.db.models import LlmModel
            db.get(LlmModel, 1).enabled = False
        db.commit()
    run_job(db_sessionmaker, run.id)
    assert get_run(db_sessionmaker, run.id).status == status
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).last_activity_at > before


def llm_calls(db_sessionmaker) -> list[LlmCall]:
    with db_sessionmaker() as db:
        return list(db.scalars(select(LlmCall).order_by(LlmCall.id)))


def custom_reply(mutate):
    """在模拟服务的正常回复上做修改后返回：mutate(output_dict, messages)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        status, payload = build_reply(body)
        output = json.loads(payload["choices"][0]["message"]["content"])
        mutate(output, body["messages"])
        payload["choices"][0]["message"]["content"] = json.dumps(output, ensure_ascii=False)
        return httpx.Response(status, json=payload)

    return handler


# ---------------------------------------------------------------- 成功


def test_review_success_writes_results(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm("normal")
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)

    with db_sessionmaker() as db:
        r = db.get(ReviewRun, run.id)
        t = db.get(Task, task.id)
        assert (r.status, r.error_message, r.model_name) == ("completed", None, "mock-model")
        assert r.started_at is not None and r.finished_at is not None
        assert r.doc_summary.startswith("模拟摘要") and r.sanctions_level == "none"
        assert r.tokens_in > 0 and r.tokens_out > 0
        assert r.coverage["requirements"]["total"] > 10
        assert r.coverage["requirements"]["issue"] == 1
        assert r.coverage["sanctions_offline"] is True
        assert (t.status, t.high_count) == ("reviewed", 0) and t.last_completed_at is not None and t.latest_run_id == r.id
        # A-07：结论数 = 发送的要求数
        results = list(db.scalars(select(RequirementResult).where(RequirementResult.run_id == r.id)
                                  .order_by(RequirementResult.id)))
        sent = service.collect_requirements(db, doc_type="contract", stance="sales", source="outgoing", user_note=None)
        assert [x.req_id for x in results] == [q.id for q in sent] and len(results) > 10
        assert results[0].verdict == "issue" and {x.verdict for x in results[1:]} == {"ok"}
        # 问题：代号形式入库，引用快照加密
        (f,) = db.scalars(select(Finding).where(Finding.run_id == r.id))
        assert (f.seq, f.cite_status, f.handling_status, f.task_id) == (1, "verified", "pending", task.id)
        assert f.quote_snapshot_enc is not None and "国际销售合同".encode() not in f.quote_snapshot_enc
        assert crypto.decrypt_text(f.quote_snapshot_enc) == "国际销售合同"
        # 外发记录：一次 sent，请求中没有任何真实值（A-03）
        (call,) = llm_calls(db_sessionmaker)
        assert (call.status, call.purpose, call.run_id, call.task_id) == ("sent", "review", r.id, task.id)
        for real in (REAL_OUR_NAME, REAL_BUYER_NAME, "13800138000"):
            assert real not in call.request_text
    assert len(requests) == 1


def test_high_count_and_partial(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)

    def mutate(output, messages):
        output["findings"][0]["severity"] = "高"
        output["findings"].append({**output["findings"][0], "title": "另一个高风险问题", "locs": ["F1-P2"],
                                   "quote": "合同编号"})
        output["requirement_results"].pop()  # 漏掉最后一条要求（NOTE）→ 补 unable → partial

    llm(handler=custom_reply(mutate))
    with db_sessionmaker() as db:
        db.get(Task, task.id).user_note_enc = crypto.encrypt_text("请重点检查付款条款")
        db.get(Task, task.id).user_note_redacted = "请重点检查付款条款"
        db.commit()
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    with db_sessionmaker() as db:
        r = db.get(ReviewRun, run.id)
        assert r.status == "partial" and db.get(Task, task.id).high_count == 2
        assert r.coverage["requirements"]["unable"] == 1
        assert any(item["req_id"] == "NOTE" for item in r.coverage["unable"])
        last = db.scalars(select(RequirementResult).where(RequirementResult.run_id == r.id)
                          .order_by(RequirementResult.id.desc())).first()
        assert (last.req_id, last.req_source, last.verdict, last.note) == ("NOTE", "note", "unable", "模型未返回结论")
        assert db.get(Task, task.id).status == "reviewed"


def test_f01_findings_ordered_by_severity_then_position(db_sessionmaker, owner, ready_task, llm):
    """V321Q-25：入库序号按等级、再按原文位置；缺失类问题排在同级末尾。"""
    task = ready_task(owner)

    def mutate(output, messages):
        base = output["findings"][0]
        output["findings"] = [
            {**base, "title": "低-P1", "severity": "低", "locs": ["F1-P1"]},
            {**base, "title": "高-P3", "severity": "高", "locs": ["F1-P3"]},
            {**base, "title": "中-缺失", "severity": "中", "locs": [], "missing": True},
            {**base, "title": "高-缺失", "severity": "高", "locs": [], "missing": True},
            {**base, "title": "高-P2", "severity": "高", "locs": ["F1-P2"]},
        ]

    llm(handler=custom_reply(mutate))
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    with db_sessionmaker() as db:
        rows = db.scalars(select(Finding).where(Finding.run_id == run.id).order_by(Finding.seq))
        assert [f.title for f in rows] == ["高-P2", "高-P3", "高-缺失", "中-缺失", "低-P1"]


def test_company_requirements_are_sent_and_get_results(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    with db_sessionmaker() as db:
        db.add_all([
            CompanyRequirement(code="CO-1", text="付款条款必须写明开户行", doc_types=["all"], stances=["sales"], sort=1),
            CompanyRequirement(code="CO-2", text="仅适用于采购", doc_types=["all"], stances=["purchase"], sort=2),
            CompanyRequirement(code="CO-3", text="已停用", doc_types=["all"], stances=["all"], enabled=False, sort=3),
            CompanyRequirement(code="CO-4", text="仅适用于报价单", doc_types=["quotation"], stances=["all"], sort=4),
        ])
        db.commit()
    requests = llm("normal")
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    prompt = json.loads(requests[0].content)["messages"][1]["content"]
    assert "[CO-1] （公司补充）付款条款必须写明开户行" in prompt
    for absent in ("CO-2", "CO-3", "CO-4"):
        assert absent not in prompt
    with db_sessionmaker() as db:
        ids = [x.req_id for x in db.scalars(select(RequirementResult).where(RequirementResult.run_id == run.id)
                                            .order_by(RequirementResult.id))]
        assert "CO-1" in ids and ids.index("CO-1") == len(ids) - 1  # 内置之后、NOTE（本例没有）之前


# ---------------------------------------------------------------- 网关拦截（T-07）


def tamper(db_sessionmaker, task: Task, loc: str, text: str) -> str:
    """把某个正文节点的外发文本改成含真实名称（模拟漏替换），返回原来的外发文本。"""
    with db_sessionmaker() as db:
        node = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc == loc))
        old = node.redacted_text
        node.redacted_text = text
        db.commit()
        return old


def test_t07_gateway_block_returns_task_to_awaiting_confirm(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm("normal")
    run = new_run(db_sessionmaker, task, owner)  # 预检通过后才被改动，只有网关能拦住
    old = tamper(db_sessionmaker, task, "F1-P2", f"合同编号：{REAL_OUR_NAME}")
    run_job(db_sessionmaker, run.id)

    assert requests == []  # 没有发送
    with db_sessionmaker() as db:
        r = db.get(ReviewRun, run.id)
        t = db.get(Task, task.id)
        assert r.status == "blocked" and r.finished_at is not None
        assert r.error_message.startswith("出口复检发现未脱敏的信息，已拦截，未发送（已替换的内容在别处仍是原文")
        assert REAL_OUR_NAME not in r.error_message
        assert t.status == "awaiting_confirm"
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
        (call,) = llm_calls(db_sessionmaker)
        assert (call.status, call.request_text, call.response_text) == ("blocked", None, None)
        assert call.block_reason.startswith("MAP@") and REAL_OUR_NAME not in call.block_reason
    # 处理后重新提交：产生第 2 个轮次
    tamper(db_sessionmaker, task, "F1-P2", old)
    second = new_run(db_sessionmaker, task, owner)
    assert second.run_no == 2
    run_job(db_sessionmaker, second.id)
    assert get_run(db_sessionmaker, second.id).status == "completed"
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).latest_run_id == second.id


# ---------------------------------------------------------------- 失败


def test_http_500_retries_then_fails(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm("http_500")
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    assert len(requests) == 3  # 首次 + 重试 2 次
    r = get_run(db_sessionmaker, run.id)
    assert (r.status, r.error_message) == ("failed", "模型服务调用失败（HTTP 500）")
    assert [c.status for c in llm_calls(db_sessionmaker)] == ["error"] * 3
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "reviewed"


def test_bad_output_twice_fails_and_counts_tokens(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm("non_json")
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    assert len(requests) == 2  # 重试 1 次
    r = get_run(db_sessionmaker, run.id)
    assert r.status == "failed" and r.error_message.startswith(job.MSG_BAD_OUTPUT)
    assert '已自动纠正重试 1 次' in r.error_message
    assert r.receipt['calls'] == r.receipt['rechecks'] == 2
    assert r.receipt['blocked'] == 0 and r.receipt['sent_at']
    assert r.tokens_in > 0 and r.tokens_out > 0
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "reviewed" and db.get(Task, task.id).high_count == 0
        assert db.scalar(select(func.count()).select_from(Finding)) == 0


def test_bad_output_once_then_ok(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm(["non_json", "normal"])
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    assert len(requests) == 2
    assert get_run(db_sessionmaker, run.id).status == "completed"


def test_monthly_cap_reached_fails_without_calling_model(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm("normal")
    run = new_run(db_sessionmaker, task, owner)  # V2Q-65：验证确认通过后、worker 执行前额度用尽
    with db_sessionmaker() as db:
        configure_model(db, "model.monthly_call_cap", 1, None)
        db.add(LlmCall(model_id=1, purpose="review", model_name="mock-model", status="sent"))
        db.commit()
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert (r.status, r.error_message) == ("failed", "已达本月调用上限，请联系管理员")
    assert requests == []
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "reviewed"


def test_model_not_configured_fails(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm("normal")
    run = new_run(db_sessionmaker, task, owner)
    with db_sessionmaker() as db:
        from app.db.models import LlmModel
        db.get(LlmModel, 1).enabled = False
        db.commit()
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert (r.status, r.error_message) == ("failed", "所选模型已停用，请重新审核并换一个模型") and requests == []


def test_unexpected_error_marks_failed_and_reraises(db_sessionmaker, owner, ready_task, llm, monkeypatch):
    task = ready_task(owner)
    llm("normal")
    run = new_run(db_sessionmaker, task, owner)

    def boom(*args, **kwargs):
        raise RuntimeError("内部")

    monkeypatch.setattr(job, "review_once", boom)
    with db_sessionmaker() as db, pytest.raises(RuntimeError):
        job.run_review(db, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert (r.status, r.error_message) == ("failed", job.MSG_INTERNAL)
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "reviewed"


# ---------------------------------------------------------------- 不外发（A-04）与边界


def test_a04_local_only_never_calls_model(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner, policy="local_only")
    requests = llm("normal")
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert t.status == "completed_local"
        # 即使有人绕过接口直接塞进一个轮次，处理函数也不会调用模型
        run = ReviewRun(task_id=t.id, run_no=1, status="queued", stance="sales", source="outgoing",
                        main_doc_type="contract", created_by=owner.id)
        db.add(run)
        db.commit()
        run_id = run.id
    run_job(db_sessionmaker, run_id)
    r = get_run(db_sessionmaker, run_id)
    assert (r.status, r.error_message) == ("failed", job.MSG_LOCAL_ONLY)
    assert requests == [] and llm_calls(db_sessionmaker) == []


def test_skips_deleted_finished_and_resumes_running(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm("normal")
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    assert len(requests) == 1
    run_job(db_sessionmaker, run.id)  # 重复投递：已结束，不再调用
    assert len(requests) == 1
    import uuid
    run_job(db_sessionmaker, uuid.uuid4())  # 不存在：不报错
    # worker 异常退出后重新入队：running 的轮次会重新审核，旧结果不会重复
    with db_sessionmaker() as db:
        db.get(ReviewRun, run.id).status = "running"
        db.commit()
    with db_sessionmaker() as db:
        db.execute(Finding.__table__.delete())
        db.execute(RequirementResult.__table__.delete())
        db.commit()
    run_job(db_sessionmaker, run.id)
    assert len(requests) == 2 and get_run(db_sessionmaker, run.id).status == "completed"
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 1


def test_task_deleted_during_model_call(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)

    def handler(request: httpx.Request) -> httpx.Response:
        with db_sessionmaker() as s:  # 用户在审核期间删除了任务
            s.delete(s.get(Task, task.id))
            s.commit()
        status, payload = build_reply(json.loads(request.content))
        return httpx.Response(status, json=payload)

    llm(handler=handler)
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)  # 不报错
    with db_sessionmaker() as db:
        assert db.get(ReviewRun, run.id) is None and db.get(Task, task.id) is None
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
        assert db.scalar(select(func.count()).select_from(RequirementResult)) == 0


def test_review_handler_registered_and_runs_from_queue(db_sessionmaker, owner, ready_task, llm):
    assert worker.HANDLERS["review"] is job.handle_review
    task = ready_task(owner)
    llm("normal")
    run = new_run(db_sessionmaker, task, owner)
    with db_sessionmaker() as db:
        queued = db.scalars(select(Job).where(Job.type == "review")).one()
        assert queued.status == "queued" and queued.payload == {"task_id": str(task.id), "run_id": str(run.id)}
        claimed = jobs.claim(db, "test-worker", ("review",))
        assert claimed.id == queued.id
        worker.HANDLERS[claimed.type](db, claimed.payload)
    assert get_run(db_sessionmaker, run.id).status == "completed"


# ---------------------------------------------------------------- 还原

def test_restore_is_not_stored(db_sessionmaker, owner, ready_task, llm):
    """数据库里始终是代号形式；真实名称只在返回前端时还原。"""
    task = ready_task(owner)

    def mutate(output, messages):
        output["doc_summary"] = "[我方]与[客户_A]签订销售合同"
        output["findings"][0]["problem"] = "[客户_A]的付款方式对[我方]不利"
        output["sanctions"] = {"level": "需注意", "reasons": ["[客户_A]所在地区需关注"]}

    llm(handler=custom_reply(mutate))
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    with db_sessionmaker() as db:
        r = db.get(ReviewRun, run.id)
        assert r.doc_summary == "[我方]与[客户_A]签订销售合同" and r.sanctions_level == "attention"
        assert db.scalar(select(Finding.problem).where(Finding.run_id == r.id)) == "[客户_A]的付款方式对[我方]不利"
        mapping = engine.load_redaction_map(db, task.id)
        assert mapping["[客户_A]"] == REAL_BUYER_NAME and db.scalar(select(func.count()).select_from(Party)) == 2
        detail = service.run_detail(db, r)
    assert detail.doc_summary == f"{REAL_OUR_NAME}与{REAL_BUYER_NAME}签订销售合同"
    assert detail.sanctions.level == "attention" and detail.sanctions.reasons == [f"{REAL_BUYER_NAME}所在地区需关注"]
    assert detail.findings[0].problem == f"{REAL_BUYER_NAME}的付款方式对{REAL_OUR_NAME}不利"


# ---------------------------------------------------------------- 分段审核（M5-01，SPEC 11.5）


def long_contract() -> bytes:
    """约 4000 字的虚构合同：签约方 + 20 条条款，分段长度 1000 时约 5 段。"""
    import io

    import docx

    d = docx.Document()
    d.add_paragraph("国际销售合同")
    d.add_paragraph(f"卖方：{REAL_OUR_NAME}")
    d.add_paragraph(f"买方：{REAL_BUYER_NAME}")
    for i in range(1, 21):
        d.add_paragraph(f"第{i}条 条款{i}")
        d.add_paragraph(f"{i}.1 本条约定双方在履行合同过程中的第{i}项义务，" + "具体内容按双方确认的附件执行，" * 5)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def chunked_task(db_sessionmaker, owner, ready_task):
    task = ready_task(owner, [("长合同.docx", long_contract())])
    with db_sessionmaker() as db:
        configure_model(db, "model.chunk_chars", 1000, None)
        db.commit()
    return task


def chunk_of(request: httpx.Request) -> int:
    import re

    text = json.loads(request.content)["messages"][1]["content"]
    m = re.search(r"分段：第 (\d+)/(\d+) 段", text)
    return int(m.group(1)) if m else 0


def by_chunk(modes: dict[int, str]):
    """按分段号选择模拟服务的回复模式，没有列出的分段正常回复。"""

    def handler(request: httpx.Request) -> httpx.Response:
        status, payload = build_reply(json.loads(request.content), modes.get(chunk_of(request), "normal"))
        return httpx.Response(status, json=payload)

    return handler


def test_t12_chunked_review_success(db_sessionmaker, owner, ready_task, llm):
    task = chunked_task(db_sessionmaker, owner, ready_task)
    requests = llm("normal")
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    n = r.chunk_count
    assert n >= 3 and sorted(chunk_of(q) for q in requests) == list(range(1, n + 1))  # 分段并发发送，顺序不定
    assert r.status in ("completed", "partial") and r.failed_chunks == []
    assert r.coverage["chunked"] is True and r.coverage["no_cross_chunk_check"] is True
    with db_sessionmaker() as db:
        detail = service.run_detail(db, db.get(ReviewRun, run.id))
        assert detail.coverage["summary"].startswith("本次按 ")
        assert f"分 {n} 段审核，未做跨分段的一致性检查" in "\n".join(detail.coverage["notes"])
    with db_sessionmaker() as db:
        results = db.scalars(select(RequirementResult).where(RequirementResult.run_id == run.id)).all()
        sent = service.collect_requirements(db, doc_type="contract", stance="sales", source="outgoing", user_note=None)
        assert sorted(x.req_id for x in results) == sorted(x.id for x in sent)  # A-07
        findings = db.scalars(select(Finding).where(Finding.run_id == run.id)).all()
        assert len(findings) == n  # 模拟服务对每段的第一行给一个问题，位置互不相同
        assert db.get(Task, task.id).status == "reviewed"
    # 每段的正文互不重叠，拼起来就是完整外发文本的正文
    docs = [json.loads(q.content)["messages"][1]["content"].split("<document>\n")[1].split("\n</document>")[0]
            for q in sorted(requests, key=chunk_of)]
    with db_sessionmaker() as db:
        full = engine.build_outbound_text(db, db.get(Task, task.id))
    assert "\n".join(docs) == full.split("\n", 1)[1]
    assert all(len(d) <= 1000 for d in docs)


def test_chunk_failure_gives_partial(db_sessionmaker, owner, ready_task, llm):
    task = chunked_task(db_sessionmaker, owner, ready_task)
    requests = llm(handler=by_chunk({2: "http_500"}))
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert r.status == "partial" and r.error_message == "第 2 段审核失败，结果不完整"
    (f,) = r.failed_chunks
    assert f["index"] == 2 and f["reason"] == "模型服务调用失败（HTTP 500）" and f["first_loc"] and f["last_loc"]
    assert [chunk_of(q) for q in requests].count(2) == 3  # 首次 + 重试 2 次，之后继续第 3 段
    assert max(chunk_of(q) for q in requests) == r.chunk_count
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "reviewed"


def test_bad_output_chunk_counts_tokens(db_sessionmaker, owner, ready_task, llm):
    task = chunked_task(db_sessionmaker, owner, ready_task)
    llm(handler=by_chunk({1: "non_json"}))
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert r.status == "partial" and r.failed_chunks[0]["reason"].startswith(job.MSG_BAD_OUTPUT)
    calls = llm_calls(db_sessionmaker)
    assert r.tokens_in == sum(c.tokens_in for c in calls) and r.tokens_out == sum(c.tokens_out for c in calls)


def test_all_chunks_fail(db_sessionmaker, owner, ready_task, llm):
    task = chunked_task(db_sessionmaker, owner, ready_task)
    llm("http_500")
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert (r.status, r.error_message) == ("failed", "模型服务调用失败（HTTP 500）")
    assert [f["index"] for f in r.failed_chunks] == list(range(1, r.chunk_count + 1))
    assert r.coverage["requirements"]["total"] > 0
    assert (r.coverage["requirements"]["issue"], r.coverage["requirements"]["ok"],
            r.coverage["requirements"]["na"], r.coverage["requirements"]["unable"]) == (0, 0, 0, 0)
    assert len(r.coverage["failed_chunks"]) == r.chunk_count
    with db_sessionmaker() as db:
        detail = service.run_detail(db, db.get(ReviewRun, run.id))
        assert "审核失败，未取得有效 AI 结论" in detail.coverage["summary"]  # V311-FIX-01 改了失败轮次的说明
        assert any("审核失败" in note for note in detail.coverage["notes"])
        assert db.scalar(select(func.count()).select_from(RequirementResult)) == 0


def test_block_in_second_chunk_stops_run(db_sessionmaker, owner, ready_task, llm):
    from app.review.chunking import outbound_lines, split_chunks
    from app.review.prompt import split_outbound

    task = chunked_task(db_sessionmaker, owner, ready_task)
    requests = llm("normal")
    run = new_run(db_sessionmaker, task, owner)
    with db_sessionmaker() as db:
        boundaries = service.chunk_boundaries(db, task.id)
        body = split_outbound(engine.build_outbound_text(db, db.get(Task, task.id)))[1]
    chunks = split_chunks(outbound_lines(body, boundaries), 1000)
    # 第 2 段中的某个节点混入真实名称（预检之后改动，只有网关能拦住）
    tamper(db_sessionmaker, task, chunks[1].last_loc, f"合同由{REAL_OUR_NAME}签署")
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert r.status == "blocked" and r.error_message.startswith("出口复检发现未脱敏的信息")
    assert requests == []  # V31-U5F-01：发送前先复检全部分段，任何一段命中则一段都不发
    assert [c.status for c in llm_calls(db_sessionmaker)] == ["blocked"]
    assert r.receipt['calls'] == 0 and r.receipt['blocked'] == r.receipt['rechecks'] == 1
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "awaiting_confirm"
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
        assert db.get(ReviewRun, run.id).coverage is None
        assert service.run_detail(db, db.get(ReviewRun, run.id)).coverage is None


def test_monthly_cap_counts_chunked_round_once(db_sessionmaker, owner, ready_task, llm):
    task = chunked_task(db_sessionmaker, owner, ready_task)
    requests = llm("normal")
    with db_sessionmaker() as db:
        configure_model(db, "model.monthly_call_cap", 1, None)
        db.commit()
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert len(requests) == r.chunk_count and r.status == "completed" and r.failed_chunks == []
    from app.llm import usage
    with db_sessionmaker() as db:
        assert usage.monthly_sent_calls(db, 1) == 1
        assert usage.monthly_usage(db, 1)["calls"] == r.chunk_count
        assert usage.cap_reached(db, owner.id, "review", 1) == "CAP_REACHED"


def test_progress_visible_while_running(db_sessionmaker, owner, ready_task, llm, monkeypatch):
    monkeypatch.setattr(job, "CHUNK_PARALLEL", 1)  # 逐段发送时进度按段递增
    task = chunked_task(db_sessionmaker, owner, ready_task)
    seen = []
    holder = {}

    def handler(request: httpx.Request) -> httpx.Response:
        with db_sessionmaker() as db:
            d = service.run_detail(db, db.get(ReviewRun, holder["run_id"]))
            seen.append((chunk_of(request), d.progress.done, d.progress.sent, d.progress.total, d.chunk_count, d.coverage))
        status, payload = build_reply(json.loads(request.content))
        return httpx.Response(status, json=payload)

    llm(handler=handler)
    run = new_run(db_sessionmaker, task, owner)
    holder["run_id"] = run.id
    run_job(db_sessionmaker, run.id)
    n = get_run(db_sessionmaker, run.id).chunk_count
    assert seen == [(i, i - 1, i, n, n, None) for i in range(1, n + 1)]  # 发出时 sent 已计入
    with db_sessionmaker() as db:
        d = service.run_detail(db, db.get(ReviewRun, run.id))
        assert d.progress is None and d.chunk_count == n


def test_q72_finding_on_quote_summary_line(db_sessionmaker, owner, ready_task, llm):
    import io

    import docx

    d = docx.Document()
    d.add_paragraph("报价单")
    d.add_paragraph(f"卖方：{REAL_OUR_NAME}")
    d.add_paragraph(f"买方：{REAL_BUYER_NAME}")
    rows = [("品名", "数量(MT)", "单价(USD/MT)", "金额(USD)"), ("煅后石油焦", "100", "1250", "125000"),
            ("针状焦", "50", "2100", "105000"), ("石墨电极", "20", "3000", "60000")]
    table = d.add_table(rows=len(rows), cols=4)
    for r, values in enumerate(rows):
        for c, v in enumerate(values):
            table.cell(r, c).text = v
    buf = io.BytesIO()
    d.save(buf)
    task = ready_task(owner, [("报价单.docx", buf.getvalue())])

    def mutate(output, messages):
        line = next(x for x in messages[1]["content"].splitlines() if "【报价明细表" in x)
        loc, text = line[1:].split("] ", 1)
        output["findings"] = [dict(output["findings"][0], locs=[loc], quote=text[:15])]

    llm(handler=custom_reply(mutate))
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    with db_sessionmaker() as db:
        (f,) = db.scalars(select(Finding).where(Finding.run_id == run.id)).all()
        tables = engine.build_outbound(db, db.get(Task, task.id)).quote_tables
        assert len(tables) == 1 and f.locs == [tables[0].first_loc]
        assert f.cite_status == "verified" and crypto.decrypt_text(f.quote_snapshot_enc).startswith("【报价明细表")
        stored_run = db.get(ReviewRun, run.id)
        assert stored_run.coverage["quote_tables"] == []
        detail = service.run_detail(db, stored_run)
        assert not any("报价明细表" in note for note in detail.coverage["notes"])


def test_personal_cap_counts_chunked_round_once(db_sessionmaker,owner,ready_task,llm,monkeypatch):
    from app.review.chunking import Chunk
    task = ready_task(owner)
    def two_chunks(lines,limit):
        midpoint = len(lines)//2
        parts = (lines[:midpoint],lines[midpoint:])
        return [Chunk(i,2,[line.text for line in part],part[0].loc,part[-1].loc,
                      frozenset(line.loc for line in part)) for i,part in enumerate(parts,1)]
    monkeypatch.setattr(job,"split_chunks",two_chunks)
    with db_sessionmaker() as db:
        configure_model(db,"model.user_daily_cap",2,None)
        db.add(LlmCall(model_id=1, user_id=owner.id,purpose="review",status="sent",model_name="mock"))
        db.commit()
    requests = llm("normal")
    run = new_run(db_sessionmaker,task,owner)
    run_job(db_sessionmaker,run.id)
    result = get_run(db_sessionmaker,run.id)
    assert result.chunk_count == 2 and result.status == "completed" and result.failed_chunks == []
    assert len(requests) == 2
    with db_sessionmaker() as db:
        calls = list(db.scalars(select(LlmCall)))
        assert len(calls) == 3 and all(c.user_id == owner.id and c.status == "sent" for c in calls)
        from app.llm import usage
        assert usage.cap_reached(db, owner.id, "review", 1) == "USER_CAP_REACHED"


def test_streamed_finding_is_readonly_then_replaced_by_final(db_sessionmaker, owner, ready_task, llm):
    from app.security.errors import AppError
    task = ready_task(owner)
    run = new_run(db_sessionmaker, task, owner)
    seen = []

    class Stream(httpx.SyncByteStream):
        def __init__(self, output):
            self.output = output
        def __iter__(self):
            item = json.dumps(self.output.pop("findings")[0], ensure_ascii=False)
            first = '{"findings":[' + item + ']'
            tail = ',' + json.dumps(self.output, ensure_ascii=False)[1:]
            def event(piece):
                return ('data: ' + json.dumps({"choices": [{"delta": {"content": piece}}]}, ensure_ascii=False) + '\n\n').encode()
            yield event(first)
            with db_sessionmaker() as db:
                current = db.get(ReviewRun, run.id)
                temp = db.scalar(select(Finding).where(Finding.run_id == run.id))
                assert current.status == 'running' and temp.seq < 0
                detail = service.run_detail(db, current)
                assert len(detail.findings) == 1 and detail.findings[0].id != temp.id
                with pytest.raises(AppError) as exc:
                    service.update_finding(db, temp, owner, service.FindingPatch(handling_status='adopted'))
                assert exc.value.status == 409
                seen.append(temp.id)
            yield event(tail)
            yield b'data: [DONE]\n\n'

    def handler(request):
        _, payload = build_reply(json.loads(request.content))
        output = json.loads(payload['choices'][0]['message']['content'])
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Stream(output))
    llm(handler=handler)
    run_job(db_sessionmaker, run.id)
    with db_sessionmaker() as db:
        current = db.get(ReviewRun, run.id)
        result = list(db.scalars(select(Finding).where(Finding.run_id == run.id)))
        assert current.status == 'completed' and len(result) == 1
        assert seen and result[0].seq == 1 and result[0].id != seen[0]


def test_format_retry_removes_previous_streamed_findings(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    run = new_run(db_sessionmaker, task, owner)
    attempts = []
    class Stream(httpx.SyncByteStream):
        def __init__(self, output, attempt):
            self.output, self.attempt = output, attempt
        def __iter__(self):
            with db_sessionmaker() as db:
                assert not list(db.scalars(select(Finding).where(Finding.run_id == run.id)))
            item = json.dumps(self.output['findings'][0], ensure_ascii=False)
            content = '{"findings":[' + item + ']}' if self.attempt == 1 else json.dumps(self.output, ensure_ascii=False)
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': content}}]}, ensure_ascii=False) + '\n\n').encode()
            with db_sessionmaker() as db:
                row = db.scalar(select(Finding).where(Finding.run_id == run.id))
                assert row and row.seq < 0
                attempts.append(row.id)
            yield b'data: [DONE]\n\n'
    calls = []
    def handler(request):
        _, payload = build_reply(json.loads(request.content))
        output = json.loads(payload['choices'][0]['message']['content'])
        calls.append(1)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Stream(output, len(calls)))
    llm(handler=handler)
    run_job(db_sessionmaker, run.id)
    with db_sessionmaker() as db:
        rows = list(db.scalars(select(Finding).where(Finding.run_id == run.id)))
        assert len(attempts) == 2 and attempts[0] != attempts[1]
        assert len(rows) == 1 and rows[0].seq == 1 and rows[0].id not in attempts
        assert db.get(ReviewRun, run.id).status == 'completed'
