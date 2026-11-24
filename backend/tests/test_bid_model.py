"""V34-B-05：片段脱敏、确认与按家模型核对（V35-E-01 起为可选补查，只查 ★ 与否决条款）；出口复检拦截名称一例。
样本全部虚构，模型用模拟服务。"""
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

from app.db.models import DocNode, LlmCall, Task, TaskClauseVector, TaskFile
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
    assert c.get(base).json()["model"]["state"] is None  # 模型补查可选：本地检查完成后不自动准备
    assert c.post(base + "/model/prepare", headers=h).json()["state"] == "preparing"
    busy = c.patch(base, headers=h, json=BODY)
    assert busy.status_code == 409 and "片段正在准备" in busy.json()["message"]
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


def test_evidence_scope_filters_history_spares_and_other_asset():
    from app.bid import model

    nodes = [
        ('a', 1, 'q1', 'ocr_block', 1, '报价单'),
        ('a', 1, 'q2', 'ocr_block', 1, '工期按甲方要求。'),
        ('a', 1, 'h1', 'ocr_block', 2, '三、业绩证明文件'),
        ('a', 1, 'h2', 'ocr_block', 3, '完成时间为十个工作日。'),
        ('a', 1, 'h3', 'ocr_block', 3, '供货范围含配对法兰和紧固件。'),
        ('b', 2, 's1', 'ocr_block', 1, '二年备品备件价格清单（单独报价）'),
        ('b', 2, 's2', 'ocr_block', 1, '排液阀组件过流材质为合金。'),
        ('c', 3, 'w1', 'table_cell', 1, '位 号'),
        ('c', 3, 'w2', 'table_cell', 1, 'P-307A/B'),
        ('c', 3, 'w3', 'table_cell', 1, '供货范围含配对法兰和紧固件。'),
        ('c', 3, 'c1', 'table_cell', 2, '位号：P-207A/B'),
        ('c', 3, 'c2', 'table_cell', 2, '供货范围明确含配对法兰和紧固件。'),
    ]
    pieces = model.passages(nodes)
    reqs = [('time', '须明确完成时间'), ('flange', 'P-207A/B不带配对法兰和紧固件'),
            ('metal', 'P-207A/B过流材质为304'), ('history', '提供同类项目业绩和合同')]
    linked = model.retrieve(reqs, {'A': pieces})['A']
    locs = {rid: {loc for i in ids for loc in pieces[i]['locs']} for rid, ids in linked.items()}
    assert 'q2' in locs['time'] and 'h2' not in locs['time']
    assert 'c2' in locs['flange'] and {'h3', 'w3'}.isdisjoint(locs['flange'])
    assert 's2' not in locs['metal']
    assert 'h1' in locs['history']
    assert model._scope_gap('P-207A/B过流材质为304', 'spares', set())
    assert model._scope_gap('P-207A/B不带配对法兰', None, model._asset_ids('P-307A/B'))
    assert model._scope_gap('P-207B不带配对法兰', None, model._asset_ids('P-207A'))
    assert model._scope_gap('P-207A/B不带配对法兰', None, model._asset_ids('P-207B')) is None


def test_qualification_keeps_short_certificate_below_score_cutoff():
    from app.bid import model

    nodes = [
        ('f', 1, 'F1-PG1-B1', 'paragraph', 1, '清单：营业执照、法定代表人授权书。'),
        ('f', 1, 'F1-PG1-B2', 'paragraph', 1, '申报说明。' * 110),
        ('f', 1, 'F1-PG2-B1', 'paragraph', 2, '模板说明：附营业执照。'),
        ('f', 1, 'F1-PG3-B1', 'paragraph', 3, '模板要求填写营业执照。'),
        ('f', 1, 'F1-PG4-B1', 'ocr_block', 4, '营业执照\n统一社会信用代码：已填写。'),
        ('f', 1, 'F1-PG5-B1', 'ocr_block', 5, '资质证书\n证书编号：已填写。'),
    ]
    pieces = model.passages(nodes)
    reqs = [('qualification', '投标人须附营业执照'), ('other', '投标人须附营业执照')]
    matrix = {'A': np.array([[0.99], [0.91], [0.98], [0.93], [0.2], [0.7]])}
    queries = {rid: np.array([1.0]) for rid, _text in reqs}
    links = model.retrieve(reqs, {'A': pieces}, matrix, queries, qualifications={'qualification'})['A']
    pages = {rid: {pieces[i]['page'] for i in picks} for rid, picks in links.items()}
    assert pages == {'qualification': {1, 2, 4}, 'other': {1, 2, 3}}


