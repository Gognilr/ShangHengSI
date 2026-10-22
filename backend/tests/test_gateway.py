"""模型网关、设置读取、用量、set-model 命令（M2-10）。需要 TEST_DATABASE_URL。"""
import io
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app import cli
from app.admin import settings_store
from app.config import get_settings
from app.db import session as db_session_module
from app.db.models import LlmCall, RedactionMap, Task
from app.llm import gateway, usage
from app.llm.gateway import GatewayError, call_model, test_connection
from app.security import crypto
from tests.mock_llm import build_content, mock_transport

API_KEY = "sk-test-0123456789SECRETKEY"
BASE_URL = "https://llm.example.test/v1"
REAL_NAME = "星河虚构进出口有限公司"


@pytest.fixture
def gw(tmp_path, monkeypatch, pg_url, db_engine, db_sessionmaker):
    """主密钥、cli 用的测试库、已配置好的模型设置；重试间隔改为 0 并记录。"""
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    monkeypatch.setenv("DATABASE_URL", pg_url)
    for f in (get_settings, crypto.get_master_key, db_session_module.get_engine, db_session_module.get_sessionmaker):
        f.cache_clear()
    sleeps: list[float] = []
    monkeypatch.setattr(gateway.time, "sleep", sleeps.append)
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.base_url", BASE_URL, None)
        settings_store.set_setting(db, "model.name", "mock-model", None)
        settings_store.set_setting(db, "model.api_key_enc", crypto.encrypt_to_b64(API_KEY), None)
        settings_store.set_setting(db, "model.extra_params", {"thinking": {"type": "disabled"}}, None)
        db.commit()
    yield sleeps
    db_session_module.get_engine().dispose()
    for f in (get_settings, crypto.get_master_key, db_session_module.get_engine, db_session_module.get_sessionmaker):
        f.cache_clear()


def use(monkeypatch, modes="normal") -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(gateway, "TRANSPORT", mock_transport(modes, requests))
    return requests


def calls(db) -> list[LlmCall]:
    return list(db.scalars(select(LlmCall).order_by(LlmCall.id)))


REVIEW_MESSAGES = [
    {"role": "system", "content": "你是企业商务文件审核助手。"},
    {"role": "user", "content": "【审核要求】\n[G-01] 要求一\n[C-06] 要求二\n【待审核文件】\n<document>\n"
                                "[F1-P1] 买方：客户_A\n[F1-P2] 总价 USD [金额_1]\n</document>"},
]


def assert_no_secret(rows: list[LlmCall]) -> None:
    for row in rows:
        for value in vars(row).values():
            assert API_KEY not in str(value)


def test_send_records_call(gw, db_sessionmaker, make_user, monkeypatch):
    requests = use(monkeypatch)
    user = make_user("alice")
    with db_sessionmaker() as db:
        r = call_model(db, purpose="review", messages=REVIEW_MESSAGES, user_id=user.id, task_title="合同.docx")
        assert json.loads(r.content)["requirement_results"][0]["req_id"] == "G-01"
        assert r.content == build_content(REVIEW_MESSAGES)
        (row,) = calls(db)
    assert (row.id, row.status, row.purpose, row.model_name, row.user_id) == (r.llm_call_id, "sent", "review", "mock-model", user.id)
    assert row.tokens_in > 0 and row.tokens_out > 0 and row.duration_ms is not None and row.response_text == r.content
    # 请求：地址、密钥在请求头、请求体字段与附加参数
    (req,) = requests
    assert str(req.url) == f"{BASE_URL}/chat/completions"
    assert req.headers["Authorization"] == f"Bearer {API_KEY}"
    body = json.loads(req.content)
    assert body == {"model": "mock-model", "messages": REVIEW_MESSAGES, "temperature": 0.1, "max_tokens": 32000,
                    "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}}
    assert json.loads(row.request_text) == body
    assert_no_secret([row])


def test_extra_params_merge_and_json_mode(gw, db_sessionmaker, monkeypatch):
    requests = use(monkeypatch)
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.extra_params",
                                   {"temperature": 0.7, "messages": [{"role": "user", "content": "替换"}], "top_p": 0.9}, None)
        settings_store.set_setting(db, "model.json_mode", False, None)
        db.commit()
        call_model(db, purpose="chat", messages=REVIEW_MESSAGES)
        call_model(db, purpose="chat", messages=REVIEW_MESSAGES, json_mode=True)
    first, second = (json.loads(r.content) for r in requests)
    assert first["temperature"] == 0.7 and first["top_p"] == 0.9  # 原样合并
    assert first["messages"] == REVIEW_MESSAGES  # 不能替换已复检的消息
    assert "response_format" not in first and second["response_format"] == {"type": "json_object"}


