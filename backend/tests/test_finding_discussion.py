"""V32-B-02：按问题讨论（虚构样本）——上下文含后部条款、各问题与普通对话互不混入、跨会话 404、超长拒绝。"""
import json
import uuid

from sqlalchemy import select, update

from app.db.models import AssistantMessage, DocNode, Finding, LlmModel
from app.main import API_PREFIX
from app.review import job as review_job
from tests.review_support import env  # noqa: F401
from tests.test_session_review import BASE, _job, _session, model, no_ner  # noqa: F401
from tests.test_tasks_api import files_dir  # noqa: F401


def _ask(c, h, sm, seen, tid, text, finding_id=None):
    from app.assistant.job import handle_assist
    body = {"message": text, **({"finding_id": finding_id} if finding_id else {})}
    r = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert r.status_code == 201, r.text
    with sm() as db:
        handle_assist(db, _job(sm, "assist", tid).payload)
    return r.json(), json.dumps(seen[-1]["messages"], ensure_ascii=False)


def test_discuss_one_finding(files_dir, model, make_user, login, db_sessionmaker):
    seen, reply = model
    user = make_user("discuss")
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    tid, _ = _session(c, h, db_sessionmaker)
    rid = c.post(f"{BASE}/{tid}/runs", headers=h, json={"stance": "sales", "source": "outgoing"}).json()["id"]
    with db_sessionmaker() as db:
        review_job.run_review(db, uuid.UUID(rid))
        first = db.scalars(select(Finding).where(Finding.run_id == uuid.UUID(rid))).first()
        nodes = list(db.scalars(select(DocNode).where(DocNode.task_id == uuid.UUID(tid), DocNode.part == "body")
                                .order_by(DocNode.ord)))
        assert len(nodes) > 8
        # 问题 A 指向文末的条款，问题 B 指向开头；两者相距超过前后各两段
        first.locs, first.problem = [nodes[-1].loc], "A问题：后部条款"
        second = Finding(**{k: getattr(first, k) for k in ("run_id", "task_id", "title", "category", "severity", "cite_status")},
                         seq=99, locs=[nodes[0].loc], problem="B问题：开头条款")
        db.add(second)
        db.commit()
        a, b = str(first.id), str(second.id)
        last_text, head_text = nodes[-1].redacted_text, nodes[0].redacted_text
    assert last_text and head_text and last_text != head_text

    # 后部条款的问题：上下文含该条款与问题结论，不含开头；整份文件不外发
    msg, sent = _ask(c, h, db_sessionmaker, seen, tid, "这条怎么改？", a)
    assert msg["user"]["finding_id"] == a and msg["assistant"]["finding_id"] == a
    assert "【审核问题】" in sent and "A问题：后部条款" in sent and last_text in sent
    assert head_text not in sent or head_text in last_text
    # 问题 B：历史只含 B 自己的，不混入 A 的提问与回复
    _, sent_b = _ask(c, h, db_sessionmaker, seen, tid, "B 这条呢？", b)
    assert "B问题：开头条款" in sent_b and "这条怎么改？" not in sent_b and "A问题" not in sent_b
    _, sent_a2 = _ask(c, h, db_sessionmaker, seen, tid, "再说一次", a)
    assert "这条怎么改？" in sent_a2 and "B 这条呢？" not in sent_a2
    # 普通对话不带问题历史，也不带问题上下文
    _, plain = _ask(c, h, db_sessionmaker, seen, tid, "合同是谁和谁签的？")
    assert "这条怎么改？" not in plain and "B 这条呢？" not in plain and "【审核问题】" not in plain

    # 跨会话（或别人）的 finding_id：404
    other = c.post(BASE, headers=h).json()["id"]
    r = c.post(f"{BASE}/{other}/messages", headers=h, json={"message": "x", "finding_id": a})
    assert r.status_code == 404
    assert c.post(f"{BASE}/{tid}/messages", headers=h, json={"message": "x", "finding_id": str(uuid.uuid4())}).status_code == 404

    # 问题与条款放不下：发送前拒绝，不截断
    with db_sessionmaker() as db:
        db.execute(update(LlmModel).values(chunk_chars=20))
        db.commit()
    r = c.post(f"{BASE}/{tid}/messages", headers=h, json={"message": "放不下", "finding_id": a})
    assert r.status_code == 409 and r.json()["code"] == "CONTEXT_TOO_LONG"
