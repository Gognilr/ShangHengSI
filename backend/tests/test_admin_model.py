"""模型设置接口（SPEC 11.1、15.2，设计 M7 第五节）。全部内容虚构。"""
import logging
import os

import pytest
from sqlalchemy import select

from app.admin import settings_store
from app.config import get_settings
from app.db import session as db_session_module
from app.db.base import utcnow
from app.db.models import AuditLog, LlmCall
from app.llm import gateway
from app.main import API_PREFIX
from app.security import crypto
from tests.mock_llm import mock_transport

MODEL = f"{API_PREFIX}/admin/settings/model"
TEST = f"{MODEL}/test"
USAGE = f"{API_PREFIX}/admin/usage"
KEY1 = "sk-fictive-AAAA1111BBBB"
KEY2 = "sk-fictive-CCCC2222DDDD"


def _hdr(csrf):
    return {"X-CSRF-Token": csrf}


def body(**over):
    b = {
        "base_url": "https://llm.example.test/v1",
        "name": "mock-model",
        "temperature": 0.1,
        "max_tokens": 32000,
        "timeout_s": 180,
        "json_mode": True,
        "extra_params": {"thinking": {"type": "enabled"}},
        "chunk_chars": 60000,
        "price_in_per_m": "1.5",
        "price_out_per_m": "0",
        "monthly_call_cap": 0,
        "api_key": KEY1,
    }
    b.update(over)
    return b


@pytest.fixture(autouse=True)
def master_key(tmp_path, monkeypatch):
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    for f in (get_settings, crypto.get_master_key):
        f.cache_clear()
    yield
    for f in (get_settings, crypto.get_master_key):
        f.cache_clear()


@pytest.fixture
def admin(make_user, login):
    make_user("boss", role="admin")
    return login("boss")


def _put(c, csrf, **over):
    return c.put(MODEL, json=body(**over), headers=_hdr(csrf))


def _audits(sm):
    with sm() as s:
        return [r.detail for r in s.scalars(select(AuditLog).where(AuditLog.action == "setting_update").order_by(AuditLog.id))]


def test_permissions(make_user, login, api):
    make_user("alice")
    c, csrf = login("alice")
    assert c.get(MODEL).status_code == 403
    assert c.put(MODEL, json=body(), headers=_hdr(csrf)).status_code == 403
    assert c.post(TEST, headers=_hdr(csrf)).status_code == 403
    assert c.get(USAGE).status_code == 403
    assert api.get(MODEL).status_code == 401


def test_get_defaults(admin):
    c, _ = admin
    r = c.get(MODEL).json()
    assert r["api_key_set"] is False and r["api_key_last4"] is None
    assert r["price_in_per_m"] == "0.00" and r["max_tokens"] == 32000


def test_put_and_get_only_last4_and_no_full_key_anywhere(admin, db_sessionmaker, caplog):
    c, csrf = admin
    with caplog.at_level(logging.DEBUG):
        r = _put(c, csrf)
    assert r.status_code == 200
    out = r.json()
    assert out["api_key_set"] is True and out["api_key_last4"] == KEY1[-4:]
    assert out["price_in_per_m"] == "1.50" and out["extra_params"] == {"thinking": {"type": "enabled"}}
    assert KEY1 not in r.text and KEY1 not in c.get(MODEL).text
    assert KEY1 not in caplog.text
    with db_sessionmaker() as s:
        enc = settings_store.get_setting(s, "model.api_key_enc")
        assert KEY1 not in enc and crypto.decrypt_from_b64(enc) == KEY1
        dump = " ".join(f"{a.detail} {a.target_id}" for a in s.scalars(select(AuditLog)))
    assert KEY1 not in dump and KEY1[-4:] not in dump
    assert "api_key" not in KEY1 or True


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_key_keeps_existing(admin, empty):
    c, csrf = admin
    _put(c, csrf)
    r = _put(c, csrf, api_key=empty, name="other-model")
    assert r.status_code == 200 and r.json()["api_key_last4"] == KEY1[-4:] and r.json()["name"] == "other-model"
    body_no_key = body()
    del body_no_key["api_key"]
    assert c.put(MODEL, json=body_no_key, headers=_hdr(csrf)).json()["api_key_last4"] == KEY1[-4:]


