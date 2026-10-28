"""T-50：仅接受价格代号，本地 Decimal 计算；结果结构不保存价格真实值。"""
import json
from decimal import Decimal

import pytest
import uuid
import httpx
from sqlalchemy import select
from app.db.models import AssistantMessage, DocNode, LlmCall, TaskFile
from app.db.base import utcnow
from app.redaction import engine
from app.security import crypto
from app.llm import gateway
from tests.review_support import env  # noqa: F401
from tests.test_assistant_chat import BASE, conversation, run  # noqa: F401

MAPPING = {"[[ORG_1]]": "虚构甲公司", "[[ORG_2]]": "虚构乙公司", "[[PRICE_1]]": "USD 10.00",
           "[[PRICE_2]]": "USD 12.50", "[[AMT_1]]": "USD 1000.00", "[[AMT_2]]": "USD 1250.00"}


def data(currency="USD", price="[[PRICE_2]]"):
    return {"suppliers": [{"file": 1, "name": "[[ORG_1]]", "currency": "USD", "terms": "FOB"},
                          {"file": 2, "name": "[[ORG_2]]", "currency": currency, "terms": "CIF"}],
            "rows": [{"item": "虚构不锈钢管304", "unit": "米", "quotes": [
                {"file": 1, "qty": "100", "unit_price": "[[PRICE_1]]", "amount": "[[AMT_1]]"},
                {"file": 2, "qty": "100", "unit_price": price, "amount": "[[AMT_2]]"}]}], "notes": "交期不同"}


def test_t50_7_decimal_min_difference_and_totals():
    from app.assistant.compare import normalize, calculate
    normalized = normalize(json.dumps(data()), MAPPING, [1, 2])
    result = calculate(normalized, MAPPING)
    row = result["rows"][0]
    assert row["minimum_files"] == [1]
    assert row["quotes"][1]["difference_pct"] == Decimal("25.0")
    assert result["totals"][1]["amount"] == Decimal("1000.00")
    assert result["totals"][2]["amount"] == Decimal("1250.00")
    stored = json.dumps(normalized, ensure_ascii=False)
    assert "10.00" not in stored and "12.50" not in stored and "1250.00" not in stored
    assert "虚构甲公司" not in stored


@pytest.mark.parametrize("price", ["12.5", "[[PRICE_99]]", "[[AMT_2]]", 12.5])
def test_t50_8_literal_or_unknown_prices_unverifiable(price):
    from app.assistant.compare import normalize, calculate
    result = calculate(normalize(data(price=price), MAPPING, [1, 2]), MAPPING)
    assert result["rows"][0]["quotes"][1]["status"] == "无法核对"
    assert result["totals"][2]["status"] == "不完整"


def test_t50_9_different_currencies():
    from app.assistant.compare import normalize, calculate
    mapping = {**MAPPING, "[[PRICE_2]]": "12.50", "[[AMT_2]]": "1250.00"}
    result = calculate(normalize(data(currency="EUR"), mapping, [1, 2]), mapping)
    assert result["rows"][0]["status"] == "币种不同"
    assert all(q["difference_pct"] is None for q in result["rows"][0]["quotes"])


@pytest.mark.parametrize("payload", ["not-json", "[]", {"suppliers": [], "rows": []}, data()])
def test_t50_10_invalid_json_or_file_reference(payload):
    from app.assistant.compare import normalize
    with pytest.raises(ValueError):
        normalize(payload, MAPPING, [1, 3])


def test_zero_price_does_not_divide_by_zero():
    from app.assistant.compare import normalize, calculate
    mapping = {**MAPPING, "[[PRICE_1]]": "USD 0.00"}
    result = calculate(normalize(data(), mapping, [1, 2]), mapping)
    assert result["rows"][0]["minimum_files"] == [1]
    assert all(q["difference_pct"] is None for q in result["rows"][0]["quotes"])


def test_source_currency_precedes_model_currency_and_incomplete_has_no_minimum():
    from app.assistant.compare import normalize, calculate
    result = calculate(normalize(data(currency="EUR"), MAPPING, [1, 2]), MAPPING)
    assert result["rows"][0]["status"] == "可核对"
    assert result["rows"][0]["quotes"][1]["currency"] == "USD"
    missing = data()
    missing["rows"][0]["quotes"].pop()
    result = calculate(normalize(missing, MAPPING, [1, 2]), MAPPING)
    assert result["rows"][0]["minimum_files"] == []
    assert result["totals"][2]["status"] == "不完整"


