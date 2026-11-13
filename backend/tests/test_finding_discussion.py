"""V32-B-02：按问题讨论（虚构样本）——上下文含后部条款、各问题与普通对话互不混入、跨会话 404、超长拒绝。"""
import json
import uuid

from sqlalchemy import func, select, update

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
    reply[0] = f'请核对{nodes[-1].loc}。'
    msg, sent = _ask(c, h, db_sessionmaker, seen, tid, "这条怎么改？", a)
    shown = c.get(f'{BASE}/{tid}/messages').json()['items'][-1]
    assert '虚构合同.docx' in shown['content'] and not shown['marks']
    delta = c.get(f'{BASE}/{tid}/messages/{shown["id"]}', params={'offset': 0}).json()
    assert delta['text_from_offset'] == shown['content']
    from types import SimpleNamespace
    from app.assistant.service import _incremental, _discussion_check
    from app.db.models import Task
    from app.security.errors import AppError
    import pytest
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        partial = SimpleNamespace(content_redacted='请核对F1-P', finding_id=uuid.UUID(a))
        prefix, offset = _incremental(db, task, partial, {}, 0, finished=False, dates=None)
        partial.content_redacted = reply[0]
        tail, _ = _incremental(db, task, partial, {}, offset, finished=True, dates=None)
        assert prefix + tail == shown['content']
        with pytest.raises(AppError) as blocked_input:
            _discussion_check(db, task, uuid.UUID(a), {}, '联系电话13800138000', None)
        assert blocked_input.value.code == 'DISCUSSION_INPUT_BLOCKED'
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

    # 模型建议条文命中复检：发送前明确来源，可去掉后通过原网关发送。
    with db_sessionmaker() as db:
        db.get(Finding, uuid.UUID(b)).suggested_text = '联系电话13800138000。'
        db.commit()
    body = {'message': '如何修改？', 'finding_id': b}
    blocked = c.post(f'{BASE}/{tid}/preview', headers=h, json=body)
    assert blocked.status_code == 409 and blocked.json()['code'] == 'DISCUSSION_ADVICE_BLOCKED'
    assert '电话' in blocked.json()['message'] and '13800138000' not in blocked.json()['message']
    assert c.post(f'{BASE}/{tid}/messages', headers=h, json=body).json()['code'] == 'DISCUSSION_ADVICE_BLOCKED'
    from app.assistant.job import handle_assist
    r = c.post(f'{BASE}/{tid}/messages', headers=h, json={**body, 'params': {'omit_finding_advice': True}})
    assert r.status_code == 201, r.text
    with db_sessionmaker() as db:
        handle_assist(db, _job(db_sessionmaker, 'assist', tid).payload)
    assert '13800138000' not in json.dumps(seen[-1]['messages'])

    # 问题与条款放不下：发送前拒绝，不截断
    with db_sessionmaker() as db:
        db.execute(update(LlmModel).values(chunk_chars=20))
        db.commit()
    r = c.post(f"{BASE}/{tid}/messages", headers=h, json={"message": "放不下", "finding_id": a})
    assert r.status_code == 409 and r.json()["code"] == "CONTEXT_TOO_LONG"