def test_new_key_takes_effect(admin, db_sessionmaker):
    c, csrf = admin
    _put(c, csrf)
    r = _put(c, csrf, api_key=KEY2)
    assert r.json()["api_key_last4"] == KEY2[-4:]
    with db_sessionmaker() as s:
        assert crypto.decrypt_from_b64(settings_store.get_setting(s, "model.api_key_enc")) == KEY2


def test_audit_keys_only_changed(admin, db_sessionmaker):
    c, csrf = admin
    _put(c, csrf)
    first = _audits(db_sessionmaker)[0]["keys"]
    assert "model.api_key_enc" in first and "model.name" in first and "model.price_out_per_m" not in first
    assert _put(c, csrf, api_key=None).status_code == 200  # 完全没变化
    assert len(_audits(db_sessionmaker)) == 1
    _put(c, csrf, api_key=None, temperature=0.5, price_in_per_m="1.5000")
    assert _audits(db_sessionmaker)[1] == {"keys": ["model.temperature"]}
    _put(c, csrf, api_key=KEY2, temperature=0.5)
    assert _audits(db_sessionmaker)[2] == {"keys": ["model.api_key_enc"]}


@pytest.mark.parametrize(
    "over",
    [
        {"base_url": "ftp://x.test"},
        {"base_url": "llm.example.test"},
        {"base_url": "https://a b.test"},
        {"base_url": "https://" + "a" * 495},
        {"name": ""},
        {"name": "n" * 101},
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"max_tokens": 0},
        {"max_tokens": 1_000_001},
        {"timeout_s": 9},
        {"timeout_s": 601},
        {"chunk_chars": 999},
        {"chunk_chars": 1_000_001},
        {"monthly_call_cap": -1},
        {"price_in_per_m": "-1"},
        {"price_in_per_m": "1.12345"},
        {"price_out_per_m": "abc"},
        {"price_out_per_m": ""},
        {"api_key": "short"},
        {"api_key": "k" * 501},
        {"api_key": "密钥密钥密钥密钥密钥"},
        {"api_key": "has space inside key"},
        {"unknown": 1},
    ],
)
def test_validation_rejects(admin, over):
    c, csrf = admin
    r = _put(c, csrf, **over)
    assert r.status_code == 422, over
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "over",
    [
        {"temperature": 0},
        {"temperature": 2},
        {"max_tokens": 1},
        {"max_tokens": 1_000_000},
        {"timeout_s": 10},
        {"timeout_s": 600},
        {"chunk_chars": 1000},
        {"chunk_chars": 1_000_000},
        {"monthly_call_cap": 0},
        {"price_in_per_m": "0.0001"},
        {"api_key": "k" * 8},
        {"api_key": "k" * 500},
        {"base_url": "http://10.0.0.5:8000/v1"},
        {"name": "n" * 100},
    ],
)
def test_validation_boundaries_ok(admin, over):
    c, csrf = admin
    assert _put(c, csrf, **over).status_code == 200, over


@pytest.mark.parametrize("extra", [[], "x", 1, None, {"messages": []}, {"a": 1, "messages": 1}])
def test_extra_params_invalid(admin, extra):
    c, csrf = admin
    r = _put(c, csrf, extra_params=extra)
    assert r.status_code == 422 and r.json()["code"] == "EXTRA_PARAMS_INVALID"
    assert r.json()["message"] == "附加请求参数须为 JSON 对象，且不能包含 messages"


def test_extra_params_empty_object_ok(admin):
    c, csrf = admin
    assert _put(c, csrf, extra_params={}).json()["extra_params"] == {}


def test_prices_are_decimal_strings(admin, db_sessionmaker):
    c, csrf = admin
    r = _put(c, csrf, price_in_per_m="0.1", price_out_per_m="12")
    assert r.json()["price_in_per_m"] == "0.10" and r.json()["price_out_per_m"] == "12.00"
    with db_sessionmaker() as s:
        assert settings_store.get_setting(s, "model.price_in_per_m") == "0.10"


# ---------------------------------------------------------------- 测试连接


def test_connection_not_configured(admin):
    c, csrf = admin
    r = c.post(TEST, headers=_hdr(csrf))
    assert r.status_code == 200
    assert r.json() == {"ok": False, "model": None, "duration_ms": None, "reply": None, "message": "模型尚未配置，请联系管理员"}


