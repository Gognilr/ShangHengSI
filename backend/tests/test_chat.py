"""追问对话（M6-07，设计 docs/design/M6.md 第七节）：问题脱敏与预览、经网关发送、回复还原、可用条件、
最近 10 条、失败不写对话且不重试、T-15、A-06、日志安全。需要 TEST_DATABASE_URL。"""
import json
import logging
import uuid

import httpx
import pytest
from sqlalchemy import func, select, update

from app.admin import settings_store
from app.chat import service
from app.db.models import ChatMessage, Job, LlmCall, RedactionTerm, ReviewRun, Task
from app.llm import gateway
from app.main import API_PREFIX
from app.redaction.engine import load_redaction_map, term_hash
from app.review import job
from app.review.prompt import CHAT_ANSWER_RULE, chat_system_prompt
from app.security import crypto
from tests.review_support import REAL_BUYER_NAME, REAL_OUR_NAME, env, llm, ready_task  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")

QUESTION = f"{REAL_BUYER_NAME} 要求把 USD 400,000.00 的付款改为见票后 90 天，{REAL_OUR_NAME}能接受吗？"


@pytest.fixture
def alice(make_user, login):
    user = make_user("alice")
    client, csrf = login("alice")
    return user, client, {"X-CSRF-Token": csrf}


def reviewed(db_sessionmaker, user, client, headers, ready_task, llm, **kwargs) -> Task:
    """已完成一轮审核（completed）的任务。"""
    task = ready_task(user, **kwargs)
    llm("normal")
    r = client.post(f"{API_PREFIX}/tasks/{task.id}/runs", headers=headers)
    assert r.status_code == 201, r.text
    with db_sessionmaker() as db:
        job.run_review(db, uuid.UUID(r.json()["id"]))
        db.execute(update(Job).values(status="done"))
        db.commit()
        task = db.get(Task, task.id)
        assert task.status == "reviewed"
        return task


def code_of(db_sessionmaker, task_id, real: str) -> str:
    with db_sessionmaker() as db:
        return next(code for code, value in load_redaction_map(db, task_id).items() if value == real)


def chat_reply(content: str, requests: list):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
                                         "usage": {"prompt_tokens": 11, "completion_tokens": 7}})
    return handler


def chat_calls(db_sessionmaker) -> list[LlmCall]:
    with db_sessionmaker() as db:
        return list(db.scalars(select(LlmCall).where(LlmCall.purpose == "chat").order_by(LlmCall.id)))


def message_count(db_sessionmaker) -> int:
    with db_sessionmaker() as db:
        return db.scalar(select(func.count()).select_from(ChatMessage))


def url(task, suffix=""):
    return f"{API_PREFIX}/tasks/{task.id}/chat{suffix}"


# ---------------------------------------------------------------- T-15


