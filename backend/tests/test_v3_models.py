"""V3-B-01：多模型迁移、网关、独立月限额与合计日限额。"""
import json
import os
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import LlmCall
from app.llm import gateway, usage
from app.security import crypto
from app.admin.settings_store import set_setting


@pytest.fixture
def models(tmp_path, monkeypatch, db_sessionmaker):
    from app.db.models import LlmModel
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    monkeypatch.setattr(gateway, "RETRY_DELAYS", ())
    with db_sessionmaker() as db:
        rows = [LlmModel(display_name=name, base_url=f"https://{name.lower()}.example.test/v1",
                        api_key_enc=crypto.encrypt_text("secret-"+name), model_name="model-"+name,
                        for_review=True, for_assist=True, monthly_call_cap=1,
                        price_in_per_m=Decimal("2" if name == "A" else "4"), price_out_per_m=Decimal("8")) for name in ("A", "B")]
        db.add_all(rows)
        db.commit()
    yield rows
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


def install(monkeypatch, content="OK"):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                        "usage": {"prompt_tokens": 1000000, "completion_tokens": 125000}})
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    return requests


def test_selected_model_and_recheck(models, db_sessionmaker, monkeypatch):
    requests = install(monkeypatch)
    with db_sessionmaker() as db:
        for model in models:
            gateway.call_model(db, model_id=model.id, purpose="review", messages=[{"role": "user", "content": "普通问题"}])
        rows = list(db.scalars(select(LlmCall).order_by(LlmCall.id)))
        assert [row.model_id for row in rows] == [m.id for m in models]
        models[1] = db.get(type(models[1]), models[1].id)
        models[1].monthly_call_cap = 0
        db.commit()
        with pytest.raises(gateway.GatewayError, match="出口复检"):
            gateway.call_model(db, model_id=models[1].id, purpose="review", messages=[{"role": "user", "content": "电话 13800138000"}])
        blocked = db.scalars(select(LlmCall).order_by(LlmCall.id)).all()[-1]
        assert blocked.model_id == models[1].id and blocked.request_text is None
    assert len(requests) == 2
    for request, model in zip(requests, models):
        assert str(request.url) == model.base_url+"/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret-"+model.display_name
        assert json.loads(request.content)["model"] == model.model_name


def test_model_monthly_cap_and_shared_daily_cap(models, db_sessionmaker, make_user, monkeypatch):
    requests = install(monkeypatch)
    user = make_user("multi_cap")
    with db_sessionmaker() as db:
        set_setting(db, "model.user_daily_cap", 2, None)
        db.commit()
        def send(model):
            return gateway.call_model(db, model_id=model.id, purpose="review", user_id=user.id,
                                      messages=[{"role": "user", "content": "问题"}])
        send(models[0])
        with pytest.raises(gateway.GatewayError) as caught:
            send(models[0])
        assert caught.value.code == "CAP_REACHED"
        send(models[1])
        db.get(type(models[1]), models[1].id).monthly_call_cap = 0
        db.commit()
        with pytest.raises(gateway.GatewayError) as caught:
            send(models[1])
        assert caught.value.code == "USER_CAP_REACHED"
        assert usage.remaining_calls(db, user.id, models[0].id) == {"monthly": 0, "daily": 0}
        assert usage.monthly_usage(db)["cost"] == Decimal("8.00")
        assert usage.monthly_usage(db, models[0].id)["calls"] == 1
    assert len(requests) == 2


def test_config_structured_and_unavailable(models, db_sessionmaker, monkeypatch):
    requests = install(monkeypatch, '```json\n{"ok": true, "items": [{"n": 1}, {"n": 2}]}\n```')
    with db_sessionmaker() as db:
        cfg = gateway.load_model(db, models[0].id)
        db.add(LlmCall(model_id=cfg.id, purpose="review", model_name=cfg.name, status="sent"))
        db.commit()
        cfg.id = None
        gateway.test_config(db, cfg, None, structured=True)
        assert db.scalars(select(LlmCall).order_by(LlmCall.id)).all()[-1].model_id is None
        assert json.loads(requests[0].content)["response_format"] == {"type": "json_object"}
        install(monkeypatch, '{"ok": true}')
        with pytest.raises(gateway.GatewayError) as caught:
            gateway.test_config(db, cfg, None, structured=True)
        assert caught.value.code == "BAD_RESPONSE"
        db.get(type(models[0]), models[0].id).enabled = False
        db.commit()
        for model_id in (models[0].id, 999999):
            with pytest.raises(gateway.GatewayError) as caught:
                gateway.load_model(db, model_id)
            assert caught.value.code == "MODEL_UNAVAILABLE"