def test_not_configured(gw, db_sessionmaker, monkeypatch):
    requests = use(monkeypatch)
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.api_key_enc", "", None)
        db.commit()
        with pytest.raises(GatewayError) as exc:
            call_model(db, purpose="review", messages=REVIEW_MESSAGES)
        assert exc.value.code == "NOT_CONFIGURED" and calls(db) == [] and requests == []


def test_monthly_cap_checked_first(gw, db_sessionmaker, monkeypatch):
    requests = use(monkeypatch)
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.monthly_call_cap", 2, None)
        db.add_all([LlmCall(purpose="review", model_name="m", status="sent"),
                    LlmCall(purpose="review", model_name="m", status="error"),
                    LlmCall(purpose="review", model_name="m", status="sent",
                            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))])  # 往月不计
        db.commit()
        call_model(db, purpose="review", messages=REVIEW_MESSAGES)  # 本月第 2 次
        # 达到上限：先于出口复检，消息中有未脱敏电话也不会产生 blocked 记录，更不会发送
        leaky = [{"role": "user", "content": "电话 13800138000"}]
        with pytest.raises(GatewayError) as exc:
            call_model(db, purpose="review", messages=leaky)
        assert exc.value.code == "CAP_REACHED" and str(exc.value) == "已达本月调用上限，请联系管理员"
        assert len(requests) == 1 and [c.status for c in calls(db)][-1] == "sent"
        settings_store.set_setting(db, "model.monthly_call_cap", 0, None)  # 0 表示不限
        db.commit()
        call_model(db, purpose="review", messages=REVIEW_MESSAGES)
        assert len(requests) == 2


def test_t07_blocked_not_sent_and_record_has_no_real_value(gw, db_sessionmaker, make_user, monkeypatch, caplog):
    requests = use(monkeypatch)
    user = make_user("alice")
    with db_sessionmaker() as db:
        task = Task(owner_id=user.id, title="合同.docx", stance="sales", source="outgoing", policy="ai",
                    status="reviewing")
        db.add(task)
        db.flush()
        mapping = {"我方": REAL_NAME, "客户_A": "Fictional Metals Trading FZE"}
        db.add(RedactionMap(task_id=task.id, map_enc=crypto.encrypt_text(json.dumps(mapping, ensure_ascii=False))))
        db.commit()
        leaked = [REVIEW_MESSAGES[0], {"role": "user", "content": REVIEW_MESSAGES[1]["content"].replace("客户_A", REAL_NAME)}]
        with caplog.at_level(logging.INFO), pytest.raises(GatewayError) as exc:
            call_model(db, purpose="review", messages=leaked, user_id=user.id, task_id=task.id)
        assert exc.value.code == "BLOCKED" and {r.category for r in exc.value.reasons} == {"MAP"}
        (row,) = calls(db)
    assert requests == []  # 未发送
    assert (row.status, row.request_text, row.response_text, row.task_id) == ("blocked", None, None, task.id)
    assert row.id == exc.value.llm_call_id
    # 位置是在全部 messages 的 content 用换行拼接后的文本中
    assert row.block_reason == f"MAP@{'\n'.join(m['content'] for m in leaked).index(REAL_NAME)}"
    for value in vars(row).values():
        assert REAL_NAME not in str(value)
    assert REAL_NAME not in caplog.text and REAL_NAME not in str(exc.value)


