"""T-49：本人会话真实接口、worker、出口复检与模拟流式模型。"""
import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.admin.settings_store import set_setting
from app.db.base import utcnow
from app.db.models import AssistantMessage, AuditLog, DocNode, Job, LlmCall, RedactionSuspect, Task, TaskFile
from app.llm import gateway
from app.main import API_PREFIX
from app.redaction import engine, ner
from app.security import crypto
from tests.review_support import env  # noqa: F401
from tests.test_gateway_stream import Events

BASE = API_PREFIX + "/assistant/sessions"
REAL = "虚构星河贸易有限公司"


@pytest.fixture
def conversation(env, make_user, login, db_sessionmaker, monkeypatch):
    monkeypatch.setattr(ner, "recognize", lambda text: [])
    user = make_user("assist_chat")
    c, csrf = login(user.username)
    h = {"X-CSRF-Token": csrf}
    r = c.post(BASE, headers=h)
    assert r.status_code == 201
    tid = r.json()["id"]
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.title = "虚构敏感会话标题"
        f = TaskFile(task_id=task.id, seq=1, is_main=True, original_name="虚构敏感文件名.docx", ext="docx",
                     size_bytes=1, sha256="0" * 64, storage_key=uuid.uuid4().hex, confirmed_at=utcnow(), doc_type="contract")
        db.add(f)
        db.flush()
        db.add(DocNode(task_id=task.id, file_id=f.id, loc="F1-P1", ord=1, kind="paragraph", part="body",
                       text_enc=crypto.encrypt_text(REAL + "；电话：13800138001；金额：100000"),
                       redacted_text="[[ORG_1]]；电话：[[TEL_1]]；金额：[[AMT_1]]"))
        engine.write_full_map(db, task.id, {"[[ORG_1]]": REAL, "[[TEL_1]]": "13800138001", "[[AMT_1]]": "100000"})
        db.commit()
    return c, h, tid, user


def install(monkeypatch, chunks=("你好 [[OR", "G_1]]"), error=None):
    requests = []

    def handle(req):
        requests.append(req)
        return httpx.Response(200, stream=Events(chunks, error))

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    return requests


def send(conversation, message="说明术语", **kw):
    c, h, tid, _ = conversation
    return c.post(f"{BASE}/{tid}/messages", headers=h, json={"message": message, **kw})


def run(sm, tid):
    from app.assistant.job import handle_assist
    with sm() as db:
        job = db.scalar(select(Job).where(Job.type == "assist", Job.payload["task_id"].astext == tid).order_by(Job.id.desc()))
        handle_assist(db, job.payload)


def test_t49_1_3_15_17_send_restore_outbound_and_logs(conversation, db_sessionmaker, monkeypatch, caplog):
    from app.assistant.prompt import system_prompt
    c, h, tid, _ = conversation
    requests = install(monkeypatch)
    with caplog.at_level("INFO"):
        preview = c.post(f"{BASE}/{tid}/preview", headers=h, json={"message": REAL + "的术语"})
        assert preview.status_code == 200 and "[[ORG_1]]" in preview.json()["redacted"]
        r = send(conversation, REAL + "的术语")
        assert r.status_code == 201, r.text
        mid = r.json()["assistant"]["id"]
        run(db_sessionmaker, tid)
        result = c.get(f"{BASE}/{tid}/messages/{mid}?offset=0").json()
    assert result["status"] == "done" and result["text_from_offset"] == "你好 " + REAL
    body = json.loads(requests[0].content)
    assert body["messages"][0] == {"role": "system", "content": system_prompt()}
    outbound = json.dumps(body["messages"], ensure_ascii=False)
    assert "【代号说明】" in outbound and "[[ORG_1]]" in outbound
    for secret in (REAL, "虚构敏感会话标题", "虚构敏感文件名", "13800138001", "100000"):
        assert secret not in outbound and secret not in caplog.text
    with db_sessionmaker() as db:
        call = db.scalar(select(LlmCall))
        assert (call.purpose, call.task_id, call.task_title) == ("assist", uuid.UUID(tid), "虚构敏感会话标题")
        assert REAL not in "".join(db.scalars(select(AssistantMessage.content_redacted)))


def test_t49_2_11_12_busy_and_length(conversation, db_sessionmaker):
    c, h, tid, _ = conversation
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.status = "awaiting_confirm"
        db.commit()
    assert send(conversation).status_code == 409
    with db_sessionmaker() as db:
        db.get(Task, uuid.UUID(tid)).status = "active"
        set_setting(db, "model.chunk_chars", 1, None)
        db.commit()
    r = send(conversation)
    assert r.status_code == 409 and r.json()["code"] == "ASSIST_TOO_LONG"
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(AssistantMessage)) == 0
        set_setting(db, "model.chunk_chars", 60000, None)
        db.commit()
    assert send(conversation).status_code == 201
    r = send(conversation)
    assert r.status_code == 409 and "上一条回复尚未完成" in r.json()["message"]


