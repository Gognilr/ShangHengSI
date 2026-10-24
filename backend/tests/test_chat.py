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
    assert p == {"redacted": "第4条的违约金上限是否偏低？", "changed": False, "suspects": []}


def test_terms_outside_map_and_formatted_info_get_numbered_codes(db_sessionmaker, alice, ready_task, llm):
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
    # 名称按长度先分配，格式化信息按识别顺序（邮箱先于电话）；预览不写对照表
    assert redacted == ("[公司_Q1]提到[敏感词_Q2]，联系电话 [电话_Q4]，邮箱 [邮箱_Q3]，报价 EUR [金额_Q5]，"
                        "＜document＞ 怎么看？")
    with db_sessionmaker() as db:
        assert not any("_Q" in k for k in load_redaction_map(db, task.id))


def test_chat_codes_restore_reuse_and_survive_rerun(db_sessionmaker, alice, ready_task, llm):
    """Q-97：对照表外的对象分配追问代号并写入对照表：问题和回复都能还原，后续追问沿用，重新审核后仍可还原。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    first = "远方虚构贸易有限公司的联系人 13912345678 说可以，另外，近邻虚构物流有限公司呢？"
    sent: list = []
    llm(handler=chat_reply("[公司_Q1] 与 [公司_Q2] 应分别确认。", sent))
    r = c.post(url(task), json={"message": first}, headers=h)
    assert r.status_code == 200, r.text
    asked = sent[0]["messages"][-1]["content"]
    assert asked == "[公司_Q1]的联系人 [电话_Q3] 说可以，另外，[公司_Q2]呢？"
    assert r.json()["user"]["content"] == first
    assert r.json()["assistant"]["content"] == "远方虚构贸易有限公司 与 近邻虚构物流有限公司 应分别确认。"
    with db_sessionmaker() as db:
        mapping = load_redaction_map(db, task.id)
        stored = [m.content_redacted for m in db.scalars(select(ChatMessage).order_by(ChatMessage.id))]
    assert mapping["[公司_Q1]"] == "远方虚构贸易有限公司" and mapping["[电话_Q3]"] == "13912345678"
    assert all("远方" not in m and "13912345678" not in m for m in stored)

    # 第二次追问：同一对象沿用原代号（即使这次没有企业后缀线索），新对象接着编号
    sent.clear()
    llm(handler=chat_reply("好的", sent))
    c.post(url(task), json={"message": "远方虚构贸易有限公司和 a.b@example.org 是什么关系？"}, headers=h)
    assert sent[0]["messages"][-1]["content"] == "[公司_Q1]和 [邮箱_Q4] 是什么关系？"
    assert REAL_BUYER_NAME not in json.dumps(sent[0], ensure_ascii=False)

    # 重新审核：脱敏整体重算，追问代号保留；新的外发文本经出口复检后仍可确认发送
    assert c.patch(f"{API_PREFIX}/tasks/{task.id}", json={"stance": "purchase"}, headers=h).status_code == 200
    with db_sessionmaker() as db:
        assert load_redaction_map(db, task.id)["[公司_Q2]"] == "近邻虚构物流有限公司"
    items = c.get(url(task)).json()["items"]
    assert items[0]["content"] == first and items[1]["content"].startswith("远方虚构贸易有限公司 与 近邻")
    llm("normal")
    assert c.post(f"{API_PREFIX}/tasks/{task.id}/runs", headers=h).status_code == 201


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
    monkeypatch.setattr(service, "_redact_and_record", lambda db, task, message: message)
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


def test_chat_code_format():
    from app.redaction.placeholders import chat_code, chat_number

    assert chat_code("ORG", 1) == "[公司_Q1]" and chat_code("ADDR", 3, "阿联酋") == "[地址_Q3：阿联酋]"
    assert chat_number("[公司_Q12]") == 12 and chat_number("[地址_Q3：阿联酋]") == 3
    for code in ("客户_A", "[金额_1]", "[账号_2]", "人员_1", "[电话]", "[地址：阿联酋]", "我方"):
        assert chat_number(code) is None  # 解析时分配的代号不会被当成追问代号


# ---------------------------------------------------------------- R4-04（设计 5.2，既有测试仅按 Q-158 修改上面的 preview 相等断言）

R4_NAME = "Meridian Fictive Inspection"
R4_QUESTION = R4_NAME + " 的检验报告能用吗？"


def r4_decision(decision="keep", category=None, start=0):
    out = {"start": start, "end": len(R4_NAME), "decision": decision}
    if category is not None:
        out["category"] = category
    return out


def test_r4_preview_unchanged_but_suspect(db_sessionmaker, alice, ready_task, llm):
    """5.2 #1：未自动识别不等于没有风险，偏移量按去掉首尾空白后的文字。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    preview = c.post(url(task, "/preview"), json={"message": "  " + R4_QUESTION + "  "}, headers=h)
    assert preview.status_code == 200
    assert preview.json() == {"redacted": R4_QUESTION, "changed": False,
                              "suspects": [{"start": 0, "end": len(R4_NAME), "text": R4_NAME, "reason": "capitalized"}]}
    assert message_count(db_sessionmaker) == 0 and chat_calls(db_sessionmaker) == []


