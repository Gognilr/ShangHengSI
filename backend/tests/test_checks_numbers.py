"""本地校验的 Decimal 数字边界，全部数据虚构。"""
from decimal import Decimal, ROUND_DOWN, localcontext

import pytest

from app.checks.numbers import money, parse_number, parse_rate, pct_diff


@pytest.mark.parametrize(("text", "expected"), [
    ("1,234.50", "1234.50"), ("USD 1,234.50", "1234.50"), ("$1,234.50", "1234.50"),
    ("100 吨", "100"), ("520/MT", "520"), ("-12.5", "-12.5"), ("+12.5", "12.5"),
    ("人民币 12.50 元", "12.50"), ("HK$ 5,210.50", "5210.50"), ("eur 100", "100"),
    ("０．５０", "0.50"), (".5", "0.5"), ("0", "0"), (" 1 234.50 ", "1234.50"),
])
def test_single_number(text, expected):
    assert parse_number(text) == Decimal(expected)


@pytest.mark.parametrize("text", [
    "", "N/A", "待定", "100-120", "1,234.50 / 1,300.00", "12.3.4", "1e3", "NaN", "Infinity",
    "12,34", "100 及 120", "1/2", "(100)", "100万元", "13%", "2026-10-20", "--12", "12-",
])
def test_uncertain_number_is_not_guessed(text):
    assert parse_number(text) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("13%", "0.13"), ("0.13", "0.13"), ("13", "0.13"), ("1", "1"), ("0", "0"),
    ("100%", "1"), ("０．１３", "0.13"), (" 13 % ", "0.13"),
])
def test_rates(text, expected):
    assert parse_rate(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "N/A", "13-15%", "13% 或 9%", "-13%", "USD 13", "13吨", "NaN"])
def test_uncertain_rates(text):
    assert parse_rate(text) is None


def test_formatting_and_ratio_use_half_up_independently_of_context():
    with localcontext() as ctx:
        ctx.rounding = ROUND_DOWN
        assert money(Decimal("52400.005")) == "52,400.01"
        assert pct_diff(Decimal("101.25"), Decimal("100")) == "1.3%"
        assert pct_diff(Decimal("52400"), Decimal("52000")) == "0.8%"
        assert pct_diff(Decimal("99.99"), Decimal("100")) == "不足 0.1%"
        assert pct_diff(Decimal("-101"), Decimal("-100")) == "1.0%"
        assert pct_diff(Decimal("1"), Decimal("0")) == "无法按比例计算"
