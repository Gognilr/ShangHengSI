"""报价表各项确定性校验与真实解析样本的精确 manifest。"""
from decimal import Decimal

import pytest

from app.checks.amounts import check_tables, column_kind, find_tables
from app.checks.model import CheckFile, CheckNode, SEVERITY
from app.redaction.recheck import recheck


def nodes(rows, table="F1-T1", start=1):
    return [CheckNode(f"{table}-R{r}-C{c}", str(value), int(table[1:table.index('-')]), None)
            for r, row in enumerate(rows, start) for c, value in enumerate(row, 1) if value is not None]


def run(rows, **kwargs):
    return check_tables(nodes(rows, **kwargs), [CheckFile(1, True, "docx", "quotation", [])])


@pytest.mark.parametrize(("text", "kind"), [
    ("税率", "rate"), ("Tax Rate", "rate"), ("VAT Rate", "rate"), ("税额", "tax"),
    ("VAT", "tax"), ("Tax", "tax"), ("Unit Price", "price"), ("单价", "price"),
    ("Total Price", "amount"), ("Total Amount", "amount"), ("Subtotal", "amount"),
    ("金额", "amount"), ("Price", "price"), ("Qty", "qty"), ("Quantity", "qty"),
    ("不含税金额", "amount_excl"), ("Amount incl VAT", "tax"),
    ("Amount excl.", "amount_excl"), ("Net Amount", "amount_excl"),
    ("含税金额", "amount_incl"), ("Gross Amount", "amount_incl"), ("产品", None),
])
def test_header_priority(text, kind):
    assert column_kind(text) == kind


@pytest.mark.parametrize(("amount", "bad"), [("52,400", True), ("52,000", False), ("52,000.01", False),
                                                  ("52,000.02", True)])
def test_row_amount_and_cent_tolerance(amount, bad):
    tables, results = run([["数量", "单价", "金额"], ["100", "520", amount]])
    assert tables[0].recognized and tables[0].rows_checked == 1
    assert tables[0].row_errors == int(bad)
    assert [r.code for r in results] == (["L-01"] if bad else [])
    if bad:
        assert results[0].locs == ["F1-T1-R2-C3", "F1-T1-R2-C1", "F1-T1-R2-C2"]
        assert "52,000.00" in results[0].detail


def test_product_rounding_and_total_include_rounded_rows():
    tables, results = run([["数量", "单价", "金额"], ["3", "0.335", "1.005"], ["2", "0.335", "0.67"],
                           ["合计", "", "1.68"], ["99", "99", "0"]])
    assert not results and tables[0].total_ok is True and tables[0].rows_checked == 2


@pytest.mark.parametrize(("total", "codes"), [("30", []), ("30.01", []), ("30.02", ["L-02"])])
def test_total_check(total, codes):
    tables, results = run([["项目", "数量", "单价", "金额"], ["甲", "2", "5", "10"],
                           ["小计", "4", "5", "20"], ["", "合计", "", total], ["忽略", "9", "9", "0"]])
    assert [r.code for r in results] == codes
    assert tables[0].total_ok == (not codes)
    if codes:
        assert results[0].locs == ["F1-T1-R4-C4"]


def test_missing_amount_disables_total_and_does_not_hide_bad_row():
    tables, results = run([["数量", "单价", "金额"], ["100", "520", "待定"], ["合计", "", "0"]])
    assert tables[0].rows_skipped == [2] and tables[0].total_ok is None
    assert [r.code for r in results] == ["L-INFO"]
    assert "第 2 行" in results[0].detail


def test_no_total_is_not_a_failed_total_check():
    tables, results = run([["数量", "单价", "金额"], ["2", "5", "10"]])
    assert not results and tables[0].total is None and tables[0].total_ok is None


@pytest.mark.parametrize(("tax", "gross", "count"), [("1300", "11300", 0), ("1200", "11200", 1),
                                                            ("1300", "11400", 1), ("1200", "11300", 2)])