def test_t49_4_suspects_do_not_write(conversation, db_sessionmaker):
    r = send(conversation, "请说明星海公司")
    assert r.status_code == 409 and r.json()["code"] == "CHAT_SUSPECTS_PENDING", r.text
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(AssistantMessage)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.type == "assist")) == 0


def test_t49_8_worker_blocked(conversation, db_sessionmaker, monkeypatch):
    requests = install(monkeypatch)
    _, _, tid, _ = conversation
    r = send(conversation)
    assert r.status_code == 201
    with db_sessionmaker() as db:
        node = db.scalar(select(DocNode).where(DocNode.task_id == uuid.UUID(tid)))
        node.redacted_text = REAL
        db.commit()
    run(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        reply = db.get(AssistantMessage, r.json()["assistant"]["id"])
        assert (reply.status, reply.error_code) == ("failed", "BLOCKED")
        call = db.scalar(select(LlmCall))
        assert call.status == "blocked" and call.request_text is None
    assert requests == []


def test_t49_9_18_partial_failure_and_retry(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    install(monkeypatch, ("生成部分",), httpx.ReadTimeout("替身超时"))
    r = send(conversation)
    assert r.status_code == 201
    mid = r.json()["assistant"]["id"]
    run(db_sessionmaker, tid)
    result = c.get(f"{BASE}/{tid}/messages/{mid}").json()
    assert result["status"] == "failed" and result["text_from_offset"] == "生成部分"
    retry = c.post(f"{BASE}/{tid}/messages/{mid}/retry", headers=h)
    assert retry.status_code == 201, retry.text
    assert retry.json()["assistant"]["id"] != mid
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(AssistantMessage).where(AssistantMessage.role == "user")) == 1


def test_t49_10_requeue_existing_content_and_final_failure(conversation, db_sessionmaker, monkeypatch):
    from app.db import jobs
    requests = install(monkeypatch)
    _, _, tid, _ = conversation
    r = send(conversation)
    assert r.status_code == 201
    with db_sessionmaker() as db:
        reply = db.get(AssistantMessage, r.json()["assistant"]["id"])
        reply.content_redacted = "已收到"
        reply.status = "streaming"
        db.commit()
    run(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        reply = db.get(AssistantMessage, r.json()["assistant"]["id"])
        assert (reply.status, reply.error_code) == ("failed", "ASSIST_INTERRUPTED")
    assert requests == []
    r = send(conversation)
    with db_sessionmaker() as db:
        job = db.scalar(select(Job).where(Job.type == "assist").order_by(Job.id.desc()))
        jobs.fail(db, job.id, "NO_HANDLER")
        db.refresh(db.get(AssistantMessage, r.json()["assistant"]["id"]))
        assert db.get(AssistantMessage, r.json()["assistant"]["id"]).status == "failed"


def test_t49_7_stop_before_worker(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    requests = install(monkeypatch)
    r = send(conversation)
    mid = r.json()["assistant"]["id"]
    assert c.post(f"{BASE}/{tid}/messages/{mid}/stop", headers=h).json()["status"] == "stopped"
    run(db_sessionmaker, tid)
    assert requests == []


def test_t49_14_user_daily_cap(conversation, db_sessionmaker, monkeypatch):
    _, _, tid, _ = conversation
    install(monkeypatch)
    with db_sessionmaker() as db:
        set_setting(db, "model.user_daily_cap", 1, None)
        db.commit()
    assert send(conversation).status_code == 201
    run(db_sessionmaker, tid)
    r = send(conversation)
    assert r.status_code == 409 and r.json()["code"] == "USER_CAP_REACHED"


def test_t49_16_confirm_guards_and_activity(conversation, db_sessionmaker):
    from app.db.models import Party, RedactionTerm
    c, h, tid, _ = conversation
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.status = "awaiting_confirm"
        file = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
        file.confirmed_at = None
        db.add(Party(task_id=task.id, code="[[ORG_1]]", role_label="买方", country="中国", org_type="企业"))
        suspect = RedactionSuspect(task_id=task.id, loc="F1-P1", char_start=0, char_end=2, reason="before_company")
        db.add(suspect)
        db.commit()
        sid = suspect.id
    assert c.post(f"{BASE}/{tid}/confirm", headers=h).json()["code"] == "SUSPECTS_PENDING"
    with db_sessionmaker() as db:
        db.get(RedactionSuspect, sid).decision = "ignore"
        node = db.scalar(select(DocNode).where(DocNode.task_id == uuid.UUID(tid)))
        node.redacted_text = REAL
        db.commit()
    assert c.post(f"{BASE}/{tid}/confirm", headers=h).json()["code"] == "OUTBOUND_BLOCKED"
    with db_sessionmaker() as db:
        db.scalar(select(DocNode).where(DocNode.task_id == uuid.UUID(tid))).redacted_text = "[[ORG_1]]"
        db.commit()
    r = c.post(f"{BASE}/{tid}/confirm", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert r.json()["files"][0]["confirmed_at"] is not None
    with db_sessionmaker() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "assistant_files_confirm")) is not None
        learned = db.scalar(select(RedactionTerm).where(RedactionTerm.kind == "learned"))
        assert learned is not None and crypto.decrypt_text(learned.term_enc) == REAL


def test_message_owner_isolation(conversation, db_sessionmaker, make_user, login):
    _, _, tid, _ = conversation
    mid = send(conversation).json()["assistant"]["id"]
    make_user("assist_other", role="admin")
    c, csrf = login("assist_other")
    h = {"X-CSRF-Token": csrf}
    for r in [c.get(f"{BASE}/{tid}/messages"), c.get(f"{BASE}/{tid}/messages/{mid}"),
              c.post(f"{BASE}/{tid}/messages/{mid}/stop", headers=h),
              c.post(f"{BASE}/{tid}/messages/{mid}/retry", headers=h),
              c.post(f"{BASE}/{tid}/confirm", headers=h)]:
        assert r.status_code == 404


def test_t49_13_history_only_ten_done_and_only_used_codes(conversation, db_sessionmaker, monkeypatch):
    _, _, tid, _ = conversation
    with db_sessionmaker() as db:
        for i in range(14):
            db.add(AssistantMessage(task_id=uuid.UUID(tid), role="user" if i % 2 == 0 else "assistant",
                                    status="done", content_redacted=f"历史片段{i}"))
        for state in ("failed", "stopped"):
            db.add(AssistantMessage(task_id=uuid.UUID(tid), role="assistant", status=state, content_redacted="不带失败停止内容"))
        engine.write_full_map(db, uuid.UUID(tid), {"[[ORG_1]]": REAL, "[[TEL_8]]": "13800138001"})
        db.commit()
    requests = install(monkeypatch)
    assert send(conversation).status_code == 201
    run(db_sessionmaker, tid)
    messages = json.loads(requests[0].content)["messages"]
    assert [m["content"] for m in messages[2:-1]] == [f"历史片段{i}" for i in range(4, 14)]
    assert "[[TEL_8]]" not in str(messages)
    assert "不带失败停止内容" not in str(messages)


def test_t49_5_6_actual_worker_polling_thirty_chunks(conversation, db_sessionmaker, monkeypatch):
    c, _, tid, _ = conversation
    result = send(conversation)
    mid = result.json()["assistant"]["id"]
    chunks = ["说明" * 100] * 14 + ["说明" * 100 + "[[OR", "G_1]] 的" + "后续" * 100] + ["后续" * 100] * 14
    shown, offsets = [], [0]

    class Polling(httpx.SyncByteStream):
        def __iter__(self):
            for text in chunks:
                yield ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n").encode()
                r = c.get(f"{BASE}/{tid}/messages/{mid}?offset={offsets[-1]}")
                assert r.status_code == 200
                data = r.json()
                assert "[[" not in data["text_from_offset"]
                shown.append(data["text_from_offset"])
                offsets.append(data["offset"])
            yield b"data: [DONE]\n\n"

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, stream=Polling())))
    run(db_sessionmaker, tid)
    data = c.get(f"{BASE}/{tid}/messages/{mid}?offset={offsets[-1]}").json()
    shown.append(data["text_from_offset"])
    assert data["status"] == "done"
    assert "".join(shown) == "".join(chunks).replace("[[ORG_1]]", REAL)
    assert offsets == sorted(offsets)


