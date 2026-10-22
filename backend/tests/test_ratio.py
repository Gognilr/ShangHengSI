"""金额比例（M4-03，SPEC 9.6，设计 docs/design/M4.md 2.5，Q-72）。全部用 Decimal 断言。"""
from decimal import Decimal

import pytest

from app.redaction import detect
from app.redaction.engine import NodeIn
from app.redaction.ratio import AmountAt, applies, contract_total, insert_position, ratio_text


def amounts_of(nodes: list[NodeIn]) -> list[AmountAt]:
    return [AmountAt(i, h) for i, n in enumerate(nodes) for h in detect.find_amounts(n.text) if h.category == "AMOUNT"]


def total_of(nodes, main_prefix="F1-"):
    return contract_total(nodes, amounts_of(nodes), main_prefix)


# ---------------------------------------------------------------- Hit 的币种与数值


def test_hit_currency_and_value():
    def one(text):
        (h,) = detect.find_amounts(text)
        return h.currency, h.value

    assert one("USD 1,250.50") == ("USD", Decimal("1250.50"))
    assert one("¥ 4671800") == ("CNY", Decimal("4671800"))
    assert one("合同总价 12 万元") == ("CNY", Decimal("120000"))
    assert one("共 3.5 万美元") == ("USD", Decimal("35000"))
    assert one("100 亿元") == ("CNY", Decimal("10000000000"))
    assert one("EUR 99") == ("EUR", Decimal("99"))
    assert one("人民币捌拾万元整") == ("CNY", Decimal("800000"))
    (bare,) = detect.find_amounts("1250", "AMOUNT")
    assert bare.currency is None and bare.value == Decimal("1250")
    # 其他函数不填
    assert detect.find_formatted("13800138000")[0].currency is None
    assert detect.find_formatted("13800138000")[0].value is None


# ---------------------------------------------------------------- 合同总价


@pytest.mark.parametrize("label", [
    "合同总价", "合同总额", "合同金额", "总价", "总金额", "Total Amount", "Total Value", "Contract Value", "Contract Price",
    "Contract Amount", "total amount", "CONTRACT VALUE",
])
def test_total_labels(label):
    nodes = [NodeIn("F1-P1", "USD 100"), NodeIn("F1-P2", f"{label}：USD 1,000.00")]
    assert total_of(nodes) == ("USD", Decimal("1000.00"))


def test_total_label_reach_30_chars():
    near = NodeIn("F1-P1", "合同总价为" + "，" * 20 + "USD 500")
    far = NodeIn("F1-P1", "合同总价为" + "，" * 40 + "USD 500")
    assert total_of([near]) == ("USD", Decimal("500"))
    assert total_of([far]) == ("USD", Decimal("500"))  # 标签不算，退回同币种取最大值，结果相同
    nodes = [NodeIn("F1-P1", "USD 900"), far]
    assert total_of(nodes) == ("USD", Decimal("900"))  # 远处的金额不是合同总价；没有可用标签，取最大的


def test_total_label_takes_first_amount_after_label_in_document_order():
    nodes = [NodeIn("F1-P1", "合同总价 USD 1000，其中预付 USD 300"), NodeIn("F1-P2", "总金额 USD 9999")]
    assert total_of(nodes) == ("USD", Decimal("1000"))


def test_total_label_main_file_first():
    nodes = [NodeIn("F2-P1", "合同总价 USD 8000"), NodeIn("F1-P1", "总价 USD 1000")]
    assert total_of(nodes, "F1-") == ("USD", Decimal("1000"))
    # 主文件中没有标签时再看其他文件
    nodes = [NodeIn("F1-P1", "USD 500"), NodeIn("F2-P1", "合同总价 USD 8000")]
    assert total_of(nodes, "F1-") == ("USD", Decimal("8000"))


def test_total_without_label_same_currency_takes_max():
    nodes = [NodeIn("F1-P1", "预付 USD 300"), NodeIn("F1-P2", "余款 USD 700"), NodeIn("F1-P3", "合计 USD 1,000")]
    assert total_of(nodes) == ("USD", Decimal("1000"))


def test_total_without_label_multiple_currencies_is_unknown():
    nodes = [NodeIn("F1-P1", "预付 USD 300"), NodeIn("F1-P2", "余款 ¥ 5000")]
    assert total_of(nodes) is None


def test_total_ignores_amounts_without_currency_value_or_not_positive():
    nodes = [NodeIn("F1-P1", "合同总价 USD 0"), NodeIn("F1-P2", "USD 0")]
    assert total_of(nodes) is None  # 总价为 0：不加比例
    nodes = [NodeIn("F1-P1", "合同总价 USD 0"), NodeIn("F1-P2", "余款 USD 700")]
    assert total_of(nodes) == ("USD", Decimal("700"))  # 标签处的金额不可用，退回同币种取最大值
    assert contract_total([NodeIn("F1-T1-R1-C1", "100")], [AmountAt(0, detect.find_amounts("100", "AMOUNT")[0])], "F1-") is None
    assert total_of([NodeIn("F1-P1", "没有金额")]) is None
    assert contract_total([], [], None) is None


