"""V35-D-03 增量复审（虚构三稿合同，模型用模拟服务）：一稿全文审 → 二稿只审变化 → 三稿接着二稿那一轮往下走。

覆盖：设置页预览（能否只审变化、字数少于全文）、只发变化条款与上轮问题、上轮问题三类处理、
结果分类与合并、逐条结论沿用、覆盖说明、报告与批注版、调用次数（增量轮 1 次，全文轮按维度 4 次）。
"""
import io
import json
import re
import uuid
from time import perf_counter

import httpx
import pytest
from docx import Document
from sqlalchemy import select, update

from app.db.models import Finding, Job, LlmCall, ReviewRun
from app.llm import gateway
from app.main import API_PREFIX
from app.review import job as review_job
from tests.review_support import env  # noqa: F401
from tests.test_drafts import NAMES, _upload
from tests.test_assistant_sessions import BASE, create, mock_ner, session_client  # noqa: F401
from tests.test_tasks_api import files_dir  # noqa: F401

_LINE = re.compile(r"^\[(F\d+-[^\]]+)\] ?(.*)$", re.M)
_REQ = re.compile(r"^\[([A-Z]{1,2}-\d+|CO-\d+|NOTE)\]", re.M)


def _loc(text, start):
    return next(loc for loc, line in _LINE.findall(text) if re.sub(r"^\d+[.、]?\s*", "", line).startswith(start))


