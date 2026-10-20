"""合同与附件比较；所有金额与主体均为虚构。"""
from decimal import Decimal

import pytest

from app.checks.amounts import find_tables
from app.checks.consistency import check_consistency, contract_amount
from app.checks.model import CheckFile, CheckNode
from app.redaction.recheck import recheck


def example(total="850000", currency="CNY", *, table_seq=2):
    files = [CheckFile(1, True, "docx", "contract", []), CheckFile(2, False, "xlsx", "quotation", [])]
    ns = [CheckNode("F1-P1", "合同总价：人民币 800000 元", 1, None)]
    for row, values in enumerate([["数量", "单价", f"金额 {currency}"], ["1", total, total], ["合计", "", total]], 1):
        ns += [CheckNode(f"F{table_seq}-S1-{col}{row}", text, table_seq, None)
               for col, text in zip("ABC", values)]
    return ns, files


@pytest.mark.parametrize(("amount", "currency", "codes"), [
    ("850000", "CNY", ["L-05"]), ("800000", "CNY", []), ("800000.01", "CNY", []),
    ("800000.02", "CNY", ["L-05"]), ("800000", "USD", ["L-06"]), ("850000", "USD", ["L-06"]),
    ("850000", "", ["L-05"]),
])
def test_cross_file_amounts_and_currency(amount, currency, codes):
    ns, files = example(amount, currency)
    results = check_consistency(ns, files, find_tables(ns))
    assert [r.code for r in results] == codes
    for r in results:
        assert r.locs == ["F1-P1", "F2-S1-C3"]
        assert recheck(r.ai_summary, real_values=["800000", "850000"], terms=[]) == []
        assert not any(x in r.ai_summary for x in ("800000", "850000", "800,000", "850,000"))
    if currency == "":
        assert "附件报价表币种未识别" in results[0].detail


def test_only_main_file_supplies_contract_total_and_own_table_is_not_attachment():
    ns, files = example(table_seq=1)
    assert check_consistency(ns, files, find_tables(ns)) == []
    ns, files = example()
    ns[0] = CheckNode("F1-P1", "虚构合同，没有明确金额", 1, None)
    ns.append(CheckNode("F2-P1", "合同总价 USD 800000", 2, None))
    assert contract_amount(ns, files) is None
    assert check_consistency(ns, files, find_tables(ns)) == []


def test_contract_total_reuses_label_priority_and_unit_multiplier():
    files = [CheckFile(1, True, "docx", "contract", [])]
    ns = [CheckNode("F1-P1", "保证金 USD 999999", 1, None),
          CheckNode("F1-P2", "合同总价人民币80万元", 1, None)]
    assert contract_amount(ns, files) == ("F1-P2", "CNY", Decimal("800000"))


def test_cross_file_fixture_manifest_exact():
    from pathlib import Path
    import yaml
    from app.parsing.docx import parse_docx
    from app.parsing.xlsx import parse_xlsx

    root = Path(__file__).parent / "fixtures" / "checks"
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    for case in manifest["consistency_cases"]:
        ns, files = [], []
        for seq, name in enumerate(case["files"], 1):
            path = root / name
            parsed = (parse_docx if path.suffix == ".docx" else parse_xlsx)(path.read_bytes(), seq)
            ns += [CheckNode(n.loc, n.text, seq, n.clause_no) for n in parsed.nodes if n.part == "body"]
            files.append(CheckFile(seq, seq == 1, path.suffix[1:], "contract" if seq == 1 else "quotation", parsed.warnings))
        results = check_consistency(ns, files, find_tables(ns))
        assert [{"code": r.code, "locs": r.locs} for r in results] == case["expected"]


def test_generated_uppercase_fixture_expected():
    from pathlib import Path
    from app.checks.amounts import check_uppercase
    from app.parsing.docx import parse_docx

    root = Path(__file__).parent / "fixtures" / "checks"
    for name, count in (("uppercase_error.docx", 1), ("uppercase_ok.docx", 0)):
        parsed = parse_docx((root / name).read_bytes(), 1)
        results = check_uppercase([CheckNode(n.loc, n.text, 1, n.clause_no) for n in parsed.nodes if n.part == "body"])
        assert len(results) == count
