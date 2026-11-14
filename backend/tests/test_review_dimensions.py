"""V321-D-03：四组全文并发、按轮额度、失败组与发送前整轮复检。"""
import json
import threading

import httpx
from sqlalchemy import select

from app.admin import settings_store
from app.db.models import CompanyRequirement, LlmCall, LlmModel, RequirementResult, ReviewRun, Task
from app.llm import usage
from app.review import job, service
from tests.mock_llm import build_reply
from tests.review_support import env, llm, ready_task, REAL_OUR_NAME  # noqa: F401


def execute(db_sessionmaker, task, owner):
    with db_sessionmaker() as db:
        run = service.create_run(db, db.get(Task, task.id), owner, None)
        run_id = run.id
        job.run_review(db, run_id)
    return run_id


def test_full_text_groups_parallel_retry_and_one_quota(db_sessionmaker, make_user, ready_task, llm):
    owner = make_user("dimension-owner")
    task = ready_task(owner, user_note="重点看付款")
    with db_sessionmaker() as db:
        db.add(CompanyRequirement(code="CO-1", text="检查交付", doc_types=["all"], stances=["all"]))
        db.get(LlmModel, 1).monthly_call_cap = 1
        settings_store.set_setting(db, "model.user_daily_cap", 1, None)
        db.commit()
    holder = {}
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    first = set()

    def handler(request):
        with db_sessionmaker() as db:
            current = db.get(ReviewRun, holder["run_id"])
            assert current.chunk_count == 1
            assert current.coverage["progress"]["total"] == 1
            assert current.coverage["progress"]["sent"] == 1
        body = json.loads(request.content)
        status, reply = build_reply(body)
        output = json.loads(reply["choices"][0]["message"]["content"])
        ids = tuple(r["req_id"] for r in output["requirement_results"])
        with lock:
            initial = ids not in first
            first.add(ids)
        if initial:
            barrier.wait(timeout=15)  # 必须四组同时在途，串行实现会失败。
        if initial and "G-05" in ids:
            reply["choices"][0]["message"]["content"] = "invalid json"
        return httpx.Response(status, json=reply)

    requests = llm(handler=handler)
    with db_sessionmaker() as db:
        run = service.create_run(db, db.get(Task, task.id), owner, None)
        run_id = holder["run_id"] = run.id
        job.run_review(db, run_id)
    assert len(requests) == 5 and len(first) == 4  # 一组格式重试仍占本轮的一份额度。
    texts = [json.loads(r.content)["messages"][1]["content"] for r in requests]
    assert len({t.split("【审核要求】", 1)[0] for t in texts}) == 1
    assert all("分段：" not in t and "</document>\n【审核要求】" in t for t in texts)
    ids = [req for group in first for req in group]
    with db_sessionmaker() as db:
        run = db.get(ReviewRun, run_id)
        assert run.status == "completed" and run.chunk_count == 1 and run.failed_chunks == []
        assert run.coverage["chunked"] is False and run.coverage["no_cross_chunk_check"] is False
        results = list(db.scalars(select(RequirementResult).where(RequirementResult.run_id == run_id)))
        assert len(ids) == len(set(ids)) == len(results)
        assert set(ids) == {r.req_id for r in results} and {"CO-1", "NOTE"} <= set(ids)
        calls = list(db.scalars(select(LlmCall).where(LlmCall.run_id == run_id)))
        assert run.receipt["calls"] == run.receipt["rechecks"] == len(calls) == 5
        assert run.tokens_in == sum(c.tokens_in for c in calls)
        assert run.tokens_out == sum(c.tokens_out for c in calls)
        assert usage.monthly_sent_calls(db, 1) == 1 and usage.monthly_usage(db, 1)["calls"] == 5
        assert usage.cap_reached(db, owner.id, "review", 1) == "CAP_REACHED"
        db.get(LlmModel, 1).monthly_call_cap = 0
        db.commit()
        assert usage.cap_reached(db, owner.id, "review", 1) == "USER_CAP_REACHED"


def test_failed_dimension_keeps_other_groups_and_marks_unable(db_sessionmaker, make_user, ready_task, llm):
    owner = make_user("dimension-failure")
    task = ready_task(owner)

    def handler(request):
        body = json.loads(request.content)
        legal = "[G-05]" in body["messages"][1]["content"]
        status, reply = build_reply(body, "non_json" if legal else "normal")
        return httpx.Response(status, json=reply)

    requests = llm(handler=handler)
    run_id = execute(db_sessionmaker, task, owner)
    assert len(requests) == 5
    with db_sessionmaker() as db:
        run = db.get(ReviewRun, run_id)
        assert run.status == "partial" and run.chunk_count == 1 and not run.coverage["chunked"]
        results = list(db.scalars(select(RequirementResult).where(RequirementResult.run_id == run_id)))
        assert all(r.verdict == "unable" for r in results if r.dimension == "法律")
        assert all(r.verdict != "unable" for r in results if r.dimension != "法律")
        calls = list(db.scalars(select(LlmCall).where(LlmCall.run_id == run_id)))
        assert run.tokens_out == sum(c.tokens_out for c in calls)


def test_last_group_precheck_blocks_all_sends(db_sessionmaker, make_user, ready_task, llm):
    owner = make_user("dimension-blocked")
    task = ready_task(owner)
    requests = llm("normal")
    with db_sessionmaker() as db:
        run = service.create_run(db, db.get(Task, task.id), owner, None)
        run_id = run.id
        # 创建轮次预检后，商务组的公司要求中混入真实值。
        db.add(CompanyRequirement(code="CO-1", text=REAL_OUR_NAME, doc_types=["all"], stances=["all"]))
        db.commit()
        job.run_review(db, run_id)
    assert requests == []
    with db_sessionmaker() as db:
        assert db.get(ReviewRun, run_id).status == "blocked"
        assert [c.status for c in db.scalars(select(LlmCall))] == ["blocked"]