def test_breadcrumb_vector_upgrades_and_reuses_cache(owner, db_sessionmaker, monkeypatch):
    from app.bid import model

    c, h, _ = owner
    task_id = uuid.UUID(c.post(ROOT, headers=h, json=BODY).json()['id'])
    pieces = model.passages([
        ('a', 1, 'F1-PG1-B1', 'paragraph', 1, '第一章 商务文件'),
        ('a', 1, 'F1-PG1-B2', 'ocr_block', 1, '（一）资格证明'),
        ('a', 1, 'F1-PG2-B1', 'ocr_block', 2, '证照随附。'),
        ('b', 2, 'F2-PG1-B1', 'paragraph', 1, '另一个文件。'),
    ])
    body, other = pieces[-2:]
    assert other['breadcrumb'] == ()
    plain, contextual = np.eye(2, 1024, dtype=np.float32)
    inputs = []

    def embed(texts, kind):
        inputs.extend(texts)
        return np.array([contextual])

    monkeypatch.setattr(embedding, 'embed', embed)
    with db_sessionmaker() as db:
        task = db.get(Task, task_id)
        db.add(TaskClauseVector(task_id=task_id, file_seq=1, first_loc=body['locs'][0], locs=body['locs'],
                                embedding=plain, model=embedding.model_id()))
        db.flush()
        for _ in range(2):
            matrix, _ = model._embed(db, task, [body])
            np.testing.assert_allclose(matrix[0], 0.75 * plain + 0.25 * contextual)
    assert inputs == ['第一章 商务文件 > (一)资格证明\n证照随附。']


def test_snippets_redacted_confirmed_and_judged(owner, db_sessionmaker, llm, monkeypatch):
    c, h, _ = owner
    base = _prepared(c, h, db_sessionmaker)
    # V34Q-33 的虚构 golden 同时经过实际发送、外发记录与下面的 A-03 核对。
    from app.bid import model
    from app.redaction import engine, ner
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
        vocabulary = GOLDEN["prompt_vocabulary"]
        line, ordinary = vocabulary["original"], vocabulary["ordinary"]
        with monkeypatch.context() as local:
            local.setattr(ner, "raw_recognize", lambda text: [ner.Entity(0, len(ordinary), "PERSON", 0.99)] if text == line else [])
            red, _, _ = model.redact(db, task, [bidder], {"F99-P1": line}, {})
            explicit = engine.compute([engine.NodeIn("F99-P1", line)], task.stance, style="neutral",
                                      user_terms=[engine.UserTerm(ordinary, "CUSTOM")])
        assert ordinary in red["F99-P1"] and ordinary not in engine.load_redaction_map(db, task.id).values()
        assert ordinary in explicit.mapping.values() and ordinary not in explicit.redacted["F99-P1"]
        node.redacted_text += "\n" + red["F99-P1"]
        node.text_enc = crypto.encrypt_text(crypto.decrypt_text(node.text_enc) + "\n" + line)
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
        sent.append("\n".join(m["content"] for m in body["messages"]))
        reqs = re.findall(r"^\[(R\d+)\]", content, re.M)
        snippets = dict(re.findall(r"^\[([A-C]\d+)\] (.+)$", content, re.M))
        code = re.search(r"投标人代号：\[投标人([A-C])\]", content).group(1)
        first = next(iter(snippets))
        items = [{"req": r, "verdict": "响应", "snippet": first, "quote": snippets[first][:12], "reason": "片段写明满足要求"} for r in reqs]
        if code == "A":
            items[0].update(verdict="疑似不响应", reason="片段未注明所要求的条件，因此不符合要求。")
            items[1].update(verdict="疑似不响应", reason="片段明确拒绝提供规定的材料。")
        if code == "B":
            items[0] = {"req": reqs[0], "verdict": "疑似不响应", "snippet": first, "quote": "片段中并不存在的一句话引文",
                        "reason": "[投标人 Ｂ]明确拒绝提供相应材料"}
        if code == "C":
            items[1]["conflict"] = "投标人C的偏离表写无偏离，正文写交货期 45 日"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"items": items}, ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    llm(handler=handler)
    assert c.post(base + "/model/confirm", headers=h).json()["state"] == "running"
    busy = c.patch(base, headers=h, json=BODY)
    assert busy.status_code == 409 and "正在发送" in busy.json()["message"]
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
    assert {(i["type"], i["bidder_ids"][0]) for i in issues} == {
        ("model_response", a), ("model_response", b), ("deviation_conflict", x),
    }
    assert BIDDERS[2] in next(i for i in issues if i["type"] == "deviation_conflict")["rows"][0]["value"]
    findings = {(i["bidder_ids"][0], i["level"]): i for i in issues if i["type"] == "model_response"}
    assert set(findings) == {(a, "general"), (a, "veto"), (b, "general")}
    for bid in (a, b):
        finding = findings[bid, "general"]
        assert finding["article"] is None and finding["title"].startswith("响应待核实：")
        assert any(row["label"] == "核实提示" for row in finding["rows"])
        cell = next(c for c in cells if c["bidder_id"] == bid and c["requirement_id"] == finding["requirement_id"])
        assert cell["verdict"] == "no" and cell["cite"] == ("verified" if bid == a else "unverified")
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

    previous = c.get(base + "/sent").json()
    assert c.post(base + "/reopen", headers=h).status_code == 200
    assert c.patch(base, headers=h, json=BODY).json()["policy"] == "local_only"
    view = c.get(base + "/model").json()
    assert view["state"] is None and view["receipt"] is None and not view["bidders"]
    assert view["sent_count"] == 3 and c.get(base + "/sent").json() == previous
    assert c.post(base + "/model/confirm", headers=h).json()["code"] == "LOCAL_ONLY"

    ref = {"requirement_id": bad["requirement_id"], "bidder_id": bad["bidder_id"]}
    detail = c.get(base + "/cell", params=ref).json()
    assert detail["requirement"]["method"] == "manual" and detail["cell"]["source"] == "model"
    assert c.patch(f"{base}/requirements/{ref['requirement_id']}", headers=h, json={"method": "local"}).status_code == 200
    assert c.post(base + "/check", headers=h).json()["stage"] == "checking"
    _jobs(db_sessionmaker, base, "check")
    refreshed = c.get(base + "/check").json()
    assert refreshed["state"] == "done"
    assert all(row["source"] != "model" for row in [*refreshed["cells"], *refreshed["items"]])
    assert c.get(base + "/sent").json() == previous


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


