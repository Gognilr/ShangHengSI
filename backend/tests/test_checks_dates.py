"""确定性日期含义与边界；不依赖宿主时钟。"""
from datetime import date
from pathlib import Path

import pytest
import yaml

from app.checks.dates import check_dates, find_dates, parse_date
from app.checks.model import CheckNode
from app.redaction.recheck import recheck


@pytest.mark.parametrize("text", ["2026年10月10日", "2026-10-10", "2026/10/10", "2026.10.10", "10 Oct 2026",
                                 "10 October 2026", "Oct 10, 2026", "October 10, 2026", "Oct. 10, 2026", "10 OCT 2026"])
def test_date_spellings(text):
    assert parse_date(text) == date(2026, 10, 10)


@pytest.mark.parametrize("text", ["10/11/2026", "2026-02-30", "2026/13/10", "2026-10/10", "2026-10-100",
                                 "12026-10-10", "2026", "签约后60天", "Oct 32, 2026", "2026-10-10 2026-10-11"])
def test_uncertain_or_invalid_date(text):
    assert parse_date(text) is None


def test_offsets_and_leap_year():
    text = "日期：2024-02-29，另一个日期 2026年10月10日。"
    found = find_dates(text)
    assert [text[start:end] for start, end, _ in found] == ["2024-02-29", "2026年10月10日"]
    assert parse_date("2024-02-29") == date(2024, 2, 29)
    assert parse_date("2026-02-29") is None


def ns(*texts):
    return [CheckNode(f"F1-P{i}", text, 1, None) for i, text in enumerate(texts, 1)]


@pytest.mark.parametrize("today,codes", [(date(2026, 9, 29), []), (date(2026, 9, 30), []), (date(2026, 10, 1), ["L-07"])])
def test_expiry_compares_injected_today(today, codes):
    results = check_dates(ns("报价有效期截止2026-09-30"), today)
    assert [r.code for r in results] == codes


def test_delivery_and_ambiguous_signing_dates():
    data = ns("签订日期：2026-09-15", "交货日期：2026-09-01")
    results = check_dates(data, date(2026, 10, 20))
    assert [r.code for r in results] == ["L-08"] and results[0].locs == ["F1-P2", "F1-P1"]
    assert not check_dates(data + ns("签订日期：2026-09-16"), date(2026, 10, 20))
    assert len(check_dates(data + ns("签订日期：2026-09-15"), date(2026, 10, 20))) == 1
    assert not check_dates(ns("交货日期：2026-09-01"), date(2026, 10, 20))
    assert not check_dates(ns("签订日期2026-09-15", "交货日期2026-09-15"), date(2026, 10, 20))


def test_relative_period_and_next_label_do_not_borrow_date():
    assert not check_dates(ns("报价有效期30天，签订日期2026-09-01"), date(2026, 10, 20))
    assert not check_dates(ns("有效至：详见附件，签订日期2026-09-01"), date(2026, 10, 20))
    assert not check_dates(ns("有效至：" + "甲" * 31 + "2026-09-01"), date(2026, 10, 20))
    assert check_dates(ns("有效至：" + "甲" * 28 + "2026-09-01"), date(2026, 10, 20))


def test_date_fixture_manifest_and_safe_summary():
    from app.parsing.docx import parse_docx

    root = Path(__file__).parent / "fixtures" / "checks"
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    for entry in manifest["samples"]:
        path = root / entry["file"]
        if path.suffix != ".docx":
            continue
        parsed = parse_docx(path.read_bytes(), 1)
        results = check_dates([CheckNode(n.loc, n.text, 1, n.clause_no) for n in parsed.nodes if n.part == "body"], date(2026, 10, 20))
        assert [{"code": r.code, "locs": r.locs} for r in results] == [
            r for r in entry["expected"] if r["code"] in ("L-07", "L-08")]
        for result in results:
            assert result.detail == result.ai_summary
            assert recheck(result.ai_summary, real_values=["800000", "星河虚构进出口有限公司"], terms=[]) == []