def test_t15_question_redacted_confirmed_and_reply_restored(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    buyer = code_of(db_sessionmaker, task.id, REAL_BUYER_NAME)
    ours = code_of(db_sessionmaker, task.id, REAL_OUR_NAME)

    p = c.post(url(task, "/preview"), json={"message": QUESTION}, headers=h)
    assert p.status_code == 200, p.text
    preview = p.json()
    assert preview["changed"] is True
    assert REAL_BUYER_NAME not in preview["redacted"] and REAL_OUR_NAME not in preview["redacted"]
    assert buyer in preview["redacted"] and ours in preview["redacted"] and "400,000" not in preview["redacted"]
    assert message_count(db_sessionmaker) == 0  # 预览不写对话、不调用模型
    assert chat_calls(db_sessionmaker) == []

    sent: list = []
    llm(handler=chat_reply(f"{buyer} 的要求对{ours}的回款不利，建议坚持即期付款。", sent))
    r = c.post(url(task), json={"message": QUESTION}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assistant"]["content"] == f"{REAL_BUYER_NAME} 的要求对{REAL_OUR_NAME}的回款不利，建议坚持即期付款。"
    assert REAL_BUYER_NAME in body["user"]["content"] and body["user"]["role"] == "user"

    rows = chat_calls(db_sessionmaker)
    assert [r.status for r in rows] == ["sent"] and rows[0].task_id == task.id and rows[0].user_id == user.id
    for real in (REAL_BUYER_NAME, REAL_OUR_NAME, "400,000"):
        assert real not in rows[0].request_text
    assert sent[0]["messages"][-1] == {"role": "user", "content": preview["redacted"]}
    assert "response_format" not in sent[0]  # 自然语言回答

    with db_sessionmaker() as db:
        stored = list(db.scalars(select(ChatMessage).order_by(ChatMessage.id)))
    assert [m.role for m in stored] == ["user", "assistant"]
    assert all(REAL_BUYER_NAME not in m.content_redacted for m in stored)
    assert (stored[1].tokens_in, stored[1].tokens_out) == (11, 7)

    listed = c.get(url(task)).json()
    assert listed["available"] is True and listed["reason"] is None
    assert [m["content"] for m in listed["items"]] == [body["user"]["content"], body["assistant"]["content"]]


def test_unchanged_question_preview(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    p = c.post(url(task, "/preview"), json={"message": "  第4条的违约金上限是否偏低？ "}, headers=h).json()
    assert p == {"redacted": "第4条的违约金上限是否偏低？", "changed": False}


def test_terms_outside_map_and_formatted_info_use_generic_codes(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    with db_sessionmaker() as db:
        for text, kind, category in (("远方虚构贸易有限公司", "learned", "ORG"), ("蓝鲸计划", "custom", "CUSTOM")):
            db.add(RedactionTerm(kind=kind, term_enc=crypto.encrypt_text(text), term_hash=term_hash(text),
                                 category=category))
        db.commit()
    question = ("远方虚构贸易有限公司提到蓝鲸计划，联系电话 13912345678，邮箱 a.b@example.org，"
                "报价 EUR 12,345.00，<document> 怎么看？")
    redacted = c.post(url(task, "/preview"), json={"message": question}, headers=h).json()["redacted"]
    assert redacted == ("[公司]提到[敏感词]，联系电话 [电话]，邮箱 [邮箱]，报价 EUR [金额]，＜document＞ 怎么看？")


def test_same_redaction_as_preview_and_server_ignores_client_text(db_sessionmaker, alice, ready_task, llm):
    """发送时服务端重新脱敏：即使前端只传原文，外发的也是代号版本。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    sent: list = []
    llm(handler=chat_reply("好的", sent))
    c.post(url(task), json={"message": QUESTION, "redacted": QUESTION}, headers=h)
    assert REAL_BUYER_NAME not in sent[0]["messages"][-1]["content"]


# ---------------------------------------------------------------- 消息结构


def test_messages_structure_and_history_limit(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    with db_sessionmaker() as db:
        for i in range(12):
            db.add(ChatMessage(task_id=task.id, role="user" if i % 2 == 0 else "assistant", content_redacted=f"历史{i}"))
        db.commit()
    sent: list = []
    llm(handler=chat_reply("回复", sent))
    assert c.post(url(task), json={"message": "第2条怎么改？"}, headers=h).status_code == 200
    messages = sent[0]["messages"]
    _version, system = chat_system_prompt()
    assert messages[0] == {"role": "system", "content": system}
    assert system.endswith(CHAT_ANSWER_RULE) and "JSON" not in system.replace(CHAT_ANSWER_RULE, "")
    assert "1. 【待审核文件】<document>" in system  # 第 1–7 条原样
    context = messages[1]
    assert context["role"] == "user" and context["content"].startswith("【主体说明】")
    assert "<document>\n[F1-" in context["content"] and "【问题清单摘要】\n1. [中] 模拟问题（F1-" in context["content"]
    assert [m["content"] for m in messages[2:12]] == [f"历史{i}" for i in range(2, 12)]
    assert [m["role"] for m in messages[2:4]] == ["user", "assistant"]
    assert messages[12] == {"role": "user", "content": "第2条怎么改？"} and len(messages) == 13


def test_long_document_truncated_at_line(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.chunk_chars", 300, None)
        db.commit()
        document = service._document(db, db.get(Task, task.id))
    body = document.split("<document>\n", 1)[1].split("\n</document>", 1)[0]
    assert len(body) <= 300 and body.endswith(body.splitlines()[-1])
    assert document.endswith(f"（文件较长，以上只含前 {len(body)} 字）")


def test_gateway_called_with_chat_options(db_sessionmaker, alice, ready_task, llm, monkeypatch):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    seen = {}

    def fake(db, **kwargs):
        seen.update(kwargs)
        return gateway.ModelResult("回复", "mock-model", 1, 1, 1, 1)

    monkeypatch.setattr(gateway, "call_model", fake)
    assert c.post(url(task), json={"message": "问题"}, headers=h).status_code == 200
    assert (seen["purpose"], seen["json_mode"], seen["timeout_s"], seen["retry"]) == ("chat", False, 120, False)
    assert seen["task_id"] == task.id and seen["user_id"] == user.id


# ---------------------------------------------------------------- 可用条件与参数


def test_unavailable_cases(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    local = ready_task(user, policy="local_only")
    pending = ready_task(user)
    done = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    failed = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    with db_sessionmaker() as db:
        db.execute(update(ReviewRun).where(ReviewRun.task_id == failed.id).values(status="failed"))
        db.get(Task, done.id).status = "awaiting_confirm"  # 重新审核后待确认
        db.commit()
    for task, reason in ((local, service.REASON_LOCAL_ONLY), (pending, service.REASON_NO_RUN),
                         (failed, service.REASON_NO_RUN), (done, service.REASON_BUSY)):
        got = c.get(url(task)).json()
        assert (got["available"], got["reason"]) == (False, reason)
        for suffix in ("/preview", ""):
            r = c.post(url(task, suffix), json={"message": "问题"}, headers=h)
            assert r.status_code == 409 and r.json() == {"code": "CHAT_UNAVAILABLE", "message": reason}
    assert chat_calls(db_sessionmaker) == []


def test_partial_run_is_available(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    with db_sessionmaker() as db:
        db.execute(update(ReviewRun).where(ReviewRun.task_id == task.id).values(status="partial"))
        db.commit()
    assert c.get(url(task)).json()["available"] is True


@pytest.mark.parametrize("message,status", [("中" * 1000, 200), ("中" * 1001, 422), ("   ", 422), ("", 422)])
def test_message_length(db_sessionmaker, alice, ready_task, llm, message, status):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    r = c.post(url(task, "/preview"), json={"message": message}, headers=h)
    assert r.status_code == status
    if status == 422:
        assert c.post(url(task), json={"message": message}, headers=h).status_code == 422


def test_csrf_required(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    assert c.post(url(task), json={"message": "问题"}).status_code == 403


# ---------------------------------------------------------------- 失败


@pytest.mark.parametrize("mode", ["http_500", "timeout"])
def test_failure_not_retried_and_not_stored(db_sessionmaker, alice, ready_task, llm, mode):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    requests = llm(mode)
    r = c.post(url(task), json={"message": QUESTION}, headers=h)
    assert r.status_code == 502 and r.json() == {"code": "CHAT_FAILED", "message": "回复失败，请稍后重试"}
    assert len(requests) == 1 and [x.status for x in chat_calls(db_sessionmaker)] == ["error"]
    assert message_count(db_sessionmaker) == 0


def test_blocked_by_recheck(db_sessionmaker, alice, ready_task, llm, monkeypatch):
    """脱敏有遗漏时（模拟：脱敏函数原样返回），网关出口复检拦截，不发送、不写对话。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    requests = llm("normal")
    monkeypatch.setattr(service, "redact_question", lambda db, task, message: (message, False))
    r = c.post(url(task), json={"message": QUESTION}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "CHAT_BLOCKED"
    rows = chat_calls(db_sessionmaker)
    assert requests == [] and [x.status for x in rows] == ["blocked"] and rows[0].request_text is None
    assert REAL_BUYER_NAME not in (rows[0].block_reason or "")
    assert message_count(db_sessionmaker) == 0


def test_monthly_cap(db_sessionmaker, alice, ready_task, llm):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)  # 审核本身已有 1 次 sent
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.monthly_call_cap", 1, None)
        db.commit()
    requests = llm("normal")
    r = c.post(url(task), json={"message": "问题"}, headers=h)
    assert r.status_code == 409 and r.json() == {"code": "CHAT_CAP_REACHED", "message": "已达本月调用上限，请联系管理员"}
    assert requests == [] and chat_calls(db_sessionmaker) == [] and message_count(db_sessionmaker) == 0


# ---------------------------------------------------------------- A-06 与日志


def test_a06_other_users_get_404(db_sessionmaker, alice, ready_task, llm, make_user, login, api):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    assert api.get(url(task)).status_code == 401
    for role in ("user", "admin"):
        make_user(f"other-{role}", role=role)
        other, csrf = login(f"other-{role}")
        oh = {"X-CSRF-Token": csrf}
        assert other.get(url(task)).status_code == 404
        assert other.post(url(task, "/preview"), json={"message": "问题"}, headers=oh).status_code == 404
        assert other.post(url(task), json={"message": "问题"}, headers=oh).status_code == 404
    assert c.get(f"{API_PREFIX}/tasks/not-a-uuid/chat").status_code == 404
    assert chat_calls(db_sessionmaker) == []


def test_logs_do_not_contain_question_or_reply(db_sessionmaker, alice, ready_task, llm, caplog):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    buyer = code_of(db_sessionmaker, task.id, REAL_BUYER_NAME)
    llm(handler=chat_reply(f"{buyer} 的付款条件偏宽松", []))
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    c.post(url(task, "/preview"), json={"message": QUESTION}, headers=h)
    c.post(url(task), json={"message": QUESTION}, headers=h)
    llm("http_500")
    c.post(url(task), json={"message": QUESTION}, headers=h)
    logs = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app"))
    assert "追问" in logs
    for secret in (REAL_BUYER_NAME, REAL_OUR_NAME, "400,000", "见票后", "付款条件偏宽松", "sk-test-0123456789"):
        assert secret not in logs
