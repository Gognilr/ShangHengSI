"""V34-B-05：片段脱敏、确认与按家模型核对；出口复检拦截名称一例。样本全部虚构，模型用模拟服务。"""
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import yaml
from sqlalchemy import select

from app.db.models import DocNode, LlmCall, Task, TaskFile
from app.embedding import engine as embedding
from app.security import crypto
from tests.fixtures.bid.samples import BIDDERS, bid2, tender_with_logo
from tests.review_support import env, llm  # noqa: F401
from tests.test_bid_checks import _jobs
from tests.test_bid_projects import BODY, ROOT, drain, owner  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")
GOLDEN = yaml.safe_load((Path(__file__).parent / "fixtures/bid/model_review_golden.yaml").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fake_embedding(monkeypatch):
    monkeypatch.setattr(embedding, "embed", lambda texts, kind: np.tile(np.eye(1, 1024, dtype=np.float32), (len(texts), 1)))


def _prepared(c, h, sm):
    base = ROOT + "/" + c.post(ROOT, headers=h, json={**BODY, "policy": "ai"}).json()["id"]
    c.post(base + "/files", headers=h, files={"files": ("虚构招标文件.docx", tender_with_logo())})
    drain(sm, base)
    c.post(base + "/files", headers=h, files=[("files", (f"投标文件{i}.docx", bid2(i))) for i in range(3)],
           data={"groups": json.dumps(list(BIDDERS), ensure_ascii=False)})
    drain(sm, base)
    assert c.post(base + "/confirm", headers=h).json()["stage"] == "ready"
    c.post(base + "/check", headers=h)
    _jobs(sm, base, "check")
    assert c.get(base).json()["model"]["state"] == "preparing"  # 本地检查完成后自动在后台准备片段
    _jobs(sm, base, "snippets")
    return base


def test_quote_context_preserves_page_and_current_term():
    from app.bid import model
    rows = [('f', 1, 'F1-PG1-B1', 'ocr_block', 1, '历史材料' * 70),
            ('f', 1, 'F1-PG2-B1', 'ocr_block', 2, '六、报价单'),
            ('f', 1, 'F1-PG2-B2', 'ocr_block', 2, '本次服务费用包括全过程费用；税率6%；工期按甲方要求。')]
    pieces = model.passages(rows)
    assert len(pieces) == 2 and '税率6%' in pieces[1]['text'] and pieces[1]['page'] == 2
    history = [{'text': f'历史合同{i}，投标文件中明确完成时间，报告完成时间按旧订单。', 'section': ''} for i in range(4)]
    pieces = [*history, {**pieces[1], 'section': ''}]
    links = model.retrieve([('R1', '投标文件中须明确完成时间')], {'A': pieces})
    assert len(links['A']['R1']) <= model.TOP and 4 in links['A']['R1']


def test_snippets_redacted_confirmed_and_judged(owner, db_sessionmaker, llm):
    c, h, _ = owner
    base = _prepared(c, h, db_sessionmaker)
    # V34Q-33 的虚构 golden 同时经过实际发送、外发记录与下面的 A-03 核对。
    from app.bid import model
    from app.redaction import engine
    case = GOLDEN["outbound_placeholder"]
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(base.rsplit("/", 1)[-1]))
        state = model._state(model._project(db, task))
        batch = state["batches"][0]
        snippet = next(s for s in state["snippets"][batch["bidder_id"]] if s["id"] == batch["snippets"][0])
        node = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc == snippet["locs"][0]))
        node.redacted_text += "\n" + case["safe"]
        node.text_enc = crypto.encrypt_text(crypto.decrypt_text(node.text_enc) + "\n" + case["original"])
        mapping = engine.load_redaction_map(db, task.id)
        mapping[case["code"]] = case["value"]
        engine.write_full_map(db, task.id, mapping)
        cross = GOLDEN["redaction"][0]
        assert cross["id"] == "cross_ocr_currency_and_address"
        engine.write_full_map(db, task.id, {**mapping, **cross["pinned"]})
        bidder = SimpleNamespace(id=uuid.uuid4(), code="A", name_enc=crypto.encrypt_text(BIDDERS[0]))
        red, _, _ = model.redact(db, task, [bidder], cross["nodes"], {}, cross["groups"])
        node.redacted_text += "\n" + model.join(list(red), red)
        node.text_enc = crypto.encrypt_text(crypto.decrypt_text(node.text_enc) + "\n" + model.join(list(red), cross["nodes"]))
        db.commit()
    view = c.get(base + "/model").json()
    assert view["state"] == "awaiting" and len(view["bidders"]) == 3
    assert [b["code"] for b in view["bidders"]] == ["[投标人A]", "[投标人B]", "[投标人C]"]
    ai = "\n".join(s["ai_text"] for b in view["bidders"] for s in b["snippets"]) + "\n".join(r["ai_text"] for r in view["requirements"])
    assert "[投标人A]" in ai and not any(name in ai for name in BIDDERS)  # 投标人名称必替换为代号
    assert "2,616,000.00" not in ai  # 报价金额不发送
    receipt = view["receipt"]
    assert receipt["calls"] == 3 and receipt["snippets"] > 0 and receipt["protected"] > 0

    sent = []

    def handler(request):
        body = json.loads(request.content)
        content = body["messages"][1]["content"]
        sent.append(content)
        reqs = re.findall(r"^\[(R\d+)\]", content, re.M)
        snippets = dict(re.findall(r"^\[([A-C]\d+)\] (.+)$", content, re.M))
        code = re.search(r"投标人代号：\[投标人([A-C])\]", content).group(1)
        first = next(iter(snippets))
        items = [{"req": r, "verdict": "响应", "snippet": first, "quote": snippets[first][:12], "reason": "片段写明满足要求"} for r in reqs]
        if code == "B":
            items[0] = {"req": reqs[0], "verdict": "疑似不响应", "snippet": first, "quote": "片段中并不存在的一句话引文",
                        "reason": "[投标人B]未提供相应材料"}
        if code == "C":
            items[1]["conflict"] = "偏离表写无偏离，正文写交货期 45 日"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"items": items}, ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    llm(handler=handler)
    assert c.post(base + "/model/confirm", headers=h).json()["state"] == "running"
    _jobs(db_sessionmaker, base, "model")
    project = c.get(base).json()
    assert project["model"] == {"state": "done", "error": None, "done": 3, "total": 3}
    assert len(sent) == 3 and not any(name in text for text in sent for name in BIDDERS)
    a, b, x = [i["id"] for i in project["bidders"]]
    result = c.get(base + "/check").json()
    cells = [cell for cell in result["cells"] if cell["source"] == "model"]
    assert {cell["bidder_id"] for cell in cells} == {a, b, x}
    assert all(cell["cite"] == "verified" for cell in cells if cell["bidder_id"] == a)
    bad = next(cell for cell in cells if cell["bidder_id"] == b and cell["verdict"] == "no")
    assert bad["cite"] == "unverified" and BIDDERS[1] in bad["reason"]  # 理由在本机还原；引文对不上标“待核实”
    issues = [i for i in result["items"] if i["source"] == "model"]
    assert {(i["type"], i["bidder_ids"][0]) for i in issues} == {("model_response", b), ("deviation_conflict", x)}
    with db_sessionmaker() as db:
        calls = db.scalars(select(LlmCall.purpose).where(LlmCall.task_id == uuid.UUID(base.rsplit("/", 1)[-1]))).all()
        assert calls.count("bid") == 3
    # A-03：实际请求与外发记录都查，使用独立的 golden 标注和完整复检上下文。
    from app.redaction.recheck import recheck, recheck_context
    with db_sessionmaker() as db:
        tid = uuid.UUID(base.rsplit("/", 1)[-1])
        ctx = recheck_context(db, tid)
        records = db.scalars(select(LlmCall).where(LlmCall.task_id == tid)).all()
        for text in [*sent, *(r.request_text for r in records)]:
            assert text and not any(value in text for value in GOLDEN["must_not_send"])
            assert recheck(text, real_values=ctx.real_values, terms=ctx.terms,
                           placeholders=ctx.placeholders, kept=ctx.kept) == []


