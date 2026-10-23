"""轮次接口（M2-12）：确认发送的前置检查与 409 错误码、轮次列表与详情（还原真实名称）、修改问题处理状态，
以及对真实接口重跑 A-06。需要 TEST_DATABASE_URL。"""
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, update

from app.db.models import (
    AuditLog,
    DocNode,
    Finding,
    Job,
    LlmCall,
    Party,
    RedactionSuspect,
    RedactionTerm,
    ReviewRun,
    Task,
    TaskFile,
)
from app.main import API_PREFIX
from app.redaction.engine import load_redaction_map
from app.review import job
from app.review.service import _View
from app.security.crypto import decrypt_text
from app.tasks import pipeline
from tests.mock_llm import build_reply
from tests.review_support import REAL_BUYER_NAME, REAL_OUR_NAME, env, llm, ready_task, sample  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def alice(make_user, login):
    user = make_user("alice")
    client, csrf = login("alice")
    return user, client, {"X-CSRF-Token": csrf}


def post_run(client, headers, task_id):
    return client.post(f"{API_PREFIX}/tasks/{task_id}/runs", headers=headers)


def count(db_sessionmaker, model) -> int:
    with db_sessionmaker() as db:
        return db.scalar(select(func.count()).select_from(model))


def execute(db_sessionmaker, run_id) -> None:
    """代替 worker 执行 review 任务，并把队列中的任务记为完成。"""
    with db_sessionmaker() as db:
        job.run_review(db, uuid.UUID(str(run_id)))
        db.execute(update(Job).where(Job.payload["run_id"].astext == str(run_id)).values(status="done"))
        db.commit()


# ---------------------------------------------------------------- 确认发送