def test_connection_ok_writes_llm_call(admin, monkeypatch, db_sessionmaker):
    c, csrf = admin
    _put(c, csrf)
    reqs = []
    monkeypatch.setattr(gateway, "TRANSPORT", mock_transport("normal", reqs))
    r = c.post(TEST, headers=_hdr(csrf))
    out = r.json()
    assert out["ok"] is True and out["model"] == "mock-model" and isinstance(out["duration_ms"], int)
    assert isinstance(out["reply"], str) and len(reqs) == 1
    with db_sessionmaker() as s:
        rows = s.scalars(select(LlmCall)).all()
        assert [(x.purpose, x.status) for x in rows] == [("test", "sent")]
        assert KEY1 not in str(vars(rows[0]))
    assert KEY1 not in r.text
    assert _audits(db_sessionmaker)[-1]["keys"]  # 只有保存写了审计，测试连接没有再写
    assert len(_audits(db_sessionmaker)) == 1


def test_connection_http_error(admin, monkeypatch):
    c, csrf = admin
    _put(c, csrf)
    monkeypatch.setattr(gateway.time, "sleep", lambda s: None)
    monkeypatch.setattr(gateway, "TRANSPORT", mock_transport("http_500"))
    r = c.post(TEST, headers=_hdr(csrf))
    assert r.status_code == 200
    assert r.json()["ok"] is False and "HTTP 500" in r.json()["message"]
    assert KEY1 not in r.text


def test_connection_cap_reached(admin, monkeypatch, db_sessionmaker):
    c, csrf = admin
    _put(c, csrf, monthly_call_cap=1)
    with db_sessionmaker() as s:
        s.add(LlmCall(purpose="chat", model_name="m", status="sent", created_at=utcnow()))
        s.commit()
    monkeypatch.setattr(gateway, "TRANSPORT", mock_transport("normal"))
    r = c.post(TEST, headers=_hdr(csrf))
    assert r.json() == {"ok": False, "model": None, "duration_ms": None, "reply": None, "message": "已达本月调用上限，请联系管理员"}


# ---------------------------------------------------------------- 用量


def test_usage(admin, db_sessionmaker):
    c, csrf = admin
    _put(c, csrf, price_in_per_m="2", price_out_per_m="4.5", monthly_call_cap=10)
    with db_sessionmaker() as s:
        for tin, tout, st in [(1000, 500, "sent"), (2000, 1000, "sent"), (999, 999, "error")]:
            s.add(LlmCall(purpose="review", model_name="m", status=st, tokens_in=tin, tokens_out=tout, created_at=utcnow()))
        s.commit()
    u = c.get(USAGE).json()
    assert u["calls"] == 2 and u["cap"] == 10 and u["tokens_in"] == 3000 and u["tokens_out"] == 1500
    # (3000*2 + 1500*4.5) / 1e6 = 0.01275 → 0.01
    assert u["cost"] == "0.01"
    assert len(u["month"]) == 7 and u["month"][4] == "-"


def test_connection_shows_provider_reason_without_key(admin, monkeypatch, caplog):
    """M8-05：测试连接失败时附上模型服务给出的原因，去掉密钥；原因不写日志。"""
    import httpx

    c, csrf = admin
    _put(c, csrf)
    reason = f"The supported API model names are model-a, model-b. key {KEY1} sk-abcdefghijklmn"

    def handler(request):
        return httpx.Response(400, json={"error": {"message": reason, "type": "invalid_request_error"}})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG):
        r = c.post(TEST, headers=_hdr(csrf))
    msg = r.json()["message"]
    assert r.json()["ok"] is False and "HTTP 400" in msg and "supported API model names are model-a" in msg
    assert KEY1 not in r.text and "sk-abcdefghijklmn" not in r.text
    assert "supported API model names" not in caplog.text


@pytest.mark.parametrize("text,expected", [
    (None, None),
    ("", None),
    ('{"error": "plain reason"}', "plain reason"),
    ("<html>bad   gateway\n</html>", "<html>bad gateway </html>"),
    ('{"error": {"message": "' + "x" * 400 + '"}}', "x" * 300),
])
def test_error_detail_extraction(text, expected):
    assert gateway._error_detail(text, "") == expected