def test_recheck_blocks_name_left_in_snippet(owner, db_sessionmaker, llm):
    """片段脱敏结果被改回含真实名称时，确认时整轮不发。"""
    c, h, _ = owner
    base = _prepared(c, h, db_sessionmaker)
    tid = uuid.UUID(base.rsplit("/", 1)[-1])
    with db_sessionmaker() as db:
        # 故意污染最后一家的一批，确认前两家也不能先发出去。
        from app.bid import model
        state = model._state(model._project(db, db.get(Task, tid)))
        last = state["batches"][-1]
        loc = next(s["locs"][0] for s in state["snippets"][last["bidder_id"]] if s["id"] in last["snippets"])
        node = db.scalars(select(DocNode).join(TaskFile, TaskFile.id == DocNode.file_id)
                          .where(DocNode.task_id == tid, DocNode.loc == loc)).one()
        node.redacted_text = crypto.decrypt_text(node.text_enc) + BIDDERS[2]
        db.commit()
    sent = llm()
    view = c.post(base + "/model/confirm", headers=h).json()
    assert view["state"] == "failed" and "出口复检拦截" in view["error"] and not sent
    with db_sessionmaker() as db:
        records = db.scalars(select(LlmCall).where(LlmCall.task_id == tid)).all()
        assert len(records) == 1 and records[0].status == "blocked" and records[0].request_text is None


