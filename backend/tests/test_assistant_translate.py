"""T-50：翻译分段、完整节点位置对齐与按书写系统还原。"""
import pytest
import json
import re
import uuid
import httpx
from sqlalchemy import delete, func, select
from app.db.models import AssistantMessage, DocNode, Job, LlmCall, TaskFile
from app.redaction import engine
from app.security import crypto
from app.llm import gateway
from tests.review_support import env  # noqa: F401
from tests.test_assistant_chat import BASE, conversation, run  # noqa: F401
from tests.test_gateway_stream import Events


def test_t50_2_segment_9500_characters():
    from app.assistant.translate import segments
    nodes = [(f"F1-P{i}", "文" * 500) for i in range(1, 20)]
    result = segments(nodes, 4000)
    assert len(result) == 3
    assert [pair for part in result for pair in part] == nodes
    assert all(len("\n".join(f"⟦{loc}⟧ {text}" for loc, text in part)) <= 4000 for part in result)


def test_oversize_node_stays_whole():
    from app.assistant.translate import segments
    assert segments([("F1-P1", "文" * 5000), ("F1-P2", "短文")], 4000) == [
        [("F1-P1", "文" * 5000)], [("F1-P2", "短文")]]


def test_t50_5_alignment_and_whole_segment_fallback():
    from app.assistant.translate import align
    nodes = [("F1-P1", "一"), ("F1-P2", "二")]
    assert align("⟦F1-P1⟧ One\n⟦F1-P2⟧ Two", nodes) == ({"F1-P1": "One", "F1-P2": "Two"}, False)
    missing = "⟦F1-P1⟧ One\nTwo"
    assert align(missing, nodes) == ({"F1-P1": missing}, True)
    unknown = "⟦F1-P1⟧ One\n⟦F9-P9⟧ Two"
    assert align(unknown, nodes) == ({"F1-P1": unknown}, True)


def test_t50_6_target_script_mapping():
    from app.assistant.translate import target_mapping
    mapping = {"[[ORG_1]]": "星河公司", "[[ORG_1]]#1": "Xinghe Co.", "[[AMT_1]]": "12,345.00"}
    assert target_mapping(mapping, "英文") == {"[[ORG_1]]": "Xinghe Co.", "[[AMT_1]]": "12,345.00"}
    assert target_mapping(mapping, "日文")["[[ORG_1]]"] == "星河公司"


@pytest.mark.parametrize("target,alias", [("日文", "ほし会社"), ("韩文", "별빛회사"), ("俄文", "Звезда"), ("阿拉伯文", "شركة النجوم")])
def test_script_specific_alternates(target, alias):
    from app.assistant.translate import target_mapping
    assert target_mapping({"[[ORG_1]]": "星河公司", "[[ORG_1]]#1": alias}, target)["[[ORG_1]]"] == alias


@pytest.mark.parametrize("remaining", [{"monthly": 10, "daily": 2}, {"monthly": 2, "daily": None}])
def test_t50_3_quota_uses_smaller_remaining_limit(remaining):
    from app.assistant.translate import require_quota
    from app.security.errors import AppError
    with pytest.raises(AppError) as caught:
        require_quota(3, remaining)
    assert caught.value.status == 409 and caught.value.code == "ASSIST_QUOTA"
    assert caught.value.message == "翻译需要调用 3 次，今日剩余 2 次"


def test_unlimited_or_sufficient_quota():
    from app.assistant.translate import require_quota
    require_quota(3, {"monthly": None, "daily": None})
    require_quota(3, {"monthly": 3, "daily": 8})


def translation_input(sm, tid):
    with sm() as db:
        file = db.scalar(select(TaskFile).where(TaskFile.task_id == uuid.UUID(tid)))
        db.execute(delete(DocNode).where(DocNode.task_id == uuid.UUID(tid)))
        for i in range(1, 20):
            db.add(DocNode(task_id=uuid.UUID(tid), file_id=file.id, loc=f"F1-P{i}", ord=i, kind="paragraph", part="body",
                           text_enc=crypto.encrypt_text("文" * 500), redacted_text="文" * 500))
        db.add(AssistantMessage(task_id=uuid.UUID(tid), role="user", status="done", content_redacted="HISTORY_SENTINEL"))
        db.commit()
        return {"template": "translate", "params": {"file_id": str(file.id), "target": "英文"}}


def translation_transport(monkeypatch, fail_at=None, omit_at=None):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == fail_at:
            raise httpx.ReadTimeout("替身第二段超时")
        data = json.loads(request.content)
        from app.assistant.prompt import system_prompt
        from app.assistant.templates import instruction
        assert data["messages"][0]["content"] == system_prompt() + "\n\n" + instruction("translate")
        content = "\n".join(m["content"] for m in data["messages"])
        locs = re.findall(r"(?m)^⟦([^⟧]+)⟧", content)
        if len(requests) == omit_at:
            locs = locs[:-1]
        output = "\n".join(f"⟦{loc}⟧ translated [[ORG_1]]" for loc in locs)
        return httpx.Response(200, stream=Events([output]))

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    return requests