def test_large_scale_difference_preserves_decimal_precision():
    from decimal import localcontext
    from app.assistant.compare import normalize, calculate
    tiny = "0." + "0" * 39 + "1"
    mapping = {**MAPPING, "[[PRICE_1]]": "USD " + tiny, "[[PRICE_2]]": "USD 1.00"}
    result = calculate(normalize(data(), mapping, [1, 2]), mapping)
    with localcontext() as ctx:
        ctx.prec = 100
        expected = ((Decimal(1) - Decimal(tiny)) / Decimal(tiny) * Decimal(100)).quantize(Decimal("0.1"))
    assert result["rows"][0]["quotes"][1]["difference_pct"] == expected


def compare_input(sm, tid):
    with sm() as db:
        first = db.scalar(select(TaskFile).where(TaskFile.task_id == uuid.UUID(tid)))
        node = db.scalar(select(DocNode).where(DocNode.file_id == first.id))
        node.text_enc = crypto.encrypt_text("USD 10.00 / USD 1000.00")
        node.redacted_text = "USD [[PRICE_1]] / USD [[AMT_1]]"
        second = TaskFile(task_id=uuid.UUID(tid), seq=2, is_main=False, original_name="虚构第二家.xlsx", ext="xlsx",
                          size_bytes=1, sha256="0" * 64, storage_key=uuid.uuid4().hex, confirmed_at=utcnow())
        db.add(second)
        db.flush()
        db.add(DocNode(task_id=uuid.UUID(tid), file_id=second.id, loc="F2-P1", ord=1, kind="paragraph", part="body",
                       text_enc=crypto.encrypt_text("USD 12.50 / USD 1250.00"),
                       redacted_text="USD [[PRICE_2]] / USD [[AMT_2]]"))
        engine.write_full_map(db, uuid.UUID(tid), MAPPING)
        db.commit()
        return {"template": "compare", "params": {"file_ids": [str(first.id), str(second.id)]}}


@pytest.mark.parametrize("bad", [False, True])
def test_t50_7_10_non_stream_compare_end_to_end(conversation, db_sessionmaker, monkeypatch, bad):
    from app.admin.settings_store import set_setting
    c, h, tid, _ = conversation
    body = compare_input(db_sessionmaker, tid)
    with db_sessionmaker() as db:
        set_setting(db, "model.extra_params", {"stream": True, "response_format": {"type": "text"}}, None)
        db.commit()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json" if bad else json.dumps(data())}}],
                                        "usage": {"prompt_tokens": 12, "completion_tokens": 8}})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 201, response.text
    mid = response.json()["assistant"]["id"]
    run(db_sessionmaker, tid)
    request = json.loads(requests[0].content)
    assert request["stream"] is False and request["response_format"] == {"type": "json_object"}
    with db_sessionmaker() as db:
        reply = db.get(AssistantMessage, mid)
        if bad:
            assert (reply.status, reply.error_code) == ("failed", "BAD_RESPONSE")
            assert (reply.tokens_in, reply.tokens_out) == (12, 8)
        else:
            assert reply.status == "done" and reply.extras["compare"]["rows"]
            stored = json.dumps(reply.extras)
            for price in ("10.00", "12.50", "1000.00", "1250.00"):
                assert price not in stored
                assert all(price not in (call.request_text or "") + (call.response_text or "") for call in db.scalars(select(LlmCall)))
    if not bad:
        items = c.get(f"{BASE}/{tid}/messages").json()["items"]
        comparison = next(m["compare"] for m in items if m["id"] == mid)
        assert comparison["rows"][0]["quotes"][1]["difference_pct"] == "25.0"
        assert comparison["totals"]["1"]["amount"] == "1000.00"
    assert len(requests) == 1


def test_compare_does_not_retry_http_failure(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    body = compare_input(db_sessionmaker, tid)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(503, text="虚构错误")

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 201
    run(db_sessionmaker, tid)
    assert len(requests) == 1
    with db_sessionmaker() as db:
        reply = db.get(AssistantMessage, response.json()["assistant"]["id"])
        assert (reply.status, reply.error_code) == ("failed", "HTTP_ERROR")