@pytest.mark.parametrize("case", GOLDEN["redaction"], ids=[case["id"] for case in GOLDEN["redaction"]])
def test_snippet_redaction_golden(case, owner, db_sessionmaker):
    """通过标书实际片段脱敏入口核对标注；不为每个识别样本另写测试函数。"""
    from app.bid import model
    from app.redaction import engine
    from app.redaction.restore import restore
    c, h, _ = owner
    tid = uuid.UUID(c.post(ROOT, headers=h, json={**BODY, "policy": "ai"}).json()["id"])
    with db_sessionmaker() as db:
        task = db.get(Task, tid)
        engine.write_full_map(db, tid, case["pinned"])
        db.flush()
        bidder = SimpleNamespace(id=uuid.uuid4(), code="A", name_enc=crypto.encrypt_text(case["bidder"]))
        red, _, _ = model.redact(db, task, [bidder], case["nodes"], {}, case.get("groups"))
        mapping = engine.load_redaction_map(db, tid)
        problems = []
        if case["contains"] not in "\n".join(red.values()):
            problems.append(f"片段不符：{red}")
        if case.get("preserve_map"):
            if not case["pinned"].items() <= mapping.items():
                problems.append("片段准备删除或改写了已有对照表")
        if case.get("recheck"):
            from app.redaction.recheck import recheck
            text = model.join(list(red), red)
            assert recheck(text, real_values=mapping.values(), terms=[], placeholders=mapping) == []
            assert recheck(text + "\n" + model.join(list(red), case["nodes"]),
                           real_values=mapping.values(), terms=[], placeholders=mapping)
        if case.get("round_trip_nodes"):
            assert set(red) == set(case["nodes"])
            for loc, original in case["nodes"].items():
                assert restore(red[loc], mapping) == original
            assert not any(value in model.join(list(red), red) for value in case["hidden"])
        if sample := case.get("returns"):
            if (actual := restore(sample["model"], mapping)) != sample["expect"]:
                problems.append(f"还原不符：{actual}")
        assert not problems, problems


def test_model_quote_golden(owner, db_sessionmaker):
    """走 apply 的实际校验与写库路径，区分原样引文与改写投标人代号的引文。"""
    from app.bid import model
    from app.db.models import BidCell
    c, h, _ = owner
    base = _prepared(c, h, db_sessionmaker)
    tid = uuid.UUID(base.rsplit("/", 1)[-1])
    with db_sessionmaker() as db:
        task = db.get(Task, tid)
        state = model._state(model._project(db, task))
        batch = state["batches"][0]
        snippet = next(s for s in state["snippets"][batch["bidder_id"]] if s["id"] == batch["snippets"][0])
        loc = snippet["locs"][0]
        snippet["locs"] = [loc]
        node = db.scalar(select(DocNode).where(DocNode.task_id == tid, DocNode.loc == loc))
        node.redacted_text = GOLDEN["quotes"]["source"]
        node.text_enc = crypto.encrypt_text(node.redacted_text.replace("[投标人A]", BIDDERS[0]))
        db.flush()
        rid = batch["reqs"][0]
        results = []
        for case in GOLDEN["quotes"]["cases"]:
            model.apply(db, task, state, 0, [{"req": state["requirements"][rid]["n"], "verdict": "响应",
                "snippet": snippet["id"], "quote": case["quote"], "reason": "[投标人A]承诺提供三年质保"}])
            db.flush()
            cell = db.scalar(select(BidCell).where(BidCell.requirement_id == uuid.UUID(rid),
                                                   BidCell.bidder_id == uuid.UUID(batch["bidder_id"])))
            assert crypto.decrypt_text(cell.reason_enc) == BIDDERS[0] + "承诺提供三年质保"
            results.append(cell.cite)
        assert results == [case["cite"] for case in GOLDEN["quotes"]["cases"]]