def test_placeholder_text_is_not_a_real_value(gw, db_sessionmaker, make_user, monkeypatch):
    """B-04、Q-134 ②：对照表真实值恰好是代号的一部分（“电话”与“[电话]”）时，代号本身不拦截，正文中出现仍拦截。"""
    requests = use(monkeypatch)
    user = make_user("bob")
    with db_sessionmaker() as db:
        task = Task(owner_id=user.id, title="合同.docx", stance="sales", source="outgoing", policy="ai",
                    status="reviewing")
        db.add(task)
        db.flush()
        mapping = {"人员_1": "电话", "[电话]#1": "0531-6612345"}
        db.add(RedactionMap(task_id=task.id, map_enc=crypto.encrypt_text(json.dumps(mapping, ensure_ascii=False))))
        db.commit()
        ok = [{"role": "user", "content": "[F1-P1] 联系人员_1：【[电话]】"}]
        call_model(db, purpose="review", messages=ok, user_id=user.id, task_id=task.id)
        assert len(requests) == 1
        leaked = [{"role": "user", "content": "[F1-P1] 联系电话：【[电话]】"}]
        with pytest.raises(GatewayError) as exc:
            call_model(db, purpose="review", messages=leaked, user_id=user.id, task_id=task.id)
        assert exc.value.code == "BLOCKED" and [(r.category, r.position) for r in exc.value.reasons] == [("MAP", 10)]
    assert len(requests) == 1


def test_rescan_blocks_unreplaced_phone_without_task(gw, db_sessionmaker, monkeypatch):
    use(monkeypatch)
    with db_sessionmaker() as db, pytest.raises(GatewayError) as exc:
        call_model(db, purpose="chat", messages=[{"role": "user", "content": "请联系 13800138000"}])
    assert [r.category for r in exc.value.reasons] == ["PHONE"]


def test_retries_then_success(gw, db_sessionmaker, monkeypatch):
    requests = use(monkeypatch, ["http_500", "timeout", "normal"])
    with db_sessionmaker() as db:
        r = call_model(db, purpose="review", messages=REVIEW_MESSAGES)
        rows = calls(db)
    assert gw == [10, 30]  # 间隔 10 秒、30 秒
    assert len(requests) == 3 and [c.status for c in rows] == ["error", "error", "sent"]
    assert rows[0].error_message == "模型服务调用失败（HTTP 500）" and rows[1].error_message == "模型服务响应超时"
    assert r.llm_call_id == rows[2].id
    with db_sessionmaker() as db:
        assert usage.monthly_sent_calls(db) == 1  # 重试失败的不计入用量


@pytest.mark.parametrize("mode,code", [("http_500", "HTTP_ERROR"), ("timeout", "TIMEOUT")])
def test_retries_exhausted(gw, db_sessionmaker, monkeypatch, mode, code):
    requests = use(monkeypatch, mode)
    with db_sessionmaker() as db:
        with pytest.raises(GatewayError) as exc:
            call_model(db, purpose="review", messages=REVIEW_MESSAGES)
        assert exc.value.code == code and len(requests) == 3
        assert [c.status for c in calls(db)] == ["error"] * 3
        assert exc.value.llm_call_id == calls(db)[-1].id


def test_retry_false_sends_once(gw, db_sessionmaker, monkeypatch):
    """追问（M6-07）：retry=False 时失败不重试，只有一次请求和一条 error 记录。"""
    requests = use(monkeypatch, "http_500")
    with db_sessionmaker() as db:
        with pytest.raises(GatewayError) as exc:
            call_model(db, purpose="chat", messages=REVIEW_MESSAGES, retry=False)
        assert exc.value.code == "HTTP_ERROR" and len(requests) == 1 and gw == []
        assert [c.status for c in calls(db)] == ["error"]


