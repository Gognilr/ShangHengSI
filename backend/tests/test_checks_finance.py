"""E-03 财务校验的代表样本：故意错误、正确报表与两行 DOCX 表头。"""
import importlib.util
from pathlib import Path
from decimal import Decimal

import pytest
from docx import Document
import io

from tests.review_support import env, ready_task  # noqa: F401
from app.parsing.xlsx import parse_xlsx
from app.parsing.docx import parse_docx
from app.checks.model import CheckNode, CheckFile
from app.checks.service import evaluate
from app.checks.formula import evaluate_formula, UnsupportedFormula
from datetime import date

spec = importlib.util.spec_from_file_location('financial_fixture', Path(__file__).parent / 'fixtures/checks/make_financial_fixture.py')
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)


def inputs(parsed):
    return [CheckNode(n.loc, n.text, 1, n.clause_no, n.hidden_spans, n.formula, n.sheet, n.cell_ref) for n in parsed.nodes]


def test_financial_workbook_and_correct_guard():
    parsed = parse_xlsx((Path(__file__).parent / 'fixtures/checks/financial.xlsx').read_bytes(), 1)
    results = evaluate(inputs(parsed), [CheckFile(1, True, 'xlsx', None, [])], date(2026, 11, 2)).results
    assert [(r.code, r.locs[0]) for r in results if r.code in ('L-13', 'L-15', 'L-16')] == [
        ('L-13', 'F1-S1-B2'), ('L-15', 'F1-S4-D3'), ('L-16', 'F1-S4-D5')]
    l14 = [r for r in results if r.code == 'L-14']
    assert any(r.locs == ['F1-S4-C8'] for r in l14)
    assert any(r.locs == ['F1-S4-D5'] for r in l14)
    assert any(r.code == 'L-INFO' and r.locs == ['F1-S4-F3'] for r in results)
    assert all('102' not in r.ai_summary and '=SUM' not in r.ai_summary for r in results)
    correct = parse_xlsx(fixture.make_workbook(False), 1)
    assert evaluate(inputs(correct), [CheckFile(1, True, 'xlsx', None, [])], date(2026, 11, 2)).results == []


def test_financial_ratios():
    from app.checks.ratios import calculate
    parsed = parse_xlsx(fixture.make_workbook(False), 1)
    text = calculate(inputs(parsed))
    assert '毛利率：本期 40.0%' in text
    assert '期间费用率：本期 15.0%' in text
    assert '资产负债率：本期 60.0%（上期 62.5%）' in text
    assert '净利率：本期 5.0%' in text
    assert '经营现金流与净利润之比' not in text  # 样本未明确跨表金额单位，不猜比例尺。
    assert '流动比率' not in text  # 未提供的科目不猜零。
    from dataclasses import replace
    nodes = inputs(parsed)
    nodes = [replace(n, text=n.text + '（人民币元）') if n.loc in ('F1-S2-B1', 'F1-S3-B1') else n for n in nodes]
    assert '经营现金流与净利润之比：本期 2.00倍' in calculate(nodes)
    # V31Q-68：简短期间列头“期末 / 期初”“本期 / 上期”同样识别报表。
    short = parse_xlsx((Path(__file__).parent / 'fixtures/eval_fidelity/cn_04_finance.xlsx').read_bytes(), 1)
    text = calculate(inputs(short))
    assert '资产负债率：本期 60.0%（上期 50.0%）' in text and '毛利率：本期 50.0%（上期 50.0%）' in text


def test_two_row_docx_quote_header():
    doc = Document(); t = doc.add_table(rows=4, cols=4)
    for row, values in zip(t.rows, [['项目', '数量', '单价', '金额'], ['', '吨', 'USD', 'USD'], ['甲材料', '2', '5', '11'], ['合计', '', '', '11']]):
        for cell, value in zip(row.cells, values): cell.text = value
    buf = io.BytesIO(); doc.save(buf)
    results = evaluate(inputs(parse_docx(buf.getvalue(), 1)), [CheckFile(1, True, 'docx', 'quotation', [])], date(2026, 11, 2)).results
    assert [r.code for r in results] == ['L-01']
    assert results[0].locs[0] == 'F1-T1-R3-C4'


def test_formula_recursion_limits_and_decimal():
    cells = {('表一', 'A1'): (Decimal('0.1'), None), ('表一', 'A2'): (Decimal('0.2'), None),
             ('表一', 'A3'): (None, '=SUM($A$1:A2)'), ('表一', 'A4'): (None, '=A4')}
    assert evaluate_formula("=-(-'表一'!A3)*10/3", '表一', cells) == Decimal('1')
    assert evaluate_formula("=SUM('表一'!A1:A2)", "另一张表", cells) == Decimal("0.3")
    for formula in ('=A4', '=1/0', '=VLOOKUP(A1,A2,2)', '=[other.xlsx]A1'):
        with pytest.raises(UnsupportedFormula): evaluate_formula(formula, '表一', cells)


def test_financial_pipeline_encrypts_formulas(db_sessionmaker, make_user, ready_task):
    from sqlalchemy import select
    from app.db.models import DocNode, LocalFinding, LlmCall
    from app.security import crypto
    owner = make_user('finance-local')
    task = ready_task(owner, [('财务.xlsx', fixture.make_workbook())], policy='local_only')
    assert task.status == 'completed_local'
    with db_sessionmaker() as db:
        node = db.scalar(select(DocNode).where(DocNode.task_id == task.id, DocNode.loc == 'F1-S4-D3'))
        assert crypto.decrypt_text(node.formula_enc) == '=SUM(B3:C3)'
        assert b'SUM' not in node.formula_enc
        findings = list(db.scalars(select(LocalFinding).where(LocalFinding.task_id == task.id)))
        assert {'L-13', 'L-14', 'L-15', 'L-16'} <= {r.check_code for r in findings}
        assert all(crypto.decrypt_text(r.detail_enc) for r in findings)
        assert db.scalar(select(LlmCall.id)) is None
