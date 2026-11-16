"""V34-B-05：片段脱敏、确认与按家模型核对；出口复检拦截名称一例。样本全部虚构，模型用模拟服务。"""
import json
import re
import uuid

import httpx
import numpy as np
import pytest
from sqlalchemy import select

from app.db.models import DocNode, LlmCall, TaskFile
from app.embedding import engine as embedding
from app.security import crypto
from tests.fixtures.bid.samples import BIDDERS, bid2, tender_with_logo
from tests.review_support import env, llm  # noqa: F401
from tests.test_bid_checks import _jobs
from tests.test_bid_projects import BODY, ROOT, drain, owner  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


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


def test_snippets_redacted_confirmed_and_judged(owner, db_sessionmaker, llm):
    c, h, _ = owner
    base = _prepared(c, h, db_sessionmaker)
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


def test_recheck_blocks_name_left_in_snippet(owner, db_sessionmaker, llm):
    """片段脱敏结果被改回含真实名称时，确认时整轮不发。"""
    c, h, _ = owner
    base = _prepared(c, h, db_sessionmaker)
    tid = uuid.UUID(base.rsplit("/", 1)[-1])
    with db_sessionmaker() as db:
        node = db.scalars(select(DocNode).join(TaskFile, TaskFile.id == DocNode.file_id)
                          .where(DocNode.task_id == tid, TaskFile.bidder_id.is_not(None), DocNode.redacted_text.is_not(None))).first()
        node.redacted_text = crypto.decrypt_text(node.text_enc) + BIDDERS[2]
        db.commit()
    sent = llm()
    view = c.post(base + "/model/confirm", headers=h).json()
    assert view["state"] == "failed" and "出口复检拦截" in view["error"] and not sent