def _reply(content, prompt_tokens):
    return httpx.Response(200, json={"id": "x", "object": "chat.completion", "model": "mock",
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                                  "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 10}})


def _finding(loc, title, quote, missing=False, req=None):
    return {"title": title, "category": "商务", "severity": "中", "req_ids": req or [], "locs": [] if missing else [loc],
            "quote": None if missing else quote, "missing": missing, "problem": title + "：模拟说明",
            "suggestion": "模拟建议", "suggested_text": None, "basis": "通行商业惯例"}


@pytest.fixture
def model(monkeypatch, env):
    seen, full = [], []

    def handle(request):
        body = json.loads(request.content)
        tokens = max(1, sum(len(m["content"]) for m in body["messages"]) // 2)
        text = body["messages"][-1]["content"]
        seen.append(text)
        reqs = _REQ.findall(text.split("【审核要求】", 1)[1]) if "【审核要求】" in text else []
        if "增量复审" in text:
            priors = re.findall(r"^(P\d+) ([^｜]+)", text, re.M)
            judged = [{"id": pid, "status": "resolved" if "付款" in title else "persists", "note": "模拟依据"}
                      for pid, title in priors]
            new = [_finding(_loc(text, "质量保证"), "质量保证期起算不明", "质量保证", req=reqs[:1])]                 if "此段为本稿新增" in text else []
            return _reply(json.dumps({"doc_summary": "第 2 稿改了付款、违约金等", "prior": judged, "findings": new,
                                      "sanctions": {"level": "无明显风险", "reasons": []}}, ensure_ascii=False), tokens)
        findings = []
        full.append(text)
        if len(full) == 1:  # 全文轮按维度并发：只在第一组里提问题，免得四组重复
            findings = [_finding(_loc(text, "付款方式"), "付款期限过长", "付款方式", req=reqs[:1]),
                            _finding(_loc(text, "交货期限"), "交货期限未约定起算", "交货期限", req=reqs[:1]),
                            _finding(None, "缺少验收条款", None, missing=True, req=reqs[:1])]
        results = [{"req_id": r, "verdict": "issue" if findings and r == reqs[0] else "ok", "note": "模拟"} for r in reqs]
        return _reply(json.dumps({"doc_summary": "设备采购合同", "requirement_results": results, "findings": findings,
                                  "sanctions": {"level": "无明显风险", "reasons": []}}, ensure_ascii=False), tokens)

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    return seen


def parse(sm, sid):
    from app.tasks import pipeline
    with sm() as db:
        job = db.scalar(select(Job).where(Job.type == "parse", Job.payload["task_id"].astext == sid).order_by(Job.id.desc()))
        pipeline.handle_parse(db, job.payload)
        db.execute(update(Job).where(Job.id == job.id).values(status="done"))  # worker 完成后的状态
        db.commit()


def _confirm(c, h, sid, our=True):
    if our:
        parties = c.get(f"{API_PREFIX}/tasks/{sid}/parties").json()["items"]
        items = [{"id": p["id"], "is_our_party": i == 0, "country": p["country"] or "中国", "org_type": p["org_type"] or "企业"}
                 for i, p in enumerate(parties)]
        assert c.put(f"{API_PREFIX}/tasks/{sid}/parties", headers=h, json={"items": items}).status_code == 200
    r = c.post(f"{BASE}/{sid}/confirm", headers=h)
    assert r.status_code == 200, r.text


def _run(c, h, sm, sid, **body):
    r = c.post(f"{BASE}/{sid}/runs", headers=h, json={"stance": "purchase", "source": "incoming", **body})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    with sm() as db:
        review_job.run_review(db, uuid.UUID(rid))
        calls = db.query(LlmCall).filter(LlmCall.run_id == uuid.UUID(rid)).count()
    return rid, calls


def test_delta_review_three_drafts(files_dir, session_client, db_sessionmaker, model, make_user, login):
    c, h, _ = session_client
    sm = db_sessionmaker
    sid = create(c, h)["id"]
    _upload(c, h, sid, NAMES[0])
    parse(sm, sid)
    _confirm(c, h, sid)
    first, calls_full = _run(c, h, sm, sid)
    d1 = c.get(f"{API_PREFIX}/runs/{first}").json()
    assert d1["scope"] == "full" and len(d1["findings"]) == 3

    # 二稿：结果页“传新一稿”
    one = c.get(f"{BASE}/{sid}").json()["files"][0]
    _upload(c, h, sid, NAMES[1], draft_of=one["id"])
    parse(sm, sid)
    two = c.get(f"{BASE}/{sid}").json()["files"][1]
    _confirm(c, h, sid, our=False)
    q = {"mode": "review", "stance": "purchase", "file_ids": [two["id"]], "main_file_id": two["id"]}
    full = c.get(f"{BASE}/{sid}/outbound", params=q).json()
    preview = c.get(f"{BASE}/{sid}/outbound", params={**q, "scope": "delta"}).json()
    info = preview["delta"]
    assert info["available"] and info["recommended"] and info["base_run_no"] == 1 and info["prev_no"] == 1
    assert preview["chars"] > 0 and full["chars"] > 0  # 字数对照见报告（8 条的小样本里增量包带上一稿文字，反而更长）
    # 交货期限一条没动：问题本机记“仍存在”，不发；付款期限改了、缺失型（本稿有新增）发给模型
    assert info["priors_sent"] == 2 and info["priors_total"] == 3
    assert "交货期限" not in preview["text"] and "（上一稿此段：" in preview["text"] and "【上轮问题】" in preview["text"]

    # 上轮模型若曾把真实名称写进问题说明，沿用的问题文字也必须经出口复检拦截。
    def prior_problem(db):
        return db.scalar(select(Finding).where(Finding.run_id == uuid.UUID(first), Finding.title == "付款期限过长"))

    with sm() as db:
        finding = prior_problem(db)
        clean_problem = finding.problem
        finding.problem = "华岳精工设备有限公司的付款期限过长"
        db.commit()
    before_block = len(model)
    blocked = c.post(f"{BASE}/{sid}/runs", headers=h, json={"stance": "purchase", "source": "incoming", "scope": "delta",
                                                             "file_ids": [two["id"]], "main_file_id": two["id"]})
    assert blocked.status_code == 409 and blocked.json()["code"] == "OUTBOUND_BLOCKED"
    assert len(model) == before_block
    with sm() as db:
        prior_problem(db).problem = clean_problem
        db.commit()

    started = perf_counter()
    second, calls_delta = _run(c, h, sm, sid, file_ids=[two["id"]], main_file_id=two["id"], scope="delta")
    delta_seconds = perf_counter() - started
    assert calls_delta == 1 and calls_full == 4  # 增量轮一次请求；全文轮按四个维度并发
    sent = model[-1]
    assert "增量复审" in sent and "交货期限" not in sent.split("【上轮问题】")[0]
    assert preview["text"].split("\n", 1)[1] in sent
    assert "【本地校验摘要】" not in sent and "【本地财务比率】" not in sent
    d2 = c.get(f"{API_PREFIX}/runs/{second}").json()
    carry = {f["title"]: f["carry"] for f in d2["findings"]}
    assert carry == {"付款期限过长": "resolved", "交货期限未约定起算": "unchanged", "缺少验收条款": "persists",
                     "质量保证期起算不明": "new"}
    assert d2["findings"][-1]["carry"] == "resolved" and d2["findings"][-1]["carry_note"] == "模拟依据"
    assert d2["scope"] == "delta" and d2["base_run_no"] == 1 and d2["draft_no"] == 2
    assert d2["coverage"]["notes"][0].startswith("增量复审：对照第 1 稿，审核了变化的")
    assert d2["coverage"]["summary"].startswith("增量复审沿用第 1 轮的逐条结论")
    assert {r["req_id"] for r in d2["requirement_results"]} == {r["req_id"] for r in d1["requirement_results"]}
    runs = c.get(f"{BASE}/{sid}/runs").json()["items"]
    assert (runs[0]["scope"], runs[0]["base_run_no"], runs[0]["draft_no"]) == ("delta", 1, 2)

    # 报告：本轮状态一列、与上一稿的变化；批注版不挂已解决的问题
    report = c.get(f"{API_PREFIX}/runs/{second}/report")
    assert report.status_code == 200, report.text
    doc = Document(io.BytesIO(report.content))
    texts = "\n".join(p.text for p in doc.paragraphs)
    assert "与上一稿的变化" in texts and "已解决 1 条" in texts
    assert "第2稿.docx" in doc.tables[0].rows[1].cells[1].text
    assert "第1稿.docx" not in doc.tables[0].rows[1].cells[1].text
    assert any(t.rows[0].cells[-1].text == "本轮状态" for t in doc.tables)

    # 三稿：基准是二稿那一轮（本身就是增量轮），已解决的不再往下带
    _upload(c, h, sid, NAMES[2], draft_of=two["id"])
    parse(sm, sid)
    three = c.get(f"{BASE}/{sid}").json()["files"][2]
    _confirm(c, h, sid, our=False)
    third, _ = _run(c, h, sm, sid, file_ids=[three["id"]], main_file_id=three["id"], scope="delta")
    d3 = c.get(f"{API_PREFIX}/runs/{third}").json()
    assert d3["base_run_no"] == 2 and "付款期限过长" not in {f["title"] for f in d3["findings"]}

    # 同一份二稿全文重审，用模拟服务相同的字数计量口径比较。
    started = perf_counter()
    full_second, calls_full_second = _run(c, h, sm, sid, file_ids=[two["id"]], main_file_id=two["id"])
    full_seconds = perf_counter() - started
    assert calls_full_second == 4
    with sm() as db:
        full_tokens, delta_tokens = (db.get(ReviewRun, uuid.UUID(rid)).tokens_in for rid in (full_second, second))
    print(f"二稿全文/增量：正文预览 {full['chars']}/{preview['chars']} 字，"
          f"请求 {calls_full_second}/{calls_delta} 次，模拟输入 token {full_tokens}/{delta_tokens}，"
          f"创建到完成 {full_seconds:.2f}/{delta_seconds:.2f} 秒")

    # 完全相同的新稿仍能选增量，只在本机继承基准轮结果。
    _upload(c, h, sid, NAMES[2], draft_of=three["id"])
    parse(sm, sid)
    same = c.get(f"{BASE}/{sid}").json()["files"][-1]["id"]
    _confirm(c, h, sid, our=False)
    unchanged = c.get(f"{BASE}/{sid}/outbound", params={**q, "file_ids": [same], "main_file_id": same,
                                                       "scope": "delta"}).json()
    assert unchanged["delta"]["available"] and unchanged["delta"]["identical"] and unchanged["chars"] == 0
    _, calls_same = _run(c, h, sm, sid, file_ids=[same], main_file_id=same, scope="delta")
    assert calls_same == 0

    # 一稿没有上一稿：不能只审变化；非本人 404
    r = c.post(f"{BASE}/{sid}/runs", headers=h, json={"stance": "purchase", "source": "incoming", "scope": "delta",
                                                       "file_ids": [one["id"]], "main_file_id": one["id"]})
    assert r.status_code == 409 and r.json()["code"] == "DELTA_UNAVAILABLE"
    other, _ = login(make_user("delta_other").username)
    assert other.get(f"{BASE}/{sid}/outbound", params={**q, "scope": "delta"}).status_code == 404
    with sm() as db:
        assert db.scalar(select(ReviewRun.scope).where(ReviewRun.id == uuid.UUID(second))) == "delta"
        assert db.scalar(select(Finding.prev_finding_id).where(Finding.run_id == uuid.UUID(second),
                                                               Finding.carry == "unchanged")) is not None