def test_reprepare_reuses_snippet_ner_cache(owner, db_sessionmaker, monkeypatch):
    """V35-B-01：重新准备片段时实体识别走加密缓存，结果与首次一致；整份脱敏重算不清掉片段缓存。"""
    c, h, _ = owner
    base = _prepared(c, h, db_sessionmaker)
    tid = uuid.UUID(base.rsplit("/", 1)[-1])
    from app.bid import model
    from app.db.models import NerNodeResult
    from app.redaction import engine, ner
    with db_sessionmaker() as db:
        engine.redact_task(db, db.get(Task, tid))
        before = {r.loc: r.redacted_text for r in db.scalars(select(DocNode).where(
            DocNode.task_id == tid, DocNode.redacted_text.is_not(None)))}
        assert any(loc.startswith(model._NER_PREFIX) for loc in db.scalars(
            select(NerNodeResult.loc).where(NerNodeResult.task_id == tid)))
        calls = []
        real = ner.recognize
        monkeypatch.setattr(ner, "recognize", lambda text: calls.append(1) or real(text))
        model.prepare(db, db.get(Task, tid))
        after = {r.loc: r.redacted_text for r in db.scalars(select(DocNode).where(
            DocNode.task_id == tid, DocNode.redacted_text.is_not(None)))}
        assert not calls and after == before


@pytest.mark.parametrize("case", GOLDEN["redaction"], ids=[case["id"] for case in GOLDEN["redaction"]])
def test_snippet_redaction_golden(case, owner, db_sessionmaker):
    """通过标书实际片段脱敏入口核对标注；不为每个识别样本另写测试函数。"""
    from app.bid import model
    from app.redaction import engine
    from app.redaction.restore import check
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
                assert check(red[loc], mapping, bid=True).text == original
            assert not any(value in model.join(list(red), red) for value in case["hidden"])
        if sample := case.get("returns"):
            checked = check(sample["model"], mapping, bid=True)
            assert checked.text == sample["expect"]
            assert [mark.raw for mark in checked.marks if mark.kind == "unknown"] == sample.get("unknown", [])
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
        original = node.redacted_text.replace("[投标人A]", BIDDERS[0]).replace("[投标人B]", BIDDERS[1])
        node.text_enc = crypto.encrypt_text(original)
        db.flush()
        rid = batch["reqs"][0]
        results = []
        for case in GOLDEN["quotes"]["cases"]:
            model.apply(db, task, state, 0, [{"req": state["requirements"][rid]["n"], "verdict": "响应",
                "snippet": snippet["id"], "quote": case["quote"], "reason": "[投标人  Ａ]承诺提供三年质保"}])
            db.flush()
            cell = db.scalar(select(BidCell).where(BidCell.requirement_id == uuid.UUID(rid),
                                                   BidCell.bidder_id == uuid.UUID(batch["bidder_id"])))
            assert crypto.decrypt_text(cell.reason_enc) == BIDDERS[0] + "承诺提供三年质保"
            results.append(cell.cite)
        assert results == [case["cite"] for case in GOLDEN["quotes"]["cases"]]


