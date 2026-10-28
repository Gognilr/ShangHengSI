"""T-50：仅接受价格代号，本地 Decimal 计算；结果结构不保存价格真实值。"""
import json
from decimal import Decimal

import pytest

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