def test_tax_and_gross_checks(tax, gross, count):
    _, results = run([["数量", "单价", "不含税金额", "税率", "税额", "含税金额"],
                      ["10", "1000", "10000", "13%", tax, gross]])
    assert [r.code for r in results] == ["L-03"] * count
    assert all(r.locs in (["F1-T1-R2-C5"], ["F1-T1-R2-C6"]) for r in results)


def test_tax_on_total_and_rate_spellings():
    _, results = run([["项目", "数量", "单价", "金额", "税率", "税额"],
                      ["甲", "2", "5000", "10000", "0.13", "1300"],
                      ["Total", "", "", "10000", "13", "1200"]])
    assert [r.code for r in results] == ["L-03"]
    assert results[0].locs == ["F1-T1-R3-C6"]


def test_incomplete_structure_and_header_scan_bounds():
    tables, results = run([["单价", "金额"], ["520", "52000"]])
    assert not tables[0].recognized and [r.code for r in results] == ["L-INFO"]
    assert results[0].locs == ["F1-T1-R1-C1"]
    assert run([["数量", "单价", "金额"], ["1", "1", "1"]], start=20)[0][0].recognized
    tables, results = run([["数量", "单价", "金额"], ["1", "1", "1"]], start=21)
    assert not tables and [r.code for r in results] == ["L-INFO"] and not results[0].locs


def test_complete_header_preferred_to_earlier_incomplete_and_leftmost_column():
    n = nodes([["单价", "", ""], ["数量", "单价", "金额", "金额"], ["2", "5", "10", "99"]])
    tables, results = check_tables(n, [CheckFile(1, True, "docx", None, [])])
    assert tables[0].recognized and not results


def test_skip_list_limited_and_formula_warning_only_xlsx():
    n = nodes([["数量", "单价", "金额"]] + [["待定", "520", "52000"]] * 12)
    files = [CheckFile(1, True, "xlsx", "quotation", ["含未计算的公式，相关单元格未读取"])]
    from app.parsing.xlsx import WARN_FORMULA
    files[0].warnings[:] = [WARN_FORMULA]
    _, results = check_tables(n, files)
    assert len(results) == 2 and "等 12 行" in results[0].detail
    assert "第 2、3、4、5、6、7、8、9、10、11 行" in results[0].detail
    assert results[1].locs == [] and "含未计算的公式" in results[1].detail
    assert check_tables([], [CheckFile(1, True, "docx", "contract", [WARN_FORMULA])]) == ([], [])


def test_xlsx_columns_sort_numerically_and_currency_falls_back_to_header():
    n = [CheckNode(f"F2-S1-{col}{row}", text, 2, None) for row, entries in [
        (1, [("AA", "数量"), ("AB", "单价 USD"), ("AC", "金额"), ("B", "项目")]),
        (2, [("AA", "2"), ("AB", "5"), ("AC", "11"), ("B", "虚构产品")]),
    ] for col, text in entries]
    tables, results = check_tables(n, [CheckFile(2, False, "xlsx", "quotation", [])])
    assert tables[0].currency == "USD" and tables[0].table == "F2-S1"
    assert results[0].locs[0] == "F2-S1-AC2"
    assert find_tables(n) == tables


def test_unparseable_cells_and_missing_structure_are_explicit():
    _, results = run([["数量", "单价", "金额"], ["1", "无法识别", "待定"]])
    assert [r.code for r in results] == ["L-INFO"]
    assert "第 2 行" in results[0].ai_summary
    assert check_tables(nodes([["备注", "日期"]]), [CheckFile(1, True, "docx", "contract", [])]) == ([], [])


def test_currency_from_values_precedes_header_and_total_requires_whole_word():
    tables, results = run([["项目", "数量", "单价 EUR", "金额 EUR"],
                           ["Totally fictional", "1", "USD 5", "USD 5"],
                           ["Subtotal", "1", "5", "5"], ["Total", "", "", "USD 10"]])
    assert not results and tables[0].currency == "USD" and tables[0].rows_checked == 2
    assert tables[0].total_ok is True


