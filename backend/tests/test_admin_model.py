"""V3-B-02：模型管理代表用例，传输层全部模拟。"""
import json
import logging

import httpx
import pytest
from sqlalchemy import select

from app.db.models import AuditLog, LlmCall, LlmModel
from app.llm import gateway
from app.main import API_PREFIX
from app.security import crypto
from tests.review_support import env

BASE = API_PREFIX + "/admin/models"
KEY = "sk-fictional-01234567"


def body(**over):
    data = {"display_name": "第二模型", "model_name": "second", "base_url": "https://second.example.test/v1",
            "api_key": KEY, "for_review": True, "for_assist": True}
    return {**data, **over}


@pytest.fixture
def admin(env, make_user, login):
    make_user("models_admin", role="sys_admin")
    client, csrf = login("models_admin")
    return client, {"X-CSRF-Token": csrf}


def transport(monkeypatch, structured=True):
    requests = []
    def send(request):
        requests.append(request)
        text = json.loads(request.content)["messages"][0]["content"]
        content = json.dumps({"ok": True, "items": [{"n": 1}, {"n": 2}]}) if structured and text == gateway.STRUCTURED_MESSAGE else "OK"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(send))
    return requests


def test_user_list_hides_connections_and_admin_forbidden(env, make_user, login):
    make_user("model_reader")
    client, csrf = login("model_reader")
    data = client.get(API_PREFIX + "/models?purpose=review").json()
    assert len(data["items"]) == 1
    assert set(data["items"][0]) == {"id", "display_name", "description", "is_default", "structured_ok"}
    assert client.get(BASE).status_code == 403
    assert client.post(BASE, json=body(), headers={"X-CSRF-Token": csrf}).status_code == 403


def test_structured_failure_does_not_save_create_or_update(admin, db_sessionmaker, monkeypatch):
    client, headers = admin
    transport(monkeypatch, structured=False)
    response = client.post(BASE, json=body(), headers=headers)
    assert response.status_code == 422 and response.json()["code"] == "STRUCTURED_REQUIRED"
    assert response.json()["tests"]["structured"]["ok"] is False
    with db_sessionmaker() as db:
        assert len(db.scalars(select(LlmModel)).all()) == 1
    response = client.put(BASE + "/1", json=body(display_name="修改", model_name="changed"), headers=headers)
    assert response.status_code == 422 and response.json()["code"] == "STRUCTURED_REQUIRED"
    with db_sessionmaker() as db:
        model = db.get(LlmModel, 1)
        assert model.display_name == "模拟模型" and model.model_name == "mock-model" and model.structured_ok


def test_save_metadata_key_and_explicit_tests(admin, db_sessionmaker, monkeypatch, caplog):
    client, headers = admin
    requests = transport(monkeypatch)
    with caplog.at_level(logging.INFO):
        response = client.post(BASE, json=body(), headers=headers)
    assert response.status_code == 201, response.text
    item = response.json()
    mid = item["id"]
    assert item["api_key_last4"] == KEY[-4:] and item["structured_ok"] and len(requests) == 2
    assert KEY not in response.text and KEY not in caplog.text
    response = client.put(BASE + f"/{mid}", json=body(display_name="改名", api_key=""), headers=headers)
    assert response.status_code == 200 and len(requests) == 2
    assert response.json()["tests"] is None
    with db_sessionmaker() as db:
        assert crypto.decrypt_text(db.get(LlmModel, mid).api_key_enc) == KEY
        assert KEY not in str([row.detail for row in db.scalars(select(AuditLog))])
    response = client.post(BASE + f"/{mid}/test", headers=headers)
    assert response.status_code == 200 and response.json()["structured_ok"] and len(requests) == 4
    response = client.get(BASE)
    assert KEY not in response.text and "api_key_enc" not in response.text
    assert client.get(API_PREFIX + "/admin/usage").json()["calls"] == 4
    assert client.delete(BASE + f"/{mid}", headers=headers).status_code == 204  # 只做过测试仍可删除


def test_delete_used_default_and_disable_guards(admin, db_sessionmaker):
    client, headers = admin
    with db_sessionmaker() as db:
        db.add(LlmCall(model_id=1, purpose="review", model_name="mock-model", status="sent"))
        db.commit()
    assert client.delete(BASE + "/1", headers=headers).json()["code"] == "MODEL_IN_USE"
    assert client.post(BASE + "/1/enabled", json={"enabled": False}, headers=headers).json()["code"] == "DEFAULT_IN_USE"
    response = client.put(API_PREFIX + "/admin/settings/user-daily-cap", json={"value": 7}, headers=headers)
    assert response.status_code == 200 and client.get(BASE).json()["user_daily_cap"] == 7


def test_overview_groups_by_shanghai_day_counts_blocks_and_hides_content(admin, db_sessionmaker, env, make_user, login):
    """V36-F-02：出口地图聚合按模型与上海自然日分组，拦截单独计数，不带任务名、用户名或内容。"""
    from datetime import timedelta
    from decimal import Decimal
    from app.llm.usage import month_start
    client, _ = admin
    start = month_start()
    def call(at, status="sent", purpose="review", cost="0.50", ms=800):
        return LlmCall(model_id=1, purpose=purpose, model_name="mock-model", status=status, task_title="秘密合同",
                       request_text="正文" if status != "blocked" else None, cost=Decimal(cost) if cost else None,
                       duration_ms=ms, created_at=at)
    with db_sessionmaker() as db:
        db.add_all([call(start + timedelta(minutes=30)),               # 上海 1 日 00:30，UTC 仍是上月最后一天
                    call(start + timedelta(minutes=40), purpose="assist", cost=None, ms=1200),
                    call(start + timedelta(hours=30), status="blocked", cost=None, ms=None),
                    call(start + timedelta(hours=30), status="error", cost=None, ms=None),
                    call(start - timedelta(minutes=1))])                # 上月，不计
        db.commit()
    resp = client.get(BASE + "/overview")
    assert resp.status_code == 200
    data = resp.json()
    m = next(x for x in data["models"] if x["id"] == 1)
    assert (m["calls"], m["failed"], m["blocked"], m["unpriced"], m["cost"]) == (2, 1, 1, 1, "0.50")
    assert m["median_ms"] == 1000 and m["purposes"] == {"review": 1, "assist": 1}
    assert [(d["day"], d["calls"], d["blocked"]) for d in m["daily"]] == [(1, 2, 0), (2, 0, 1)]
    assert data["total"]["blocked"] == 1 and len(data["local"]) == 4
    assert "秘密合同" not in resp.text and "正文" not in resp.text and "user" not in json.dumps(data["models"])
    make_user("overview_reader")
    reader, _ = login("overview_reader")
    assert reader.get(BASE + "/overview").status_code == 403