@pytest.mark.parametrize("body", [
    {"message": R4_QUESTION},
    {"message": R4_QUESTION, "decisions": []},
    {"message": R4_QUESTION, "decisions": [r4_decision(start=1)]},
])
def test_r4_missing_or_shifted_decision_blocks_send(db_sessionmaker, alice, ready_task, llm, body):
    """5.2 #2、#7：缺决定或区间不完全一致时，不调用模型，不写对话或对照表。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    with db_sessionmaker() as db:
        mapping = load_redaction_map(db, task.id)
    requests = llm("normal")
    response = c.post(url(task), json=body, headers=h)
    assert response.status_code == 409
    assert response.json() == {"code": "CHAT_SUSPECTS_PENDING", "message": "问题中有尚未确认的疑似敏感信息"}
    assert requests == [] and chat_calls(db_sessionmaker) == [] and message_count(db_sessionmaker) == 0
    with db_sessionmaker() as db:
        assert load_redaction_map(db, task.id) == mapping


def test_r4_replace_suspect_and_restore_reply(db_sessionmaker, alice, ready_task, llm):
    """5.2 #3：决定只影响本条追问，不写名称库或任务词条；代号按原协议加密写入对照表。"""
    from app.db.models import RedactionItem
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    with db_sessionmaker() as db:
        terms = list(db.scalars(select(RedactionTerm.id)))
        items = list(db.scalars(select(RedactionItem.id)))
    requests = []
    llm(handler=chat_reply("[公司_Q1] 的报告需要人工核对。", requests))
    response = c.post(url(task), headers=h,
                      json={"message": R4_QUESTION, "decisions": [r4_decision("replace", "ORG")]})
    assert response.status_code == 200, response.text
    assert response.json()["assistant"]["content"] == R4_NAME + " 的报告需要人工核对。"
    assert requests[0]["messages"][-1]["content"] == "[公司_Q1] 的检验报告能用吗？"
    assert R4_NAME not in chat_calls(db_sessionmaker)[0].request_text
    with db_sessionmaker() as db:
        assert list(db.scalars(select(RedactionTerm.id))) == terms
        assert list(db.scalars(select(RedactionItem.id))) == items
        assert load_redaction_map(db, task.id)["[公司_Q1]"] == R4_NAME


def test_r4_keep_suspect_does_not_remember_decision(db_sessionmaker, alice, ready_task, llm):
    """5.2 #4：保留仍走网关与出口复检，下一条同样的问题仍须决定。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    sent = []
    llm(handler=chat_reply("请人工核对。", sent))
    response = c.post(url(task), headers=h, json={"message": R4_QUESTION, "decisions": [r4_decision()]})
    assert response.status_code == 200, response.text
    assert sent[0]["messages"][-1]["content"] == R4_QUESTION
    assert len(chat_calls(db_sessionmaker)) == 1 and chat_calls(db_sessionmaker)[0].status == "sent"
    before = message_count(db_sessionmaker)
    response = c.post(url(task), headers=h, json={"message": R4_QUESTION})
    assert response.status_code == 409 and response.json()["code"] == "CHAT_SUSPECTS_PENDING"
    assert len(sent) == 1 and message_count(db_sessionmaker) == before


def test_r4_replace_needs_category(db_sessionmaker, alice, ready_task, llm):
    """5.2 #5：匹配的替换决定缺少类别时 422，未发送。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    requests = llm("normal")
    response = c.post(url(task), headers=h, json={"message": R4_QUESTION, "decisions": [r4_decision("replace")]})
    assert response.status_code == 422
    assert requests == [] and message_count(db_sessionmaker) == 0