def test_t49_7_stop_streaming_preserves_sent_record(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    mid = send(conversation).json()["assistant"]["id"]

    class StopStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            assert c.post(f"{BASE}/{tid}/messages/{mid}/stop", headers=h).status_code == 200
            yield b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n'
            raise AssertionError("停止后不应再接收")

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, stream=StopStream())))
    run(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        row = db.get(AssistantMessage, mid)
        call = db.scalar(select(LlmCall))
        assert row.status == "stopped" and row.content_redacted == "firstsecond"
        assert call.status == "sent" and call.response_text == row.content_redacted


def test_unconfirmed_file_not_in_request_and_selected_files_are_snapshotted(conversation, db_sessionmaker, monkeypatch):
    _, _, tid, _ = conversation
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        f = TaskFile(task_id=task.id, seq=2, is_main=False, original_name="未确认.docx", ext="docx", size_bytes=1,
                     sha256="0" * 64, storage_key=uuid.uuid4().hex)
        db.add(f)
        db.flush()
        db.add(DocNode(task_id=task.id, file_id=f.id, loc="F2-P1", ord=1, kind="paragraph", part="body",
                       text_enc=crypto.encrypt_text("未确认资料"), redacted_text="未确认资料"))
        db.commit()
    requests = install(monkeypatch)
    r = send(conversation)
    assert r.status_code == 201
    run(db_sessionmaker, tid)
    assert "未确认资料" not in requests[0].content.decode()
    assert len(r.json()["user"]["params"]["file_ids"]) == 1


def test_person_two_replies_limit_and_preview_does_not_persist(conversation, db_sessionmaker):
    c, h, tid, user = conversation
    before = None
    with db_sessionmaker() as db:
        before = engine.load_redaction_map(db, uuid.UUID(tid))
    r = c.post(f"{BASE}/{tid}/preview", headers=h, json={"message": "虚构远山科技有限公司"})
    assert r.status_code == 200 and "[[ORG_2]]" in r.json()["redacted"]
    with db_sessionmaker() as db:
        assert engine.load_redaction_map(db, uuid.UUID(tid)) == before
    for _ in range(2):
        session = c.post(BASE, headers=h).json()["id"]
        assert c.post(f"{BASE}/{session}/messages", headers=h, json={"message": "普通问题"}).status_code == 201
    r = send(conversation)
    assert r.status_code == 409 and "两条回复" in r.json()["message"]


def test_new_company_question_is_persisted_as_code_and_restored(conversation, db_sessionmaker, monkeypatch):
    c, _, tid, _ = conversation
    name = "虚构远山科技有限公司"
    requests = install(monkeypatch, ("[[ORG_2]]",))
    r = send(conversation, name + "向" + name + "发函")
    assert r.status_code == 201, r.text
    assert r.json()["user"]["content"].count(name) == 2
    run(db_sessionmaker, tid)
    mid = r.json()["assistant"]["id"]
    assert c.get(f"{BASE}/{tid}/messages/{mid}").json()["text_from_offset"] == name
    assert name not in requests[0].content.decode()
    with db_sessionmaker() as db:
        assert engine.load_redaction_map(db, uuid.UUID(tid))["[[ORG_2]]"] == name
        assert name not in db.get(AssistantMessage, r.json()["user"]["id"]).content_redacted


def test_message_trimmed_length_and_parameter_body_not_persisted(conversation, db_sessionmaker):
    r = send(conversation, "普通问题", params={"message": "不应进入参数的正文"})
    assert r.status_code == 422
    r = send(conversation, " " + "文" * 4000 + " ")
    assert r.status_code == 201, r.text
    assert len(r.json()["user"]["content"]) == 4000


def test_stale_assist_exhaustion_releases_message_slot(conversation, db_sessionmaker):
    from datetime import timedelta
    from app.db import jobs
    mid = send(conversation).json()["assistant"]["id"]
    with db_sessionmaker() as db:
        job = db.scalar(select(Job).where(Job.type == "assist"))
        job.status, job.attempts, job.max_attempts = "running", 1, 1
        job.heartbeat_at = utcnow() - timedelta(minutes=20)
        db.commit()
        assert jobs.requeue_stale(db) == 1
        db.expire_all()
        row = db.get(AssistantMessage, mid)
        assert row.status == "failed" and row.error_code == "ASSIST_INTERRUPTED"
    assert send(conversation).status_code == 201


def test_concurrent_sends_queue_exactly_one_reply(conversation, db_sessionmaker):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    barrier = threading.Barrier(2)

    def submit():
        barrier.wait(5)
        return send(conversation).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [201, 409]
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(AssistantMessage).where(AssistantMessage.role == "user")) == 1
        assert db.scalar(select(func.count()).select_from(AssistantMessage).where(AssistantMessage.role == "assistant")) == 1


def test_delete_during_stream_does_not_restore_outbound_title(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    assert send(conversation).status_code == 201

    class Deleting(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            assert c.delete(f"{BASE}/{tid}", headers=h).status_code == 204
            yield b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n'

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, stream=Deleting())))
    run(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        assert db.get(Task, uuid.UUID(tid)) is None
        call = db.scalar(select(LlmCall))
        assert call is not None and call.task_title is None