def test_t50_2_5_three_real_stream_calls_without_history(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    body = translation_input(db_sessionmaker, tid)
    requests = translation_transport(monkeypatch)
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 201, response.text
    mid = response.json()["assistant"]["id"]
    run(db_sessionmaker, tid)
    assert len(requests) == 3
    assert all("HISTORY_SENTINEL" not in req.content.decode() for req in requests)
    with db_sessionmaker() as db:
        reply = db.get(AssistantMessage, mid)
        assert reply.status == "done" and reply.extras["segments_total"] == reply.extras["segments_done"] == 3
        assert len(reply.extras["translation"]) == 19 and reply.extras["translation_unaligned"] == []
        assert reply.tokens_in == 36 and reply.tokens_out == 21
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 3


def test_t50_3_quota_rejected_before_queue(conversation, db_sessionmaker, monkeypatch):
    from app.admin.settings_store import set_setting
    c, h, tid, _ = conversation
    body = translation_input(db_sessionmaker, tid)
    requests = translation_transport(monkeypatch)
    with db_sessionmaker() as db:
        set_setting(db, "model.user_daily_cap", 2, None)
        before = db.scalar(select(func.count()).select_from(AssistantMessage))
        db.commit()
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 409 and response.json()["code"] == "ASSIST_QUOTA", response.text
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(AssistantMessage)) == before
        assert db.scalar(select(func.count()).select_from(Job).where(Job.type == "assist")) == 0
    assert requests == []


def test_t50_4_second_segment_failure_preserves_first(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    requests = translation_transport(monkeypatch, fail_at=2)
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=translation_input(db_sessionmaker, tid))
    assert response.status_code == 201
    mid = response.json()["assistant"]["id"]
    run(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        reply = db.get(AssistantMessage, mid)
        assert (reply.status, reply.error_code, reply.extras["segments_done"]) == ("failed", "TIMEOUT", 1)
        assert "translated" in reply.content_redacted and reply.extras["translation"]
        assert reply.tokens_in == 12
    assert len(requests) == 2


def test_t50_5_6_unaligned_and_language_restore_in_api(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    body = translation_input(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        mapping = engine._load_full_map(db, uuid.UUID(tid))
        mapping["[[ORG_1]]#1"] = "Xinghe Co."
        engine.write_full_map(db, uuid.UUID(tid), mapping)
        db.commit()
    translation_transport(monkeypatch, omit_at=1)
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 201
    mid = response.json()["assistant"]["id"]
    run(db_sessionmaker, tid)
    delta = c.get(f"{BASE}/{tid}/messages/{mid}").json()
    assert "Xinghe Co." in delta["text_from_offset"]
    messages = c.get(f"{BASE}/{tid}/messages").json()["items"]
    reply = next(m for m in messages if m["id"] == mid)
    assert reply["extras"]["translation_unaligned"] == [1]
    assert "Xinghe Co." in "".join(reply["translation"].values())
    assert reply["translation_notice"] == "名称、地址、金额按原文写法还原，请核对"
    with db_sessionmaker() as db:
        stored = db.get(AssistantMessage, mid)
        assert "Xinghe Co." not in stored.content_redacted + json.dumps(stored.extras)


def test_translation_ignores_single_call_limit_and_honors_configured_segment_size(conversation, db_sessionmaker, monkeypatch):
    from app.admin.settings_store import set_setting
    from app.assistant.translate import task_segments
    from app.db.models import Task
    c, h, tid, _ = conversation
    body = translation_input(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        set_setting(db, "model.chunk_chars", 100, None)
        set_setting(db, "assistant.translate_segment_chars", 2000, None)
        expected = len(task_segments(db, db.get(Task, uuid.UUID(tid)), {"file_ids": [body["params"]["file_id"]]}))
        db.commit()
    requests = translation_transport(monkeypatch)
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 201, response.text
    run(db_sessionmaker, tid)
    assert len(requests) == expected and expected > 3


def test_translation_quota_changed_after_enqueue(conversation, db_sessionmaker, monkeypatch):
    from app.admin.settings_store import set_setting
    c, h, tid, _ = conversation
    body = translation_input(db_sessionmaker, tid)
    requests = translation_transport(monkeypatch)
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 201
    mid = response.json()["assistant"]["id"]
    with db_sessionmaker() as db:
        set_setting(db, "model.user_daily_cap", 2, None)
        db.commit()
    run(db_sessionmaker, tid)
    assert requests == []
    with db_sessionmaker() as db:
        assert db.get(AssistantMessage, mid).error_code == "ASSIST_QUOTA"
    delta = c.get(f"{BASE}/{tid}/messages/{mid}").json()
    assert delta["status"] == "failed" and "次数" in delta["error"]