def test_create_run_success(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = ready_task(user, user_note="请重点检查付款条款")
    requests = llm("normal")
    r = post_run(c, h, task.id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["run_no"] == 1 and body["status"] == "queued"
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert (t.status, t.latest_run_id) == ("reviewing", uuid.UUID(body["id"]))
        run = db.get(ReviewRun, uuid.UUID(body["id"]))
        assert (run.stance, run.source, run.user_note, run.main_doc_type) == (
            "sales", "outgoing", "请重点检查付款条款", "contract")
        assert (run.model_name, run.prompt_version, run.builtin_req_version) == ("mock-model", "2026.10-2", "2026.10-1")
        assert run.company_req_version is None and run.created_by == user.id
        queued = db.scalars(select(Job).where(Job.type == "review")).one()
        assert (queued.status, queued.payload) == ("queued", {"task_id": str(task.id), "run_id": body["id"]})
        log = db.scalars(select(AuditLog).where(AuditLog.action == "run_create")).one()
        assert log.target_id == body["id"] and log.detail["run_no"] == 1 and log.user_id == user.id
        # 9.10：确认发送时写入签约方名称（kind=learned），我方标记 is_our_party（Q-73）
        learned = {(decrypt_text(t.term_enc), t.category, t.is_our_party, t.created_by)
                   for t in db.scalars(select(RedactionTerm).where(RedactionTerm.kind == "learned"))}
        assert learned == {(REAL_OUR_NAME, "ORG", True, user.id), (REAL_BUYER_NAME, "ORG", False, user.id)}
        assert "付款" not in str(log.detail)
    assert requests == []  # 确认发送本身不调用模型，由 worker 执行
    detail = c.get(f"{API_PREFIX}/tasks/{task.id}").json()
    assert detail["status"] == "reviewing" and detail["latest_run"]["run_no"] == 1 and detail["queue_position"] == 1


def test_409_task_state_conflict(db_sessionmaker, alice, ready_task):
    user, c, h = alice
    task = ready_task(user)
    assert post_run(c, h, task.id).status_code == 201
    r = post_run(c, h, task.id)  # 已在审核中
    assert (r.status_code, r.json()["code"]) == (409, "TASK_STATE_CONFLICT")
    assert count(db_sessionmaker, ReviewRun) == 1
    with db_sessionmaker() as db:
        for status in ("parsing", "parse_failed", "reviewed"):
            db.get(Task, task.id).status = status
            db.commit()
            r = post_run(c, h, task.id)
            assert (r.status_code, r.json()["code"]) == (409, "TASK_STATE_CONFLICT"), status
    assert count(db_sessionmaker, ReviewRun) == 1


def test_409_local_only_and_a04_zero_model_calls(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = ready_task(user, policy="local_only")
    requests = llm("normal")
    r = post_run(c, h, task.id)
    assert (r.status_code, r.json()["code"]) == (409, "LOCAL_ONLY")
    # 状态被改成 awaiting_confirm 也一样
    with db_sessionmaker() as db:
        db.get(Task, task.id).status = "awaiting_confirm"
        db.commit()
    assert post_run(c, h, task.id).json()["code"] == "LOCAL_ONLY"
    assert requests == [] and count(db_sessionmaker, LlmCall) == 0
    assert count(db_sessionmaker, ReviewRun) == 0 and count(db_sessionmaker, Job) == 0


def test_409_suspects_pending(db_sessionmaker, alice, ready_task):
    user, c, h = alice
    task = ready_task(user)
    with db_sessionmaker() as db:
        db.add(RedactionSuspect(task_id=task.id, loc="F1-P2", char_start=0, char_end=2, reason="org_suffix"))
        db.add(RedactionSuspect(task_id=task.id, loc="F1-P3", char_start=0, char_end=2, reason="capitalized",
                                decision="ignore"))
        db.commit()
    r = post_run(c, h, task.id)
    assert (r.status_code, r.json()["code"]) == (409, "SUSPECTS_PENDING")
    with db_sessionmaker() as db:  # 全部处理后可以提交
        for s in db.scalars(select(RedactionSuspect)):
            s.decision = "ignore"
        db.commit()
    assert post_run(c, h, task.id).status_code == 201


def test_409_our_party_required(db_sessionmaker, alice, ready_task):
    user, c, h = alice
    task = ready_task(user)
    with db_sessionmaker() as db:
        for p in db.scalars(select(Party).where(Party.task_id == task.id)):
            p.is_our_party = False
        db.commit()
    r = post_run(c, h, task.id)
    assert (r.status_code, r.json()["code"]) == (409, "OUR_PARTY_REQUIRED")
    assert count(db_sessionmaker, ReviewRun) == 0
    with db_sessionmaker() as db:
        for p in db.scalars(select(Party).where(Party.task_id == task.id)):
            p.is_our_party = True  # 两方都是我方也不行
        db.commit()
    assert post_run(c, h, task.id).json()["code"] == "OUR_PARTY_REQUIRED"


def test_409_outbound_blocked_names_category_and_position_only(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = ready_task(user)
    requests = llm("normal")
    with db_sessionmaker() as db:
        node = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc == "F1-P2"))
        node.redacted_text = f"合同编号：{REAL_OUR_NAME}，联系电话 13800138000"
        db.commit()
    r = post_run(c, h, task.id)
    assert (r.status_code, r.json()["code"]) == (409, "OUTBOUND_BLOCKED")
    message = r.json()["message"]
    assert "MAP@" in message and "PHONE@" in message
    assert REAL_OUR_NAME not in message and "13800138000" not in message
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "awaiting_confirm"
    assert count(db_sessionmaker, ReviewRun) == 0 and count(db_sessionmaker, Job) == 0
    assert requests == [] and count(db_sessionmaker, LlmCall) == 0  # 预检不产生外发记录
    assert count(db_sessionmaker, RedactionTerm) == 0  # 预检拦截时不写入已记住的名称（Q-73 ①）


def test_precheck_also_covers_user_note(db_sessionmaker, alice, ready_task):
    user, c, h = alice
    task = ready_task(user, user_note=f"请核对{REAL_BUYER_NAME}的付款条款")
    with db_sessionmaker() as db:
        stored = db.get(Task, task.id)
        mapping = load_redaction_map(db, task.id)
        code = next(code for code, value in mapping.items() if value == REAL_BUYER_NAME)
        assert REAL_BUYER_NAME not in stored.user_note_redacted and code in stored.user_note_redacted
        stored.user_note_redacted = f"请核对{REAL_BUYER_NAME}的付款条款"
        db.commit()
    r = post_run(c, h, task.id)
    assert (r.status_code, r.json()["code"]) == (409, "OUTBOUND_BLOCKED")
    assert REAL_BUYER_NAME not in r.json()["message"]
    assert count(db_sessionmaker, ReviewRun) == 0 and count(db_sessionmaker, Job) == 0
    assert count(db_sessionmaker, LlmCall) == 0


def test_run_no_increments_after_block(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = ready_task(user)
    llm("normal")
    first = post_run(c, h, task.id).json()
    with db_sessionmaker() as db:  # 模拟 worker 被网关拦截后的状态
        run = db.get(ReviewRun, uuid.UUID(first["id"]))
        run.status = "blocked"
        db.get(Task, task.id).status = "awaiting_confirm"
        db.commit()
    second = post_run(c, h, task.id)
    assert second.status_code == 201 and second.json()["run_no"] == 2


# ---------------------------------------------------------------- 轮次列表与详情


def test_list_runs_newest_first(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = ready_task(user)
    llm("normal")
    first = post_run(c, h, task.id).json()["id"]
    execute(db_sessionmaker, first)
    with db_sessionmaker() as db:
        db.get(Task, task.id).status = "awaiting_confirm"  # 重新审核
        db.commit()
    second = post_run(c, h, task.id).json()["id"]
    r = c.get(f"{API_PREFIX}/tasks/{task.id}/runs")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [(i["id"], i["run_no"], i["status"]) for i in items] == [(second, 2, "queued"), (first, 1, "completed")]
    assert items[1]["model_name"] == "mock-model" and items[1]["finished_at"] and items[0]["finished_at"] is None
    assert set(items[0]) == {"id", "run_no", "status", "queued_at", "finished_at", "model_name"}


def test_run_detail_restores_names_and_sorts_findings(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = ready_task(user)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        status, payload = build_reply(body)
        out = json.loads(payload["choices"][0]["message"]["content"])
        base = out["findings"][0]
        out["doc_summary"] = "我方向客户_A销售金属制品"
        out["sanctions"] = {"level": "需注意", "reasons": ["客户_A位于需关注地区"]}
        out["requirement_results"][1]["note"] = "客户_A条款齐全"
        out["findings"] = [
            {**base, "title": "低风险问题", "severity": "低", "locs": ["F1-P2"], "quote": "合同编号"},
            {**base, "title": "高风险问题", "severity": "高", "problem": "客户_A未提供保函", "impact": "我方承担风险",
             "suggestion": "要求客户_A提供保函", "suggested_text": "客户_A应在 30 日内提供保函", "basis": "通行商业惯例"},
            {**base, "title": "缺失型问题", "severity": "高", "missing": True, "locs": [], "quote": None},
            {**base, "title": "编造引用", "severity": "中", "quote": "这是一段原文中不存在的编造引用"},
        ]
        payload["choices"][0]["message"]["content"] = json.dumps(out, ensure_ascii=False)
        return httpx.Response(status, json=payload)

    llm(handler=handler)
    rid = post_run(c, h, task.id).json()["id"]
    execute(db_sessionmaker, rid)

    r = c.get(f"{API_PREFIX}/runs/{rid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["task_id"] == str(task.id) and d["status"] == "completed" and d["run_no"] == 1
    assert (d["stance"], d["source"], d["main_doc_type"], d["model_name"]) == ("sales", "outgoing", "contract", "mock-model")
    assert d["coverage"] is not None and d["queue_position"] is None and d["error_message"] is None
    assert set(d["coverage"]) == {"summary", "notes"}
    assert d["coverage"]["summary"].startswith("本次按 ")
    assert isinstance(d["coverage"]["notes"], list)
    assert "requirements" not in d["coverage"] and "星河虚构进出口有限公司" not in str(d["coverage"])
    assert d["prompt_version"] == "2026.10-2" and d["builtin_req_version"] == "2026.10-1"
    assert d["queued_at"].endswith("Z") or "+00:00" in d["queued_at"]
    assert d["doc_summary"] == f"{REAL_OUR_NAME}向{REAL_BUYER_NAME}销售金属制品"
    assert d["sanctions"] == {"level": "attention", "reasons": [f"{REAL_BUYER_NAME}位于需关注地区"]}
    assert d["requirement_results"][1]["note"] == f"{REAL_BUYER_NAME}条款齐全"
    assert set(d["requirement_results"][0]) == {"req_id", "req_source", "req_text", "dimension", "verdict", "note"}
    # 严重程度高 → 低，同级按 seq
    assert [(f["title"], f["severity"], f["seq"]) for f in d["findings"]] == [
        ("高风险问题", "high", 2), ("缺失型问题", "high", 3), ("编造引用", "medium", 4), ("低风险问题", "low", 1)]
    high = d["findings"][0]
    assert high["problem"] == f"{REAL_BUYER_NAME}未提供保函" and high["impact"] == f"{REAL_OUR_NAME}承担风险"
    assert high["suggestion"] == f"要求{REAL_BUYER_NAME}提供保函"
    assert high["suggested_text"] == f"{REAL_BUYER_NAME}应在 30 日内提供保函"
    assert high["basis"] == "通行商业惯例"
    # 引用为原文快照（不是代号形式），位置 = 原文件名 + loc / 条款编号
    assert (high["cite_status"], high["quote"], high["locs"], high["location"]) == (
        "verified", "国际销售合同", ["F1-P1"], "销售合同.docx F1-P1")
    low = d["findings"][3]
    assert low["quote"] == "合同编号：HT-2026-0088" and low["location"] == "销售合同.docx F1-P2"
    missing = d["findings"][1]
    assert (missing["cite_status"], missing["missing"], missing["quote"], missing["location"], missing["locs"]) == (
        "missing", True, None, None, [])
    fake = d["findings"][2]
    assert (fake["cite_status"], fake["quote"]) == ("unverified", None)
    assert all(f["handling_status"] == "pending" and f["handling_note"] is None for f in d["findings"])
    # 返回内容里不应出现代号形式
    assert "客户_A" not in r.text
    # 任务列表里的高风险数
    assert c.get(f"{API_PREFIX}/tasks").json()["items"][0]["high_count"] == 2


def test_run_detail_of_clause_numbered_location(db_sessionmaker, alice, ready_task, llm):
    """有条款编号的节点，位置显示编号（“第8条”这类）而不是 loc。"""
    user, c, h = alice
    task = ready_task(user)
    with db_sessionmaker() as db:
        node = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.clause_no.is_not(None)))
        loc, clause_no = node.loc, node.clause_no
        redacted = node.redacted_text
    assert loc and clause_no

    def handler(request):
        status, payload = build_reply(json.loads(request.content))
        out = json.loads(payload["choices"][0]["message"]["content"])
        out["findings"][0]["locs"] = [loc]
        out["findings"][0]["quote"] = redacted[:15]
        payload["choices"][0]["message"]["content"] = json.dumps(out, ensure_ascii=False)
        return httpx.Response(status, json=payload)

    llm(handler=handler)
    rid = post_run(c, h, task.id).json()["id"]
    execute(db_sessionmaker, rid)
    (f,) = c.get(f"{API_PREFIX}/runs/{rid}").json()["findings"]
    assert f["location"] == f"销售合同.docx {clause_no}"


def test_run_detail_shows_blocked_reason_and_queue_position(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = ready_task(user)
    llm("normal")
    rid = post_run(c, h, task.id).json()["id"]
    d = c.get(f"{API_PREFIX}/runs/{rid}").json()
    assert (d["status"], d["queue_position"], d["findings"], d["requirement_results"]) == ("queued", 1, [], [])
    assert d["sanctions"] == {"level": None, "reasons": []} and d["doc_summary"] is None
    with db_sessionmaker() as db:
        node = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc == "F1-P2"))
        node.redacted_text = f"合同编号：{REAL_OUR_NAME}"
        db.commit()
    execute(db_sessionmaker, rid)
    d = c.get(f"{API_PREFIX}/runs/{rid}").json()
    assert d["status"] == "blocked" and d["error_message"].startswith("出口复检发现未脱敏的信息")
    assert REAL_OUR_NAME not in json.dumps(d, ensure_ascii=False)
    assert c.get(f"{API_PREFIX}/tasks/{task.id}").json()["status"] == "awaiting_confirm"


# ---------------------------------------------------------------- 修改问题处理状态


def one_finding(db_sessionmaker, c, h, user, ready_task, llm):
    task = ready_task(user)
    llm("normal")
    rid = post_run(c, h, task.id).json()["id"]
    execute(db_sessionmaker, rid)
    (f,) = c.get(f"{API_PREFIX}/runs/{rid}").json()["findings"]
    return task, rid, f


def test_patch_finding(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task, rid, f = one_finding(db_sessionmaker, c, h, user, ready_task, llm)
    url = f"{API_PREFIX}/findings/{f['id']}"
    r = c.patch(url, json={"handling_status": "adopted", "handling_note": "  已和客户沟通  "}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["id"], body["handling_status"], body["handling_note"]) == (f["id"], "adopted", "已和客户沟通")
    assert body["quote"] == "国际销售合同" and body["location"] == "销售合同.docx F1-P1" and body["title"] == "模拟问题"
    with db_sessionmaker() as db:
        row = db.get(Finding, uuid.UUID(f["id"]))
        assert row.updated_by == user.id and row.updated_at is not None
    # 只改状态，备注保留；只改备注，状态保留
    assert c.patch(url, json={"handling_status": "ignored"}, headers=h).json()["handling_note"] == "已和客户沟通"
    r = c.patch(url, json={"handling_note": "改为备注二"}, headers=h).json()
    assert (r["handling_status"], r["handling_note"]) == ("ignored", "改为备注二")
    # 备注置空 / null 表示清除
    assert c.patch(url, json={"handling_note": ""}, headers=h).json()["handling_note"] is None
    c.patch(url, json={"handling_note": "又写了"}, headers=h)
    assert c.patch(url, json={"handling_note": None}, headers=h).json()["handling_note"] is None
    # 详情里能看到
    d = c.get(f"{API_PREFIX}/runs/{rid}").json()
    assert d["findings"][0]["handling_status"] == "ignored"
    assert c.patch(url, json={"handling_status": "pending"}, headers=h).json()["handling_status"] == "pending"


def test_patch_finding_validation(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task, rid, f = one_finding(db_sessionmaker, c, h, user, ready_task, llm)
    url = f"{API_PREFIX}/findings/{f['id']}"
    for body in ({}, {"handling_status": "done"}, {"handling_status": None}, {"handling_note": "x" * 501},
                 {"handling_status": 1}):
        r = c.patch(url, json=body, headers=h)
        assert (r.status_code, r.json()["code"]) == (422, "VALIDATION_ERROR"), body
    assert c.patch(url, json={"handling_note": "x" * 500}, headers=h).status_code == 200
    # 修改类请求必须带 CSRF 令牌
    assert c.patch(url, json={"handling_status": "adopted"}).status_code == 403


# ---------------------------------------------------------------- A-06：他人和管理员一律 404


def test_a06_other_users_and_admin_get_404(db_sessionmaker, alice, ready_task, llm, make_user, login):
    user, c, h = alice
    task, rid, f = one_finding(db_sessionmaker, c, h, user, ready_task, llm)
    # 再造一个待确认的任务，用来试 POST
    task2 = ready_task(user)
    make_user("bob")
    make_user("root", role="admin")
    for name in ("bob", "root"):
        oc, ocsrf = login(name)
        oh = {"X-CSRF-Token": ocsrf}
        checks = [
            (oc.get(f"{API_PREFIX}/tasks/{task.id}/runs"), "TASK_NOT_FOUND"),
            (post_run(oc, oh, task2.id), "TASK_NOT_FOUND"),
            (oc.get(f"{API_PREFIX}/runs/{rid}"), "RUN_NOT_FOUND"),
            (oc.patch(f"{API_PREFIX}/findings/{f['id']}", json={"handling_status": "ignored"}, headers=oh),
             "FINDING_NOT_FOUND"),
        ]
        for resp, code in checks:
            assert (resp.status_code, resp.json()["code"]) == (404, code), (name, resp.request.url)
            assert "模拟" not in resp.text
        # 不存在的 id、格式不对的 id 同样是 404
        missing = uuid.uuid4()
        for url, method in ((f"/tasks/{missing}/runs", "get"), (f"/runs/{missing}", "get"), ("/runs/abc", "get"),
                            (f"/findings/{missing}", "patch"), ("/findings/xyz", "patch")):
            resp = getattr(oc, method)(f"{API_PREFIX}{url}", **({"json": {"handling_status": "adopted"}, "headers": oh}
                                                              if method == "patch" else {}))
            assert resp.status_code == 404, url
        # 他人的 POST 没有产生任何副作用
        with db_sessionmaker() as db:
            assert db.get(Task, task2.id).status == "awaiting_confirm"
    with db_sessionmaker() as db:
        assert db.get(Finding, uuid.UUID(f["id"])).handling_status == "pending"
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1


def test_requires_login(api, ready_task, make_user):
    user = make_user("alice")
    task = ready_task(user)
    assert api.get(f"{API_PREFIX}/tasks/{task.id}/runs").status_code == 401
    assert api.get(f"{API_PREFIX}/runs/{uuid.uuid4()}").status_code == 401


# ---------------------------------------------------------------- M3：其他格式


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


def fixture_bytes(path: str) -> bytes:
    return (FIXTURE_ROOT / path).read_bytes()


def test_upload_five_formats_to_awaiting_confirm(db_sessionmaker, alice):
    """经真实接口上传五种格式，parse 后到 awaiting_confirm；不外发时到 completed_local。"""
    user, c, h = alice
    files = [("销售合同.docx", sample("contract_basic")), ("报价单.xlsx", fixture_bytes("xlsx/quotation.xlsx")),
             ("合同.pdf", fixture_bytes("pdf/text_contract.pdf")),
             ("报价函.png", fixture_bytes("images/quotation_clear.png")),
             ("扫描件.jpeg", fixture_bytes("images/quotation_lowq.jpg"))]
    for policy, expected in (("ai", "awaiting_confirm"), ("local_only", "completed_local")):
        parts = [("files", (name, data, "application/octet-stream")) for name, data in files]
        r = c.post(f"{API_PREFIX}/tasks", data={"stance": "sales", "source": "outgoing", "policy": policy},
                   files=parts, headers=h)
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        with db_sessionmaker() as db:
            pipeline.parse_task(db, uuid.UUID(tid))
        d = c.get(f"{API_PREFIX}/tasks/{tid}").json()
        assert d["status"] == expected, d.get("error_message")
        with db_sessionmaker() as db:
            exts = db.scalars(select(TaskFile.ext).where(TaskFile.task_id == uuid.UUID(tid)).order_by(TaskFile.seq))
            assert list(exts) == ["docx", "xlsx", "pdf", "png", "jpg"]


def test_location_shows_page_and_cell(db_sessionmaker, alice, ready_task):
    """问题位置：PDF、图片为“第 N 页”，XLSX 为“工作表!单元格”，有 clause_no 时仍显示编号，DOCX 无编号时为 loc。"""
    user, c, h = alice
    task = ready_task(user, [("销售合同.docx", sample("contract_basic")), ("报价单.xlsx", fixture_bytes("xlsx/quotation.xlsx")),
                             ("合同.pdf", fixture_bytes("pdf/text_contract.pdf"))])
    with db_sessionmaker() as db:
        pdf_plain = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc.like("F3-PG2-%"),
                                                    DocNode.clause_no.is_(None)))
        pdf_clause = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc.like("F3-%"),
                                                     DocNode.clause_no.is_not(None)))
        db.add(DocNode(task_id=task.id, file_id=pdf_plain.file_id, loc="F9-PG1-B1", ord=999, kind="ocr_block",
                       part="body", page=1))  # 文件号不存在：只显示位置
        db.commit()
        view = _View(db, task.id)
        findings = [SimpleNamespace(locs=[loc]) for loc in
                    ("F2-S1-E8", "F2-S2-B3", pdf_plain.loc, pdf_clause.loc, "F1-P1", "F3-PG1-B99", "F9-PG1-B1")]
        findings.append(SimpleNamespace(locs=[]))
        shown = view.locations(findings)
    assert shown == {
        "F2-S1-E8": "报价单.xlsx 报价!E8",
        "F2-S2-B3": "报价单.xlsx 备注!B3",
        pdf_plain.loc: "合同.pdf 第 2 页",
        pdf_clause.loc: f"合同.pdf {pdf_clause.clause_no}",
        "F1-P1": "销售合同.docx F1-P1",
        "F3-PG1-B99": "合同.pdf F3-PG1-B99",  # 节点不存在
        "F9-PG1-B1": "第 1 页",
    }


def test_t07_precheck_blocks_real_name_in_xlsx(db_sessionmaker, alice, ready_task, llm):
    """T-07（M3 重跑）：XLSX 单元格的外发文本中混入真实名称，确认发送时预检拦截，不调用模型。"""
    user, c, h = alice
    task = ready_task(user, [("报价单.xlsx", fixture_bytes("xlsx/quotation.xlsx"))])
    requests = llm("normal")
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "awaiting_confirm"
        node = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc == "F1-S1-B8"))
        node.redacted_text = f"煅后石油焦（{REAL_BUYER_NAME}）"
        db.commit()
    r = post_run(c, h, task.id)
    assert (r.status_code, r.json()["code"]) == (409, "OUTBOUND_BLOCKED")
    assert "MAP@" in r.json()["message"] and REAL_BUYER_NAME not in r.json()["message"]
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "awaiting_confirm"
    assert requests == [] and count(db_sessionmaker, LlmCall) == 0 and count(db_sessionmaker, ReviewRun) == 0


