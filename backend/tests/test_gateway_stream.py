"""T-49：真实网关路径，网络由 SSE MockTransport 替身提供。"""
import json

import httpx
import pytest
from sqlalchemy import select

from app.db.models import LlmCall
from app.llm import gateway
from tests.review_support import env  # noqa: F401


class Events(httpx.SyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks, self.error = chunks, error

    def __iter__(self):
        for text in self.chunks:
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n").encode()
        if self.error:
            raise self.error
        yield b'data: {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}}\n\n'
        yield b'data: [DONE]\n\n'


def transport(monkeypatch, chunks=("你好", "世界"), error=None, status=200):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, stream=Events(chunks, error))

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    return requests


def test_stream_sent_usage_and_forced_flags(env, db_sessionmaker, monkeypatch):
    requests = transport(monkeypatch)
    seen = []
    with db_sessionmaker() as db:
        result = gateway.stream_model(db, purpose="assist", messages=[{"role": "user", "content": "普通问题"}],
                                      on_delta=lambda text: seen.append(text))
        row = db.scalar(select(LlmCall))
        assert (row.purpose, row.status, row.response_text, row.tokens_in, row.tokens_out) == ("assist", "sent", "你好世界", 12, 7)
        assert result.content == "你好世界" and seen == ["你好", "世界"]
    body = json.loads(requests[0].content)
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert len(requests) == 1


def test_t49_7_stop_records_received_part(env, db_sessionmaker, monkeypatch):
    requests = transport(monkeypatch, ("第一段", "不应继续"))
    with db_sessionmaker() as db:
        result = gateway.stream_model(db, purpose="assist", messages=[{"role": "user", "content": "问题"}],
                                      on_delta=lambda text: False)
        row = db.scalar(select(LlmCall))
        assert result.content == row.response_text == "第一段" and row.status == "sent"
    assert len(requests) == 1


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout("虚构敏感错误文字"), "TIMEOUT"),
                                       (httpx.ReadError("虚构敏感错误文字"), "HTTP_ERROR")])
def test_t49_9_partial_failure_no_retry(env, db_sessionmaker, monkeypatch, error, code, caplog):
    requests = transport(monkeypatch, ("已生成部分",), error)
    seen = []
    with db_sessionmaker() as db:
        with pytest.raises(gateway.GatewayError) as caught:
            gateway.stream_model(db, purpose="assist", messages=[{"role": "user", "content": "问题"}], on_delta=seen.append)
        assert caught.value.code == code
        row = db.scalar(select(LlmCall))
        assert row.status == "error" and row.response_text == "已生成部分"
    assert seen == ["已生成部分"] and len(requests) == 1
    assert "虚构敏感错误文字" not in caplog.text


def test_t49_8_blocked_before_transport(env, db_sessionmaker, monkeypatch):
    requests = transport(monkeypatch)
    with db_sessionmaker() as db:
        with pytest.raises(gateway.GatewayError) as caught:
            gateway.stream_model(db, purpose="assist", messages=[{"role": "user", "content": "电话：13800138001"}],
                                 on_delta=lambda text: None)
        assert caught.value.code == "BLOCKED"
        row = db.scalar(select(LlmCall))
        assert row.status == "blocked" and row.request_text is None
    assert requests == []


def test_bad_response_and_total_time_limit(env, db_sessionmaker, monkeypatch):
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, content=b'data: invalid\n\n')))
    with db_sessionmaker() as db:
        with pytest.raises(gateway.GatewayError) as caught:
            gateway.stream_model(db, purpose="assist", messages=[{"role": "user", "content": "问题"}], on_delta=lambda text: None)
        assert caught.value.code == "BAD_RESPONSE"


def test_stream_total_deadline_with_keepalive(env, db_sessionmaker, monkeypatch):
    from types import SimpleNamespace
    moments = iter([0, 0, *range(100, 2000, 100)])
    monkeypatch.setattr(gateway, "time", SimpleNamespace(monotonic=lambda: next(moments)))
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request:
        httpx.Response(200, content=b": keepalive\n" * 10)))
    with db_sessionmaker() as db:
        with pytest.raises(gateway.GatewayError) as caught:
            gateway.stream_model(db, purpose="assist", messages=[{"role": "user", "content": "问题"}], on_delta=lambda text: None)
        assert caught.value.code == "TIMEOUT"
        assert db.scalar(select(LlmCall.status)) == "error"


def test_total_deadline_closes_blocked_read_and_keeps_partial(env, db_sessionmaker, monkeypatch):
    import threading
    closed = threading.Event()

    class Blocking(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            assert closed.wait(2)
            raise httpx.ReadError("读取已关闭")

        def close(self):
            closed.set()

    monkeypatch.setattr(gateway, "STREAM_TOTAL_SECONDS", 0.05)
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, stream=Blocking())))
    with db_sessionmaker() as db:
        with pytest.raises(gateway.GatewayError) as caught:
            gateway.stream_model(db, purpose="assist", messages=[{"role": "user", "content": "问题"}], on_delta=lambda text: None)
        assert caught.value.code == "TIMEOUT"
        row = db.scalar(select(LlmCall))
        assert row.status == "error" and row.response_text == "partial"


def test_remaining_call_query_counts_assist(env, db_sessionmaker, make_user):
    from app.admin.settings_store import set_setting
    from app.llm.usage import remaining_calls
    user = make_user("remaining_assist")
    with db_sessionmaker() as db:
        set_setting(db, "model.monthly_call_cap", 5, None)
        set_setting(db, "model.user_daily_cap", 2, None)
        db.add(LlmCall(user_id=user.id, purpose="assist", model_name="mock", status="sent"))
        db.commit()
        assert remaining_calls(db, user.id) == {"monthly": 4, "daily": 1}


def test_inter_delta_timeout_keeps_first_delta(env, db_sessionmaker, monkeypatch):
    from types import SimpleNamespace
    moments = iter([0, 0, 0, 0, 200, 200])
    monkeypatch.setattr(gateway, "time", SimpleNamespace(monotonic=lambda: next(moments)))
    transport(monkeypatch, ("first", "second"))
    with db_sessionmaker() as db:
        with pytest.raises(gateway.GatewayError) as caught:
            gateway.stream_model(db, purpose="assist", messages=[{"role": "user", "content": "问题"}], on_delta=lambda text: None)
        assert caught.value.code == "TIMEOUT"
        row = db.scalar(select(LlmCall))
        assert row.status == "error" and row.response_text == "first"