def test_incomplete_qty_amount_table_is_only_a_hint_and_unknown_tax_is_not_guessed():
    tables, results = run([["数量", "金额"], ["2", "10"], ["合计", "9"]])
    assert not tables[0].recognized and [r.code for r in results] == ["L-INFO"]
    _, results = run([["数量", "单价", "金额", "税率", "税额"], ["2", "5", "10", "待定", "20"]])
    assert not results


def test_summary_has_only_fixed_text_positions_and_percentages():
    _, results = run([["项目", "数量", "单价", "金额", "税率", "税额"],
                      ["星河虚构进出口有限公司", "100", "USD 520", "USD 52400", "13%", "6700"],
                      ["合计", "", "", "USD 50000", "13%", "6400"]])
    for r in results:
        assert r.severity == SEVERITY[r.code]
        assert recheck(r.ai_summary, real_values=["星河虚构进出口有限公司", "520", "52400", "6700", "50000"], terms=[]) == []
        assert not any(x in r.ai_summary for x in ("52,400.00", "52400", "52,000.00", "52000", "6,700.00", "6700", "USD 520"))


def test_a01_fixture_manifest_matches_all_results_exactly():
    from collections import Counter
    from pathlib import Path

    import yaml

    from app.parsing.docx import parse_docx
    from app.parsing.pdf import parse_pdf
    from app.parsing.xlsx import parse_xlsx

    root = Path(__file__).parent / "fixtures" / "checks"
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    for entry in manifest["samples"]:
        path = root / entry["file"]
        parser = {".xlsx": parse_xlsx, ".docx": parse_docx, ".pdf": parse_pdf}[path.suffix]
        parsed = parser(path.read_bytes(), 1)
        ns = [CheckNode(n.loc, n.text, 1, n.clause_no) for n in parsed.nodes if n.part == "body"]
        _, results = check_tables(ns, [CheckFile(1, True, path.suffix[1:], entry["doc_type"], parsed.warnings)])
        assert Counter((r.code, tuple(r.locs)) for r in results) == Counter(
            (x["code"], tuple(x["locs"])) for x in entry["expected"] if x["code"] in {"L-01", "L-02", "L-03", "L-INFO"}), path.name
        from app.redaction.engine import NodeIn, compute
        values = list(compute([NodeIn(n.loc, n.text, n.clause_no) for n in ns], "sales").mapping.values())
        for r in results:
            assert recheck(r.ai_summary, real_values=values, terms=[]) == [], (path.name, r.ai_summary)
            # 只在 detail 的金额格式片段中取数，避免把允许出现的行号误当作绝对金额。
            import re
            for value in re.findall(r"[\d,]+\.\d{2}(?!\d)", r.detail):
                assert value not in r.ai_summary and value.replace(",", "") not in r.ai_summary


@pytest.mark.parametrize(("text", "count"), [
    ("人民币850000元，大写捌拾万元整", 1), ("人民币800000元，大写捌拾万元整", 0),
    ("人民币80万元，大写捌拾万元整", 0), ("大写捌拾万元整，其他条款", 0),
    ("大写捌拾万元整，人民币800000元；保证金人民币900000元", 0),
    ("人民币800000.01元，大写捌拾万元整", 1),
])
def test_t05_uppercase_amounts(text, count):
    from app.checks.amounts import check_uppercase
    results = check_uppercase([CheckNode("F1-P1", text, 1, None)])
    assert len(results) == count
    for r in results:
        assert r.code == "L-04" and r.locs == ["F1-P1"]
        assert recheck(r.ai_summary, real_values=["800000", "850000", "捌拾万元整"], terms=[]) == []


def test_uppercase_only_uses_immediate_same_row_cells():
    from app.checks.amounts import check_uppercase
    ns = nodes([["850000", "捌拾万元整", "700000"]])
    results = check_uppercase(ns)
    assert len(results) == 1 and "850,000.00" in results[0].detail
    assert not check_uppercase(nodes([["850000"], ["捌拾万元整"]]))