# ---------------------------------------------------------------- T-12 分段审核（M5-01）


def test_t12_chunked_review_through_api(db_sessionmaker, alice, llm):
    """回归集的合同经真实接口上传，分段长度设为 1000：分段审核成功，结论数 = 发送的要求数（A-07）。"""
    from app.admin import settings_store
    from tests.acceptance_support import (
        GOLDEN,
        Sample,
        _docx_bytes,
        confirm_and_review,
        golden_samples,
        settle_suspects,
        upload_and_parse,
    )

    _, c, h = alice
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.chunk_chars", 1000, None)
        db.commit()
    requests = llm("normal")
    golden = next(s for s in golden_samples() if s.name == "contract.docx")
    lines = [x for x in (GOLDEN / "contract.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    lines += [f"第{i}条 补充约定{i}：双方应按附件{i}的约定履行各自义务，附件与本合同具有同等效力。" for i in range(6, 30)]
    contract = Sample("长合同.docx", _docx_bytes(lines), "sales", golden.must_replace, golden.must_suspect)
    tid = upload_and_parse(c, h, db_sessionmaker, contract)
    settle_suspects(c, h, tid, contract.must_suspect)
    run = confirm_and_review(c, h, db_sessionmaker, tid)
    assert run["chunk_count"] >= 2 and len(requests) == run["chunk_count"]
    assert run["status"] in ("completed", "partial") and run["progress"] is None
    assert all(f"分段：第 {i}/{run['chunk_count']} 段" in json.loads(q.content)["messages"][1]["content"]
               for i, q in enumerate(requests, start=1))
    sent = json.loads(requests[0].content)["messages"][1]["content"].split("【审核要求】\n")[1].split("\n【")[0]
    assert len(run["requirement_results"]) == len(sent.splitlines())
    assert len({f["locs"][0] for f in run["findings"] if f["locs"]}) == run["chunk_count"]


def test_t13_monthly_cap_reached_through_api(db_sessionmaker, alice, ready_task, llm):
    """T-13：上限为 1 且本月已有 1 次 sent。确认发送不提前拦截；轮次 failed、提示文字，没有新的 sent 记录。"""
    from app.admin import settings_store

    user, c, h = alice
    task = ready_task(user)
    requests = llm("normal")
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.monthly_call_cap", 1, None)
        db.add(LlmCall(purpose="review", model_name="mock-model", status="sent"))
        db.commit()
    r = post_run(c, h, task.id)
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    execute(db_sessionmaker, run_id)
    detail = c.get(f"{API_PREFIX}/runs/{run_id}").json()
    assert (detail["status"], detail["error_message"]) == ("failed", "已达本月调用上限，请联系管理员")
    assert detail["findings"] == [] and requests == []
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(LlmCall).where(LlmCall.status == "sent")) == 1
        assert db.get(Task, task.id).status == "reviewed"


def test_coverage_unable_note_restores_codes(db_sessionmaker, alice, ready_task, llm):
    """覆盖说明中“无法判断”的原因与审核要求列表一样还原为真实名称（M5-03 评审）。"""
    user, c, h = alice
    task = ready_task(user)

    def handler(request: httpx.Request) -> httpx.Response:
        status, payload = build_reply(json.loads(request.content))
        out = json.loads(payload["choices"][0]["message"]["content"])
        out["requirement_results"][1].update(verdict="unable", note="需核实客户_A的资质")
        payload["choices"][0]["message"]["content"] = json.dumps(out, ensure_ascii=False)
        return httpx.Response(status, json=payload)

    llm(handler=handler)
    rid = post_run(c, h, task.id).json()["id"]
    execute(db_sessionmaker, rid)
    d = c.get(f"{API_PREFIX}/runs/{rid}").json()
    req_id = d["requirement_results"][1]["req_id"]
    assert d["requirement_results"][1]["note"] == f"需核实{REAL_BUYER_NAME}的资质"
    assert f"无法判断的审核要求：{req_id}（需核实{REAL_BUYER_NAME}的资质）" in d["coverage"]["notes"]
    with db_sessionmaker() as db:  # 数据库中仍只存代号
        assert db.get(ReviewRun, uuid.UUID(rid)).coverage["unable"] == [{"req_id": req_id, "note": "需核实客户_A的资质"}]


# ---------------------------------------------------------------- GET /requirements（M5-06）


def _add_company_reqs(db_sessionmaker):
    from app.db.models import CompanyRequirement, RequirementVersion

    with db_sessionmaker() as db:
        db.add_all([
            CompanyRequirement(code="CO-2", text="第二条", doc_types=["contract"], stances=["all"], sort=2),
            CompanyRequirement(code="CO-1", text="第一条", doc_types=["all"], stances=["sales"], sort=1),
            CompanyRequirement(code="CO-3", text="停用条", doc_types=["all"], stances=["all"], enabled=False),
            CompanyRequirement(code="CO-4", text="仅报价单", doc_types=["quotation"], stances=["all"], sort=4),
        ])
        db.add(RequirementVersion(snapshot={}))
        db.commit()


def test_requirements_any_type_returns_builtin_and_filtered_company(alice, db_sessionmaker):
    _, c, _ = alice
    _add_company_reqs(db_sessionmaker)
    r = c.get(f"{API_PREFIX}/requirements", params={"stance": "sales", "source": "outgoing", "doc_type": ""})
    assert r.status_code == 200
    d = r.json()
    assert d["builtin_version"] == "2026.10-1"
    assert d["builtin"] and all(b["doc_types"] for b in d["builtin"])
    assert d["company_version"] == 1
    # 类型为空：不按类型筛选，停用的不返回，按 sort 排序
    assert [x["code"] for x in d["company"]] == ["CO-1", "CO-2", "CO-4"]


def test_requirements_with_doc_type_filters_company(alice, db_sessionmaker):
    _, c, _ = alice
    _add_company_reqs(db_sessionmaker)
    d = c.get(f"{API_PREFIX}/requirements",
              params={"stance": "purchase", "source": "incoming", "doc_type": "contract"}).json()
    assert [x["code"] for x in d["company"]] == ["CO-2"]  # CO-1 立场不匹配，CO-4 类型不匹配
    none = c.get(f"{API_PREFIX}/requirements", params={"stance": "sales", "source": "outgoing"}).json()
    narrow = c.get(f"{API_PREFIX}/requirements",
                   params={"stance": "sales", "source": "outgoing", "doc_type": "letter"}).json()
    assert len(narrow["builtin"]) < len(none["builtin"])


def test_requirements_no_company_requirements(alice):
    _, c, _ = alice
    d = c.get(f"{API_PREFIX}/requirements", params={"stance": "other", "source": "incoming"}).json()
    assert d["company"] == [] and d["company_version"] is None


@pytest.mark.parametrize("params", [
    # Q-113（M7-04）：stance、source 改为可选，单独给出一个不再是错误
    {"stance": "bad", "source": "outgoing"},
    {"stance": "sales", "source": "bad"},
    {"stance": "sales", "source": "outgoing", "doc_type": "bad"},
])
def test_requirements_validation(alice, params):
    _, c, _ = alice
    r = c.get(f"{API_PREFIX}/requirements", params=params)
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_requirements_requires_login(new_client):
    r = new_client().get(f"{API_PREFIX}/requirements", params={"stance": "sales", "source": "outgoing"})
    assert r.status_code == 401
