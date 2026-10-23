"""R4-02：设计 3.5；第 1、10 条由完整 golden 与现有识别测试共同锁定。"""
from decimal import Decimal

import pytest

from app.chat.service import _redact
from app.redaction import detect, tables
from app.redaction.engine import NodeIn, compute


@pytest.mark.parametrize("text,surface,category,value,currency", [
    ("合同总价：125000，分两期支付。", "125000", "AMOUNT", "125000", None),
    ("预计采购额约 350万。", "350", "AMOUNT", "3500000", None),
    ("合同金额（元）：86,400.00", "86,400.00", "AMOUNT", "86400.00", "CNY"),
    ("单价：1280", "1280", "PRICE", "1280", None),
])
def test_r4_bare_amount_hits(text, surface, category, value, currency):
    hits = detect.find_bare_amounts(text)
    assert len(hits) == 1
    hit = hits[0]
    assert (text[hit.start:hit.end], hit.category, hit.value, hit.currency) == (surface, category, Decimal(value), currency)


@pytest.mark.parametrize("text", [
    "合计：12 台", "预付款 30%", "第 3 条 金额以本合同为准", "金额：5", "交货期：60 天", "质保期：12 个月",
    "金额：125000天", "金额：125000 kg", "金额：125000 %", "第金额：125000", "2金额：125000", "总价：\n125000",
])
def test_r4_not_bare_amounts(text):
    assert detect.find_bare_amounts(text) == []


def test_r4_currency_amount_is_not_duplicated():
    text = "合同总价：人民币 125,000.00 元"
    assert detect.find_bare_amounts(text) == []
    r = compute([NodeIn("F1-P1", text)], "sales")
    assert len(r.items) == 1


@pytest.mark.parametrize("loc1,loc2", [("F1-T1-R1-C1", "F1-T1-R1-C2"), ("F1-S1-Z1", "F1-S1-AA1")])
def test_r4_left_cell_label(loc1, loc2):
    nodes = [NodeIn(loc1, "合同总价（元）"), NodeIn(loc2, "125000")]
    assert tables.table_hints(nodes) == {1: "AMOUNT"}
    r = compute(nodes, "sales")
    assert r.redacted[loc2] == "[金额_1]"
    assert tables.table_hints([NodeIn(loc1, "交货期（天）"), NodeIn(loc2, "60")]) == {}


def test_r4_chat_uses_bare_amounts():
    r = _redact("合同总价：125000 是否合理？", {}, [])
    assert r.text == "合同总价：[金额_Q1] 是否合理？"


def test_r4_find_amounts_keeps_existing_contract():
    assert detect.find_amounts("合同总价：125000") == []


def test_r4_table_scale_and_adjacency():
    nodes = [NodeIn("F1-S1-Z1", "合同金额"), NodeIn("F1-S1-AA1", "350万")]
    assert tables.table_hints(nodes) == {1: "AMOUNT"}
    r = compute(nodes, "sales")
    assert r.redacted["F1-S1-AA1"] == "[金额_1]万"
    assert detect.find_amounts("350万", "AMOUNT")[0].value == Decimal("3500000")
    assert tables.table_hints([NodeIn("F1-S1-Z1", "合同金额"), NodeIn("F1-S1-AB1", "125000")]) == {}


def test_r4_same_value_currency_and_ratio():
    nodes = [NodeIn("F1-P1", "合同总价：125000"), NodeIn("F1-P2", "合同总价：125000"),
             NodeIn("F1-P3", "合同总价：USD 125000")]
    r = compute(nodes, "sales", main_prefix="F1-")
    assert r.redacted["F1-P1"] == r.redacted["F1-P2"] == "合同总价：[金额_1]"
    assert r.redacted["F1-P3"] == "合同总价：USD [金额_2]"


@pytest.mark.parametrize("label", ["合同总价", "单价", "Total Amount", "Unit Price", "Contract Value", "Deposit"])
@pytest.mark.parametrize("unit,cur", [("万元", "CNY"), ("人民币", "CNY"), ("RMB", "CNY"), ("USD", "USD")])
def test_r4_unit_notes(label, unit, cur):
    text = f"{label}（{unit}）：【125000】"
    hits = detect.find_bare_amounts(text)
    assert len(hits) == 1 and hits[0].currency == cur
    assert text[hits[0].start:hits[0].end] == "125000"