def test_snippets_wait_for_ocr_and_refresh_after(owner, db_sessionmaker, monkeypatch):
    """V34-B-08c：扫描页识别未完成时不准备片段；V35-E-01 起识别补完后不自动准备，已准备未发送的片段作废。"""
    from app.bid import model, pipeline
    c, h, _ = owner
    pending = {"on": True}
    monkeypatch.setattr(pipeline, "has_pending", lambda db, task_id: pending["on"])
    base = ROOT + "/" + c.post(ROOT, headers=h, json={**BODY, "policy": "ai"}).json()["id"]
    c.post(base + "/files", headers=h, files={"files": ("虚构招标文件.docx", tender_with_logo())})
    drain(db_sessionmaker, base)
    c.post(base + "/files", headers=h, files=[("files", (f"投标文件{i}.docx", bid2(i))) for i in range(3)],
           data={"groups": json.dumps(list(BIDDERS), ensure_ascii=False)})
    drain(db_sessionmaker, base)
    c.post(base + "/confirm", headers=h)
    c.post(base + "/check", headers=h)
    _jobs(db_sessionmaker, base, "check")
    assert c.get(base).json()["model"]["state"] is None  # 识别中不自动准备
    busy = c.post(base + "/model/prepare", headers=h)
    assert busy.status_code == 409 and busy.json()["code"] == "OCR_PENDING"
    pending["on"] = False
    tid = uuid.UUID(base.rsplit("/", 1)[-1])
    with db_sessionmaker() as db:
        pipeline._recheck(db, tid)
    assert c.get(base).json()["model"]["state"] is None
    assert c.post(base + "/model/prepare", headers=h).json()["state"] == "preparing"
    _jobs(db_sessionmaker, base, "snippets")
    assert c.get(base + "/model").json()["state"] == "awaiting"
    with db_sessionmaker() as db:  # 已准备未发送：再次补完识别时作废，由用户重新选择补查
        pipeline._recheck(db, tid)
    assert c.get(base).json()["model"]["state"] is None


def test_close_snippets_only_rewrites_confirmed_spans(monkeypatch):
    """V35-B-02 复核：边界不能确认的宽松命中只由出口拦截，不改写正文；同一串里的多处紧凑命中一轮补齐。"""
    from app.bid import model as M
    from app.redaction import recheck as R
    terms = ["AB123", "12M", "会社"]
    monkeypatch.setattr(R, "recheck_context", lambda db, task_id: R.RecheckContext(terms=terms))
    texts = {"a": "累计用量 9123456780 件", "b": "设备型号 AB", "c": "123 件备品", "d": "频率 12㎒，㍿某某",
             "e": "型号 AB-123 A-B123 AB1-23 AB12-3 齐", "f": "账号 1234 5678，"}
    mapping = {"[[ID_1]]": "12345678"}
    M._close_snippets(None, SimpleNamespace(id=None), [["a"], ["b", "c"], ["d"], ["e"], ["f"]], texts, mapping)
    assert texts["a"] == "累计用量 9123456780 件"  # 长数字嵌在更长的数字串里：普通数量保持原样
    assert (texts["b"], texts["c"]) == ("设备型号 AB", "123 件备品")  # 跨片段换行不合并替换
    assert texts["d"] == "频率 12㎒，㍿某某"  # 只对上原字符归一化展开的一部分
    assert re.fullmatch(r"型号 (\[\[[A-Z]+_\d+\]\] ){4}齐", texts["e"])
    assert texts["f"].startswith("账号 [[")

    def blocked(text):
        return R.recheck(text, real_values=list(mapping.values()), terms=terms, placeholders=list(mapping))
    assert blocked(texts["a"]) and blocked(M.join(["b", "c"], texts)) and blocked(texts["d"])  # 仍由出口拦截
    assert not blocked(texts["e"]) and not blocked(texts["f"])
