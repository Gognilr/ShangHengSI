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
from tests.review_support import REAL_BUYER_NAME, REAL_OUR_NAME, env, llm, ready_task  # noqa: F401


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
        db.get(Task, task.id).user_note = "请重点检查付款条款"
        db.commit()
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    with db_sessionmaker() as db:
        r = db.get(ReviewRun, run.id)
        assert r.status == "partial" and db.get(Task, task.id).high_count == 2
        last = db.scalars(select(RequirementResult).where(RequirementResult.run_id == r.id)
                          .order_by(RequirementResult.id.desc())).first()
        assert (last.req_id, last.req_source, last.verdict, last.note) == ("NOTE", "note", "unable", "模型未返回结论")
        assert db.get(Task, task.id).status == "reviewed"


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
        assert r.error_message.startswith("出口复检发现未脱敏的信息，已拦截，未发送（MAP@")
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
    assert (r.status, r.error_message) == ("failed", job.MSG_BAD_OUTPUT)
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
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.monthly_call_cap", 1, None)
        db.add(LlmCall(purpose="review", model_name="mock-model", status="sent"))
        db.commit()
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert (r.status, r.error_message) == ("failed", "已达本月调用上限，请联系管理员")
    assert requests == []
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "reviewed"


def test_model_not_configured_fails(db_sessionmaker, owner, ready_task, llm):
    task = ready_task(owner)
    requests = llm("normal")
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.name", "", None)
        db.commit()
    run = new_run(db_sessionmaker, task, owner)
    assert run.model_name is None
    run_job(db_sessionmaker, run.id)
    r = get_run(db_sessionmaker, run.id)
    assert (r.status, r.error_message) == ("failed", "模型尚未配置，请联系管理员") and requests == []


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
        claimed = jobs.claim(db, "test-worker")
        assert claimed.id == queued.id
        worker.HANDLERS[claimed.type](db, claimed.payload)
    assert get_run(db_sessionmaker, run.id).status == "completed"


# ---------------------------------------------------------------- 还原

def test_restore_is_not_stored(db_sessionmaker, owner, ready_task, llm):
    """数据库里始终是代号形式；真实名称只在返回前端时还原。"""
    task = ready_task(owner)

    def mutate(output, messages):
        output["doc_summary"] = "我方与客户_A签订销售合同"
        output["findings"][0]["problem"] = "客户_A的付款方式对我方不利"
        output["sanctions"] = {"level": "需注意", "reasons": ["客户_A所在地区需关注"]}

    llm(handler=custom_reply(mutate))
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    with db_sessionmaker() as db:
        r = db.get(ReviewRun, run.id)
        assert r.doc_summary == "我方与客户_A签订销售合同" and r.sanctions_level == "attention"
        assert db.scalar(select(Finding.problem).where(Finding.run_id == r.id)) == "客户_A的付款方式对我方不利"
        mapping = engine.load_redaction_map(db, task.id)
        assert mapping["客户_A"] == REAL_BUYER_NAME and db.scalar(select(func.count()).select_from(Party)) == 2
        detail = service.run_detail(db, r)
    assert detail.doc_summary == f"{REAL_OUR_NAME}与{REAL_BUYER_NAME}签订销售合同"
    assert detail.sanctions.level == "attention" and detail.sanctions.reasons == [f"{REAL_BUYER_NAME}所在地区需关注"]
    assert detail.findings[0].problem == f"{REAL_BUYER_NAME}的付款方式对{REAL_OUR_NAME}不利"