def test_timeout_override(gw, db_sessionmaker, monkeypatch):
    """timeout_s 覆盖设置中的超时；不传时仍用设置值（默认 180 秒）。"""
    seen: list[float] = []
    original = gateway._send_once

    def spy(cfg, *args):
        seen.append(cfg.timeout_s)
        return original(cfg, *args)

    monkeypatch.setattr(gateway, "_send_once", spy)
    use(monkeypatch)
    with db_sessionmaker() as db:
        call_model(db, purpose="chat", messages=REVIEW_MESSAGES, timeout_s=120)
        call_model(db, purpose="review", messages=REVIEW_MESSAGES)
    assert seen == [120.0, 180.0]


def test_client_error_not_retried_and_key_masked(gw, db_sessionmaker, monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {API_KEY}"}})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO), db_sessionmaker() as db:
        with pytest.raises(GatewayError) as exc:
            call_model(db, purpose="test", messages=[{"role": "user", "content": "ping"}])
        rows = calls(db)
    assert exc.value.code == "HTTP_ERROR" and str(exc.value) == "模型服务调用失败（HTTP 401）"
    assert len(rows) == 1 and gw == []  # 4xx 不重试
    assert "***" in rows[0].response_text
    assert_no_secret(rows)
    assert API_KEY not in caplog.text and "ping" not in caplog.text


def test_bad_response_shape(gw, db_sessionmaker, monkeypatch):
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, text="<html>gateway</html>")))
    with db_sessionmaker() as db, pytest.raises(GatewayError) as exc:
        call_model(db, purpose="review", messages=REVIEW_MESSAGES)
    assert exc.value.code == "BAD_RESPONSE"


def test_non_json_content_is_returned_for_validation(gw, db_sessionmaker, monkeypatch):
    # 内容不是 JSON 属于输出校验（M2-11）的事，网关照常返回并记为 sent
    use(monkeypatch, "non_json")
    with db_sessionmaker() as db:
        r = call_model(db, purpose="review", messages=REVIEW_MESSAGES)
    assert r.content == build_content(REVIEW_MESSAGES, "non_json")


def test_connection_sends_fixed_message(gw, db_sessionmaker, make_user, monkeypatch):
    requests = use(monkeypatch)
    user = make_user("admin1", role="admin")
    with db_sessionmaker() as db:
        r = test_connection(db, user.id)
        (row,) = calls(db)
    body = json.loads(requests[0].content)
    assert body["messages"] == [{"role": "user", "content": gateway.TEST_MESSAGE}] and "response_format" not in body
    assert (row.purpose, row.user_id, row.task_id) == ("test", user.id, None) and r.llm_call_id == row.id


# ---------------------------------------------------------------- 设置与用量


def test_settings_defaults_and_unknown_keys(gw, db_sessionmaker):
    with db_sessionmaker() as db:
        assert settings_store.get_setting(db, "model.max_tokens") == 32000
        assert settings_store.get_setting(db, "model.json_mode") is True
        assert settings_store.get_setting(db, "review.company_profile") == ""
        d = settings_store.get_setting(db, "model.chunk_chars")
        assert d == 60000
        with pytest.raises(KeyError):
            settings_store.get_setting(db, "model.unknown")
        with pytest.raises(KeyError):
            settings_store.set_setting(db, "nope", 1, None)
        settings_store.set_setting(db, "model.max_tokens", 4000, None)
        db.commit()
        assert settings_store.get_setting(db, "model.max_tokens") == 4000
        assert settings_store.get_settings_map(db, "model.")["name"] == "mock-model"
        assert settings_store.DEFAULTS["model.extra_params"] == {}  # 默认值不被修改


