"""V3-B-02：审核停用、执行时停用、追问回退及助手模型记忆。"""
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.admin.settings_store import set_setting
from app.db.models import AssistantMessage, LlmCall, LlmModel, ReviewRun, Task
from app.llm import gateway
from app.main import API_PREFIX
from app.review import job, service
from app.security import crypto
from tests.review_support import env, ready_task, llm
from tests.test_gateway_stream import Events


@pytest.fixture
def second(env, db_sessionmaker):
    with db_sessionmaker() as db:
        row = LlmModel(display_name="备用模型", model_name="backup", base_url="https://backup.example.test/v1",
                       api_key_enc=crypto.encrypt_text("sk-backup-fictional"), for_review=True, for_assist=True, structured_ok=True)
        db.add(row)
        db.commit()
    return row


def test_review_disabled_submit_and_queued_execution(env, second, make_user, login, ready_task, db_sessionmaker):
    owner = make_user("selected_review")
    client, csrf = login(owner.username)
    task = ready_task(owner)
    with db_sessionmaker() as db:
        db.get(LlmModel, second.id).enabled = False
        db.commit()
    response = client.post(f"{API_PREFIX}/tasks/{task.id}/runs", json={"model_id": second.id}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422 and response.json()["code"] == "MODEL_NOT_ALLOWED"
    with db_sessionmaker() as db:
        db.get(LlmModel, second.id).enabled = True
        db.commit()
        run = service.create_run(db, db.get(Task, task.id), owner, None, model_id=second.id)
        assert run.model_id == second.id and run.model_label == "备用模型"
        db.get(LlmModel, second.id).enabled = False
        db.commit()
        job.run_review(db, run.id)
        db.refresh(run)
        assert run.status == "failed" and run.error_message == "所选模型已停用，请重新审核并换一个模型"
        assert list(db.scalars(select(LlmCall))) == []


def test_chat_falls_back_to_review_default(env, second, make_user, ready_task, llm, db_sessionmaker):
    from app.chat.service import send
    owner = make_user("selected_chat")
    task = ready_task(owner)
    requests = llm("normal")
    with db_sessionmaker() as db:
        run = service.create_run(db, db.get(Task, task.id), owner, None, model_id=second.id)
        job.run_review(db, run.id)
        db.get(LlmModel, second.id).enabled = False
        db.commit()
        send(db, db.get(Task, task.id), owner, "请说明审核结论")
        calls = list(db.scalars(select(LlmCall).order_by(LlmCall.id)))
        assert [call.model_id for call in calls] == [second.id, 1]
        assert calls[-1].purpose == "chat"
    assert json.loads(requests[-1].content)["model"] == "mock-model"


def test_assist_choice_last_reply_retry_and_compare(env, second, make_user, login, db_sessionmaker, monkeypatch):
    from app.assistant.job import handle_assist
    owner = make_user("selected_assist")
    client, csrf = login(owner.username)
    headers = {"X-CSRF-Token": csrf}
    base = API_PREFIX + "/assistant/sessions"
    tid = client.post(base, headers=headers).json()["id"]
    url = base + f"/{tid}/messages"
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(200, stream=Events(["普通回复"]))
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(transport))
    response = client.post(url, json={"message": "说明术语", "model_id": second.id}, headers=headers)
    assert response.status_code == 201, response.text
    reply = response.json()["assistant"]
    assert reply["model_id"] == second.id and reply["model_label"] == "备用模型"
    with db_sessionmaker() as db:
        handle_assist(db, {"task_id": tid, "message_id": reply["id"]})
        assert db.get(AssistantMessage, reply["id"]).status == "done"
        assert db.scalar(select(LlmCall)).model_id == second.id
    response = client.post(url, json={"message": "继续"}, headers=headers)
    assert response.json()["assistant"]["model_id"] == second.id
    mid = response.json()["assistant"]["id"]
    with db_sessionmaker() as db:
        db.get(AssistantMessage, mid).status = "failed"
        set_setting(db, "model.default_assist_id", 1, None)
        db.commit()
    response = client.post(url + f"/{mid}/retry", headers=headers)
    assert response.status_code == 201 and response.json()["assistant"]["model_id"] == second.id
    with db_sessionmaker() as db:
        db.get(AssistantMessage, response.json()["assistant"]["id"]).status = "done"
        db.get(LlmModel, second.id).structured_ok = False
        db.commit()
    response = client.post(url, json={"message": "比价", "model_id": second.id, "template": "compare"}, headers=headers)
    assert response.status_code == 422 and response.json()["code"] == "MODEL_NOT_ALLOWED"
