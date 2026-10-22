"""M6-09：A-01 报价表金额校验准确率。

对本地校验样本、其他解析样本和脱敏回归集逐份解析，在识别出的每张报价表上用独立的
Decimal 计算逐行重算“数量×单价”和合计，与 L-01 / L-02 的报告逐条比较：
该报的全部报出、不该报的一条不报，无法识别的数字行必须列为未核验。
"""
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from app.checks.amounts import _scan_tables
from app.checks.model import CheckFile, CheckNode
from app.checks.service import evaluate
from app.tasks.pipeline import _parse_bytes
from tests.acceptance_support import fixture_samples, golden_samples

CHECKS = Path(__file__).parent / "fixtures" / "checks"
CENT = Decimal("0.01")
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _samples() -> list[tuple[str, bytes]]:
    own = [(f.name, f.read_bytes()) for f in sorted(CHECKS.iterdir()) if f.suffix in (".docx", ".pdf", ".xlsx")]
    return own + [(s.name, s.data) for s in fixture_samples() + golden_samples()]


def _num(text: str) -> Decimal | None:
    """独立的金额读取：单元格里恰好一个数字（可带币种、千分位）才算可读。"""
    found = _NUM.findall(text)
    return Decimal(found[0].replace(",", "")) if len(found) == 1 else None


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _row_of(loc: str, table) -> int:
    return next(r for r, cells in table.rows.items() if any(n.loc == loc for n in cells.values()))


@pytest.fixture(scope="module")
def parsed():
    out = []
    for name, data in _samples():
        result, _ = _parse_bytes(name.rsplit(".", 1)[-1].lower(), data, 1, 0)
        nodes = [CheckNode(n.loc, n.text, 1, n.clause_no) for n in result.nodes if n.part == "body"]
        out.append((name, nodes, CheckFile(1, True, name.rsplit(".", 1)[-1].lower(), "quotation", result.warnings)))
    return out


def test_a01_every_recognized_quote_row_recomputed(parsed):
    stats = {"tables": 0, "rows": 0, "row_errors": 0, "totals": 0, "total_errors": 0, "skipped": 0}
    for name, nodes, file in parsed:
        results = evaluate(nodes, [file], date(2026, 10, 20)).results
        for table in (t for t in _scan_tables(nodes) if t.recognized):
            stats["tables"] += 1
            reported_rows = {_row_of(r.locs[1], table) for r in results
                             if r.code == "L-01" and r.locs and r.locs[0].startswith(table.name + "-")}
            expected_rows, unreadable, amounts = set(), [], []
            for row in table.data_rows:
                qty, price, amount = (_num(table.text(row, k)) for k in ("qty", "price", table.amount))
                amounts.append(amount)
                if qty is None or price is None or amount is None:
                    unreadable.append(row)
                    continue
                stats["rows"] += 1
                if abs(_cents(amount) - _cents(qty * price)) > CENT:
                    expected_rows.add(row)
            assert reported_rows == expected_rows, (name, table.name)
            stats["row_errors"] += len(expected_rows)
            stats["skipped"] += len(unreadable)
            info = " ".join(r.detail for r in results if r.code == "L-INFO")
            for row in unreadable:
                assert re.search(rf"{table.name} 第 [\d、]*\b{row}\b", info), (name, table.name, row)
            total_reported = any(r.code == "L-02" and r.locs[0].startswith(table.name + "-") for r in results)
            if table.total_row is not None and amounts and None not in amounts:
                total = _num(table.text(table.total_row, table.amount))
                if total is not None:
                    stats["totals"] += 1
                    wrong = abs(_cents(total) - sum((_cents(a) for a in amounts), Decimal(0))) > CENT
                    stats["total_errors"] += wrong
                    assert total_reported == wrong, (name, table.name)
                    continue
            assert not total_reported, (name, table.name)
    # 样本必须真的覆盖到报价表、错行和错合计，否则“准确率 100%”没有意义。
    assert stats["tables"] >= 8 and stats["row_errors"] >= 2 and stats["total_errors"] >= 1, stats
    print("A-01", stats)
