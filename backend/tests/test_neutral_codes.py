"""T-48：中性代号、稳定编号及审核默认行为不变。"""
import re

import pytest

from app.redaction import engine, placeholders
from app.redaction.engine import NodeIn, PartyOverride
from app.redaction.parties import normalize
from app.redaction.restore import restore


@pytest.fixture(autouse=True)
def fake_ner(monkeypatch):
    monkeypatch.setattr(engine.ner, "recognize", lambda text: [])


def neutral(nodes, **kw):
    return engine.compute(nodes, "sales", style="neutral", **kw)


def test_t48_2_3_neutral_unique_roundtrip_and_no_ratios():
    nodes = [NodeIn("F1-P1", "卖方：虚构晨曦科技有限公司", None),
             NodeIn("F1-P2", "买方：虚构星河贸易有限公司", None),
             NodeIn("F1-P3", "电话：13800138001；电话：13900139002", None),
             NodeIn("F1-P4", "地址：北京市朝阳区虚构路123号", None),
             NodeIn("F1-P5", "地址：上海市浦东新区示例路456号", None),
             NodeIn("F1-P6", "合同总价人民币100000元，首付款人民币30000元", None)]
    result = neutral(nodes)
    assert "[[OUR]]" in result.redacted["F1-P1"]
    assert "[[ORG_1]]" in result.redacted["F1-P2"]
    assert "[[TEL_1]]" in result.redacted["F1-P3"] and "[[TEL_2]]" in result.redacted["F1-P3"]
    assert "[[ADDR_1]]" in result.redacted["F1-P4"] and "[[ADDR_2]]" in result.redacted["F1-P5"]
    assert not any("%" in text for text in result.redacted.values())
    assert all("#" not in code or code.split("#")[0] in result.mapping for code in result.mapping)
    for node in nodes:
        assert restore(result.redacted[node.loc], result.mapping) == node.text


def test_t48_4_amount_shared_across_files():
    result = neutral([NodeIn("F1-P1", "金额：人民币100000元", None),
                      NodeIn("F2-P1", "金额：人民币100000元，另付人民币200000元", None)])
    first = re.findall(r"\[\[AMT_\d+\]\]", result.redacted["F1-P1"])
    second = re.findall(r"\[\[AMT_\d+\]\]", result.redacted["F2-P1"])
    assert first == ["[[AMT_1]]"] and second == ["[[AMT_1]]", "[[AMT_2]]"]


def test_t48_5_6_pinned_codes_survive_new_order_and_disappearance():
    a = NodeIn("F1-P1", "买方：虚构星河贸易有限公司；电话：13800138001", None)
    first = neutral([a])
    b = NodeIn("F2-P1", "虚构山川科技有限公司与虚构星河贸易有限公司合作；电话：13900139002", None)
    second = neutral([b, a], pinned=first.mapping)
    assert second.redacted[a.loc] == first.redacted[a.loc]
    assert "[[ORG_2]]" in second.redacted[b.loc] and "[[ORG_1]]" in second.redacted[b.loc]
    removed = neutral([], pinned=second.mapping)
    assert removed.mapping == second.mapping
    from app.redaction.recheck import recheck
    assert recheck("虚构星河贸易有限公司", real_values=list(removed.mapping.values()), terms=[])


def test_t48_7_change_org_to_our_keeps_historical_mapping():
    text = "买方：虚构星河贸易有限公司"
    node = NodeIn("F1-P1", text, None)
    first = neutral([node])
    result = neutral([node], pinned=first.mapping,
                     overrides={normalize("虚构星河贸易有限公司"): PartyOverride(True, "中国", "企业", True)})
    assert "[[OUR]]" in result.redacted[node.loc]
    assert result.mapping["[[ORG_1]]"] == result.mapping["[[OUR]]"] == "虚构星河贸易有限公司"


def test_t48_8_9_question_allocator_injection_and_review_unchanged():
    from app.chat.service import _redact
    full_map = {"[[ORG_4]]": "虚构星河贸易有限公司"}
    alloc = placeholders.NeutralAllocator()
    alloc.seed(full_map)
    text = "虚构山川科技有限公司向虚构山川科技有限公司发函"
    result = _redact(text, full_map, [], allocator=alloc)
    assert result.text.count("[[ORG_5]]") == 2
    assert result.new_entries == {"[[ORG_5]]": "虚构山川科技有限公司"}
    review = _redact(text, {}, [])
    assert review.text.count("[公司_Q1]") == 2
    assert review.new_entries == {"[公司_Q1]": "虚构山川科技有限公司"}


@pytest.mark.parametrize("code", ["[[OUR]]"] + [f"[[{label}_12]]" for label in (
    "ORG", "PER", "PRJ", "REF", "TEL", "EMAIL", "URL", "ZIP", "ACCT", "SWIFT", "AMT", "PRICE", "QTY", "TERM", "IM", "ID", "ADDR")])
def test_t48_10_neutral_regex(code):
    assert placeholders.NEUTRAL.fullmatch(code)


@pytest.mark.parametrize("code", ["[公司_Q1]", "[金额_1]", "[[ORG_]]"])
def test_t48_10_not_neutral(code):
    assert not placeholders.NEUTRAL.fullmatch(code)


def test_neutral_fixed_values_and_category_boundaries():
    alloc = placeholders.NeutralAllocator()
    alloc.seed({"[[TEL_3]]": "13800138001", "[[ORG_2]]": "Example Inc.", "[[ORG_2]]#1": "EXAMPLE INC."})
    assert alloc.fixed("PHONE", "13800138001") == "[[TEL_3]]"
    assert alloc.fixed("PHONE", "13900139002") == "[[TEL_4]]"
    assert alloc.org("example inc.", "买方") == "[[ORG_2]]"
    assert alloc.numbered("CUSTOM", "example inc.") == "[[TERM_1]]"