def test_monthly_usage_shanghai_month_and_cost(gw, db_sessionmaker):
    now = datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc)
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.price_in_per_m", 2, None)
        settings_store.set_setting(db, "model.price_out_per_m", 8, None)
        db.add_all([
            # 9 月 30 日 17:00 UTC = 上海 10 月 1 日 01:00，计入 10 月
            LlmCall(purpose="review", model_name="m", status="sent", tokens_in=1_000_000, tokens_out=125_001,
                    created_at=datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)),
            # 9 月 30 日 15:00 UTC = 上海 9 月 30 日 23:00，不计
            LlmCall(purpose="review", model_name="m", status="sent", tokens_in=5, tokens_out=5,
                    created_at=datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)),
            LlmCall(purpose="review", model_name="m", status="error", tokens_in=7, tokens_out=7,
                    created_at=datetime(2026, 10, 2, tzinfo=timezone.utc)),
            LlmCall(purpose="review", model_name="m", status="blocked",
                    created_at=datetime(2026, 10, 2, tzinfo=timezone.utc)),
        ])
        db.commit()
        u = usage.monthly_usage(db, now)
    # 费用 = 1,000,000×2/1e6 + 125,001×8/1e6 = 2 + 1.000008 → 3.00（四舍五入两位）
    assert u == {"calls": 1, "tokens_in": 1_000_000, "tokens_out": 125_001, "cost": Decimal("3.00")}
    assert usage.month_start(now).isoformat() == "2026-10-01T00:00:00+08:00"


# ---------------------------------------------------------------- set-model 命令


def test_set_model_command(gw, db_sessionmaker, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-new-key-ABCD9876\n"))
    assert cli.main(["set-model", "--base-url", "http://mock-llm:9000/v1", "--model", "mock-v2",
                     "--extra-params", '{"thinking": {"type": "enabled"}}', "--api-key-stdin"]) == 0
    out = capsys.readouterr().out
    assert "sk-new-key" not in out and "9876" in out
    with db_sessionmaker() as db:
        s = settings_store.get_settings_map(db, "model.")
        assert (s["base_url"], s["name"], s["extra_params"]) == ("http://mock-llm:9000/v1", "mock-v2",
                                                                 {"thinking": {"type": "enabled"}})
        assert "sk-new-key" not in s["api_key_enc"] and crypto.decrypt_from_b64(s["api_key_enc"]) == "sk-new-key-ABCD9876"
    # 不带 --api-key-stdin 时保留原密钥；非法参数报错
    assert cli.main(["set-model", "--base-url", "https://x.test/v1", "--model", "m3"]) == 0
    with db_sessionmaker() as db:
        assert crypto.decrypt_from_b64(settings_store.get_setting(db, "model.api_key_enc")) == "sk-new-key-ABCD9876"
    assert cli.main(["set-model", "--base-url", "ftp://x", "--model", "m"]) == 1
    assert cli.main(["set-model", "--base-url", "https://x", "--model", "m", "--extra-params", "[1]"]) == 1


def test_set_model_strips_bom_and_rejects_non_ascii_key(gw, db_sessionmaker, monkeypatch):
    # PowerShell 管道传入的密钥开头带 BOM：去掉后保存
    monkeypatch.setattr("sys.stdin", io.StringIO("﻿sk-bom-key-1234\r\n"))
    assert cli.main(["set-model", "--base-url", "http://mock-llm:9000/v1", "--model", "m", "--api-key-stdin"]) == 0
    with db_sessionmaker() as db:
        assert crypto.decrypt_from_b64(settings_store.get_setting(db, "model.api_key_enc")) == "sk-bom-key-1234"
    # 其他非 ASCII 或空白字符无法放进 HTTP 头：拒绝，原密钥不变
    for bad in ("sk-全角-1234\n", "sk bad key 12\n"):
        monkeypatch.setattr("sys.stdin", io.StringIO(bad))
        assert cli.main(["set-model", "--base-url", "http://mock-llm:9000/v1", "--model", "m", "--api-key-stdin"]) == 1
    with db_sessionmaker() as db:
        assert crypto.decrypt_from_b64(settings_store.get_setting(db, "model.api_key_enc")) == "sk-bom-key-1234"