def test_draft_adopt_and_export_mainline(files_dir, model, make_user, login, db_sessionmaker):
    """V32-B-03 主线：上传 → 确认 → 问答 → 审核 → 讨论 → 保存讨论稿 → 采纳 → 导出修订版；含未知代号的讨论稿降为批注。"""
    import re
    from app.db.models import LlmCall
    from tests.test_session_review import BUYER, _code
    seen, reply = model
    user = make_user("draft")
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    tid, detail = _session(c, h, db_sessionmaker)  # 文件只上传、确认这一次
    buyer = _code(db_sessionmaker, tid, BUYER)

    def calls():
        with db_sessionmaker() as db:
            return db.scalar(select(func.count()).select_from(LlmCall))

    _ask(c, h, db_sessionmaker, seen, tid, "合同是谁和谁签的？")
    before = calls()
    assert c.get(f"{BASE}/{tid}/outbound", params={"mode": "review"}).status_code == 200  # 切到全面审核：零模型调用
    assert calls() == before
    rid = c.post(f"{BASE}/{tid}/runs", headers=h, json={"stance": "sales", "source": "outgoing"}).json()["id"]
    with db_sessionmaker() as db:
        review_job.run_review(db, uuid.UUID(rid))
        rows = list(db.scalars(select(Finding).where(Finding.run_id == uuid.UUID(rid)).order_by(Finding.seq)))
        rows.append(Finding(**{k: getattr(rows[0], k) for k in ("run_id", "task_id", "title", "category", "severity",
                                                               "cite_status", "problem")}, seq=99))
        db.add(rows[-1])
        nodes = [n for n in db.scalars(select(DocNode).where(DocNode.task_id == uuid.UUID(tid), DocNode.part == "body")
                                       .order_by(DocNode.ord)) if n.redacted_text and "[[" not in n.redacted_text
                 and len(n.redacted_text) >= 8]
        # 两个问题各指向一段不含代号的正文，引用该段开头几个字
        quotes = []
        for f, n in zip(rows[:2], nodes[:2]):
            f.locs, f.missing, f.quote_orig_enc, f.quote_model = [n.loc], False, None, n.redacted_text[:6]
            quotes.append(n.redacted_text[:6])
        db.commit()
        a, b = str(rows[0].id), str(rows[1].id)

    # 讨论：模型把条文放在固定标记内；界面取标记内的已还原文字给用户核对
    reply[0] = f"建议改为：\n【修改条文】{buyer}应在十日内付款。【/修改条文】\n理由：明确期限。"
    msg, _ = _ask(c, h, db_sessionmaker, seen, tid, "这条怎么改？", a)
    shown = c.get(f"{BASE}/{tid}/messages").json()["items"][-1]
    assert shown["id"] == msg["assistant"]["id"] and BUYER in shown["content"]
    draft = re.search(r"【修改条文】(.*?)【/修改条文】", shown["content"], re.S).group(1).strip()
    model_calls = calls()

    r = c.put(f"{API_PREFIX}/findings/{a}/draft", headers=h, json={"text": draft, "message_id": shown["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["discussed_text"] == f"{BUYER}应在十日内付款。" and out["use_discussed"] and not out["unresolved"]
    with db_sessionmaker() as db:
        row = db.get(Finding, uuid.UUID(a))
        assert row.discussed_text == f"{buyer}应在十日内付款。" and BUYER not in row.discussed_text  # 代号文本入库
        original_suggested = row.suggested_text
    # 别的问题的消息不能挂到这个问题上
    assert c.put(f"{API_PREFIX}/findings/{b}/draft", headers=h,
                 json={"text": "x", "message_id": shown["id"]}).status_code == 404
    # 第二个问题：手工整理的稿里有未知代号，界面标出，导出降为批注
    r = c.put(f"{API_PREFIX}/findings/{b}/draft", headers=h, json={"text": "由[[ORG_9]]承担全部责任。"})
    assert r.status_code == 200 and r.json()["unresolved"] == 1
    for fid in (a, b):
        assert c.patch(f"{API_PREFIX}/findings/{fid}", headers=h, json={"handling_status": "adopted"}).status_code == 200

    r = c.get(f"{API_PREFIX}/runs/{rid}/revised", params={"file_seq": 1})
    assert r.status_code == 200, r.text
    assert r.headers["X-SI-Revised"] == "1" and r.headers["X-SI-Commented"] == "1"
    from app.export.revised_docx import _views
    text = "".join(_views(r.content)[1])
    assert f"{BUYER}应在十日内付款。" in text and "[[ORG_9]]" not in text  # 修订（接受视图）只含合格的讨论稿
    # 改回原审核建议：原 suggested_text 未被覆盖
    r = c.patch(f"{API_PREFIX}/findings/{a}", headers=h, json={"use_discussed": False})
    assert r.status_code == 200 and r.json()["use_discussed"] is False
    with db_sessionmaker() as db:
        assert db.get(Finding, uuid.UUID(a)).suggested_text == original_suggested
    assert calls() == model_calls  # 保存、采纳、导出零模型调用
