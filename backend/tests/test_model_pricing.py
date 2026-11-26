"""V351Q-21：模型自动计价与余额对账（少量代表性用例，用测试价格表）。需要 TEST_DATABASE_URL。"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db.models import LlmCall, LlmModel
from app.llm import balance, gateway, pricing
from tests.test_gateway import API_KEY, BASE_URL, gw  # noqa: F401

PRICES = """
version: "test-1"
cn_holidays: [2026-10-01..2026-10-07]
vendors:
  - vendor: 测试厂商
    host: llm.example.test
    source: https://llm.example.test/pricing
    effective: 2026-01-01
    balance: deepseek
    peak_hours: ["09:00-12:00", "14:00-18:00"]
    models:
      - names: [mock-model]
        tiers: [{input: 2, cached: 0.04, output: 8}]
        offpeak: [{input: 1, cached: 0.02, output: 4}]
      - names: [tiered*]
        tiers:
          - {max_input: 128000, input: 0.8, cached: 0.16, output: 2, output_thinking: 8}
          - {max_input: 256000, max_output: 200, input: 2.4, cached: 0.48, output: 20}
          - {input: 4.8, cached: 0.96, output: 48}
"""
PEAK = datetime(2026, 11, 26, 2, 0, tzinfo=timezone.utc)  # 北京时间周四 10:00
MESSAGES = [{"role": "user", "content": "普通问题"}]


@pytest.fixture
def prices(tmp_path, monkeypatch):
    path = tmp_path / "prices.yaml"
    path.write_text(PRICES, encoding="utf-8")
    monkeypatch.setattr(pricing, "PRICES_FILE", path)
    pricing.table.cache_clear()
    yield
    pricing.table.cache_clear()


def test_cached_tokens_and_peak_hours(prices):
    use = pricing.Usage(tokens_in=1_000_000, tokens_out=100_000, tokens_cached=600_000)
    # 未命中 0.4M×2 + 命中 0.6M×0.04 + 输出 0.1M×8
    assert pricing.call_cost(BASE_URL, "mock-model", use, PEAK) == Decimal("1.624000")
    # 国庆假期的工作日、周六、工作日午休都按空闲价
    for when in (datetime(2026, 10, 2, 2, tzinfo=timezone.utc), PEAK + timedelta(days=2),
                 PEAK + timedelta(hours=2, minutes=30)):
        assert pricing.call_cost(BASE_URL, "MOCK-model", use, when) == Decimal("0.812000")


def test_tiers_thinking_and_unpriced(prices):
    def cost(**usage):
        return pricing.call_cost(BASE_URL, "tiered-plus", pricing.Usage(**usage))
    assert cost(tokens_in=128_000, tokens_out=1000) == Decimal("0.104400")
    assert cost(tokens_in=128_000, tokens_out=1000, thinking=True) == Decimal("0.110400")
    assert cost(tokens_in=200_000, tokens_out=100) == Decimal("0.482000")
    # 输出超过第二档上限：整次请求落到下一档
    assert cost(tokens_in=200_000, tokens_out=1000) == Decimal("1.008000")
    assert pricing.call_cost(BASE_URL, "other-model", pricing.Usage(tokens_in=10)) is None
    assert pricing.call_cost("https://other.example.test/v1", "mock-model", pricing.Usage(tokens_in=10)) is None


def _handler(request):
    body = json.loads(request.content)
    if not body.get("stream"):  # DeepSeek 写法
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": {
            "prompt_tokens": 1000, "completion_tokens": 10, "prompt_cache_hit_tokens": 600, "prompt_cache_miss_tokens": 400}})
    # 流式：用量只在最后一个事件，OpenAI 写法 prompt_tokens_details.cached_tokens
    events = [{"choices": [{"delta": {"content": "好"}, "finish_reason": None}]},
              {"choices": [{"delta": {}, "finish_reason": "stop"}]},
              {"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 10,
                                        "prompt_tokens_details": {"cached_tokens": 800}}}]
    text = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})


def test_gateway_records_cached_tokens_and_cost(gw, prices, db_sessionmaker, monkeypatch):
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(_handler))
    monkeypatch.setattr(gateway, "utcnow", lambda: PEAK)
    with db_sessionmaker() as db:
        mid = db.scalar(select(LlmModel.id))
        gateway.call_model(db, model_id=mid, purpose="review", messages=MESSAGES)
        gateway.call_model(db, model_id=mid, purpose="review", messages=MESSAGES, on_delta=lambda _n: None)
        gateway.stream_model(db, model_id=mid, purpose="assist", messages=MESSAGES, on_delta=lambda _t: True)
        db.get(LlmModel, mid).model_name = "other-model"
        db.commit()
        gateway.call_model(db, model_id=mid, purpose="review", messages=MESSAGES)
        rows = [(r.tokens_in, r.tokens_cached, r.tokens_out, r.cost)
                for r in db.scalars(select(LlmCall).order_by(LlmCall.id))]
    assert rows == [(1000, 600, 10, Decimal("0.000904")), (1000, 800, 10, Decimal("0.000512")),
                    (1000, 800, 10, Decimal("0.000512")), (1000, 600, 10, None)]


def test_balance_reconcile_detects_drift_and_topup(gw, prices, db_sessionmaker, monkeypatch):
    balances, requests = iter(["100.00", "90.00", "95.00"]), []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": next(balances), "granted_balance": "0", "topped_up_balance": "0"}]})
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    with db_sessionmaker() as db:
        model = db.scalar(select(LlmModel))
        balance.check(db, now=PEAK)
        db.add(LlmCall(model_id=model.id, purpose="review", model_name="mock-model", status="sent",
                       cost=Decimal("8"), created_at=PEAK + timedelta(minutes=10)))
        db.commit()
        balance.check(db, now=PEAK + timedelta(minutes=30))  # 不到一小时不重复查询
        balance.check(db, now=PEAK + timedelta(hours=1))
        state = balance.view(db, model, API_KEY)
        assert (state["verdict"], state["spent"], state["cost"]) == ("drift", "10.00", "8.00")
        balance.check(db, now=PEAK + timedelta(hours=2))  # 余额上升：充值，重新起算，上一窗口结论保留
        state = balance.view(db, model, API_KEY)
        assert (state["verdict"], state["balance"], state["prev"]["verdict"]) == ("pending", "95.00", "drift")
    assert len(requests) == 3
    assert str(requests[0].url) == "https://llm.example.test/user/balance"
    assert requests[0].headers["Authorization"] == f"Bearer {API_KEY}"