@pytest.mark.parametrize("text", [
    "合同总价的 30%，即人民币 391,000 元",
    "合同总价的30％，即人民币39万元",
    "软件总价款的 20%，即￥200,000",
    "硬件总价为人民币 200,000 元",
])
def test_total_label_rejects_percent_de_and_sub_item_totals(text):
    nodes = [NodeIn("F1-P1", text), NodeIn("F1-P2", "预付 ¥ 1,000")]
    # 标签不算，退回同币种取最大值（预付 1,000 不会被当作标签处的金额）
    assert total_of(nodes) == ("CNY", max(h.value for h in detect.find_amounts(text) if h.currency == "CNY"))


def test_total_label_accepted_forms():
    nodes = [NodeIn("F1-P1", "软件总价款的 20%，即￥200,000"), NodeIn("F1-P2", "本合同固定总价为：人民币 1,000,000 元"),
             NodeIn("F1-P3", "合同总额 人民币 900,000 元")]
    assert total_of(nodes) == ("CNY", Decimal("1000000"))
    nodes = [NodeIn("F1-P1", "硬件总价 ¥ 50,000"), NodeIn("F1-P2", "本合同总金额：¥ 80,000")]
    assert total_of(nodes) == ("CNY", Decimal("80000"))


def test_total_prices_are_not_counted():
    nodes = [NodeIn("F1-P1", "单价 USD 1250/MT"), NodeIn("F1-P2", "预付 USD 300")]
    assert total_of(nodes) == ("USD", Decimal("300"))


def test_total_main_prefix_none_uses_all_files():
    nodes = [NodeIn("F2-P1", "合同总价 USD 8000"), NodeIn("F1-P1", "总价 USD 1000")]
    assert contract_total(nodes, amounts_of(nodes), None) == ("USD", Decimal("8000"))


# ---------------------------------------------------------------- 比例文字


@pytest.mark.parametrize("value,total,expected", [
    ("300", "1000", "（=合同总价的 30.0%）"),
    ("1", "3", "（=合同总价的 33.3%）"),
    ("2", "3", "（=合同总价的 66.7%）"),
    ("1", "8", "（=合同总价的 12.5%）"),
    ("1", "16", "（=合同总价的 6.3%）"),  # 6.25 → 6.3，ROUND_HALF_UP
    ("5", "1600", "（=合同总价的 0.3%）"),  # 0.3125 → 0.3
    ("1", "800", "（=合同总价的 0.1%）"),  # 0.125 → 0.1
    ("3", "800", "（=合同总价的 0.4%）"),  # 0.375 → 0.4
    ("1", "2000", "（=合同总价的 0.1%）"),  # 0.05 → 0.1（HALF_UP，不是银行家舍入）
    ("1", "4000", "（=合同总价的 0.0%）"),  # 0.025 → 0.0
    ("1500", "1000", "（=合同总价的 150.0%）"),  # 大于 100% 照常写
    ("1000", "1000", "（=合同总价的 100.0%）"),
    ("0.1", "0.3", "（=合同总价的 33.3%）"),  # 不经过浮点数
    ("1000.50", "3000", "（=合同总价的 33.4%）"),  # 33.35 → 33.4
])
def test_ratio_text_half_up(value, total, expected):
    assert ratio_text(Decimal(value), Decimal(total)) == expected


def test_ratio_text_rejects_non_positive_total():
    with pytest.raises(ValueError):
        ratio_text(Decimal("1"), Decimal("0"))
    with pytest.raises(ValueError):
        ratio_text(Decimal("1"), Decimal("-5"))


# ---------------------------------------------------------------- 是否加比例


def hit(text, column=None):
    return detect.find_amounts(text, column)[0]


def test_applies_rules():
    total = ("USD", Decimal("1000"))
    assert applies(hit("USD 300"), total)
    assert not applies(hit("USD 1000"), total)  # 合同总价本身（同币种同数值即同一代号）
    assert not applies(hit("USD 1,000.00"), total)
    assert not applies(hit("¥ 300"), total)  # 币种不同
    assert not applies(hit("USD 1250/MT"), total)  # 单价
    assert not applies(hit("300", "AMOUNT"), total)  # 币种未知（表格金额列的裸数字）
    assert not applies(hit("USD 300"), None)
    assert not applies(hit("USD 300"), ("USD", Decimal("0")))
    assert applies(hit("USD 1500"), total)  # 超过 100% 也加
    assert not applies(hit("USD 300", "PRICE"), total)  # 按列归为单价


def test_applies_chinese_upper_amount():
    total = ("CNY", Decimal("800000"))
    assert applies(hit("人民币贰拾万元整"), total)
    assert not applies(hit("人民币捌拾万元整"), total)


# ---------------------------------------------------------------- 插入位置


def test_insert_position_after_trailing_currency():
    text = "支付 1000 美元 作为预付款"
    h = hit(text)
    assert text[:insert_position(text, h.end)] == "支付 1000 美元"
    text = "支付 1000 USD。"
    h = hit(text)
    assert text[:insert_position(text, h.end)] == "支付 1000 USD"
    text = "支付 12 万元整"
    h = hit(text)
    assert text[:insert_position(text, h.end)] == "支付 12 万元"
    text = "支付 5 亿元"
    assert text[:insert_position(text, hit(text).end)] == "支付 5 亿元"


def test_insert_position_without_trailing_currency():
    text = "USD 300 作为预付款"
    h = hit(text)
    assert insert_position(text, h.end) == h.end
    text = "支付 1000"
    assert insert_position(text, len(text)) == len(text)
    # 币种代码后紧跟字母不是币种
    text = "USD 300 USDX"
    assert insert_position(text, hit(text).end) == hit(text).end