def test_r4_no_suspects_keeps_send_contract(db_sessionmaker, alice, ready_task, llm):
    """5.2 #6：没有疑似遗漏时，不传决定正常发送；不匹配的决定忽略。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    sent = []
    llm(handler=chat_reply("请人工核对。", sent))
    message = "第4条的违约金上限是否偏低？"
    for body in ({"message": message}, {"message": message, "decisions": [r4_decision("replace")]}):
        response = c.post(url(task), headers=h, json=body)
        assert response.status_code == 200, response.text
        assert sent[-1]["messages"][-1]["content"] == message


def test_r4_suspect_decision_logs_are_safe(db_sessionmaker, alice, ready_task, llm, caplog):
    """5.2 #8：预览、缺决定、替换确认、保留确认的 DEBUG 日志均无追问原文。"""
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    replacement_task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    c.post(url(task, "/preview"), headers=h, json={"message": R4_QUESTION})
    c.post(url(task), headers=h, json={"message": R4_QUESTION})
    llm(handler=chat_reply("请人工核对。", []))
    assert c.post(url(task), headers=h, json={"message": R4_QUESTION, "decisions": [r4_decision()]}).status_code == 200
    # 保留后的历史含原文，随后把同一名称加入对照表会被既有出口复检拦截；两种成功分支用独立任务验证。
    assert c.post(url(replacement_task), headers=h, json={"message": R4_QUESTION, "decisions": [r4_decision("replace", "ORG")]}).status_code == 200
    logs = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert R4_QUESTION not in logs and R4_NAME not in logs and "检验报告能用吗" not in logs


@pytest.mark.parametrize("category,code", [("PERSON", "[人员_Q1]"), ("CUSTOM", "[敏感词_Q1]")])
def test_r4_other_replacement_categories(category, code):
    result = service._redact(R4_QUESTION, {}, [], [r4_decision("replace", category)])
    assert result.text == code + " 的检验报告能用吗？"
    assert result.new_entries == {code: R4_NAME}


def test_r4_occupied_spans_and_unicode_offsets():
    prefix = "😀𠮷 "
    message = prefix + R4_QUESTION
    result = service._redact("  " + message + "  ", {}, [])
    assert result.suspects == [{"start": len(prefix), "end": len(prefix) + len(R4_NAME),
                                "text": R4_NAME, "reason": "capitalized"}]
    covered = service._redact(message, {"[公司_Q1]": R4_NAME}, [])
    assert covered.suspects == []


def test_r4_partial_and_duplicate_decisions(monkeypatch):
    from app.security.errors import AppError
    # V2Q-40：仅隔离这个旧 R4 用例的 NER，原漏决定与重复决定断言全部保留。
    # 高置信提升及剩余疑似确认链由 test_ner.py 的真实模型用例另行验证。
    monkeypatch.setattr(service.ner, "recognize", lambda text: [])
    message = R4_QUESTION + " Northern Fictive Laboratory 的报告呢？"
    with pytest.raises(AppError) as missing:
        service._redact(message, {}, [], [r4_decision()])
    assert missing.value.code == "CHAT_SUSPECTS_PENDING"
    with pytest.raises(AppError) as duplicate:
        service._redact(R4_QUESTION, {}, [], [r4_decision(), r4_decision("replace", "ORG")])
    assert duplicate.value.status == 422
    ignored = service._redact(R4_QUESTION, {}, [], [r4_decision(), r4_decision("replace", start=999)])
    assert ignored.text == R4_QUESTION and ignored.new_entries == {}


@pytest.mark.parametrize("decisions", [[r4_decision(), r4_decision()], [r4_decision(), r4_decision("replace", "ORG")]])
def test_r4_duplicate_decisions_do_not_write_or_send(db_sessionmaker, alice, ready_task, llm, decisions):
    user, c, h = alice
    task = reviewed(db_sessionmaker, user, c, h, ready_task, llm)
    requests = llm("normal")
    with db_sessionmaker() as db:
        mapping = load_redaction_map(db, task.id)
    response = c.post(url(task), headers=h, json={"message": R4_QUESTION, "decisions": decisions})
    assert response.status_code == 422
    assert requests == [] and chat_calls(db_sessionmaker) == [] and message_count(db_sessionmaker) == 0
    with db_sessionmaker() as db:
        assert load_redaction_map(db, task.id) == mapping
