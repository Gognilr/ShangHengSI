"""V2-A-05 5.5：分期金额与一级付款条款比例。"""
from datetime import date

import pytest

from app.checks.model import CheckNode, CheckFile
from app.checks.service import evaluate

FILES = [CheckFile(1, True, 'docx', 'contract', [])]


def results(*texts):
    nodes = [CheckNode(f'F1-P{i}', text, 1, clause) for i, (clause, text) in enumerate(texts, 1)]
    return [r for r in evaluate(nodes, FILES, date(2026, 10, 24)).results if r.code == 'L-09']


@pytest.mark.parametrize('actual,count', [('30,000', 0), ('33,000', 1)])
def test_contract_percentage_amount(actual, count):
    got = results((None, '合同总价100,000元'), (None, f'预付款为合同总价的30%，即人民币{actual}元'))
    assert len(got) == count
    if got:
        assert got[0].severity == 'high'
        assert '30000.00' in got[0].detail and '33000' in got[0].detail
        assert '30000' not in got[0].ai_summary and '33000' not in got[0].ai_summary


@pytest.mark.parametrize('actual,count', [('16,000', 0), ('15,000', 1)])
def test_named_base_unique_in_same_file(actual, count):
    got = results((None, '软件总价款：80,000元'), (None, f'支付软件总价款的20%，即{actual}元'))
    assert len(got) == count


def test_no_base_or_ambiguous_base_is_not_checked():
    assert results((None, '支付软件总价款的20%，即16,000元')) == []
    assert results((None, '软件总价款：80,000元'), (None, '软件总价款：90,000元'),
                   (None, '支付软件总价款的20%，即16,000元')) == []


@pytest.mark.parametrize('rates,severity,total', [([30, 60, 10], None, 100), ([30, 60, 20], 'medium', 110),
                                                ([30, 60], 'low', 90)])
def test_payment_clause_sum(rates, severity, total):
    got = results(('第五条', '付款方式'), *[(f'{i}.', f'支付合同总价的{rate}%') for i, rate in enumerate(rates, 1)],
                  ('第六条', '其他约定'))
    if severity is None:
        assert got == []
    else:
        assert len(got) == 1 and got[0].severity == severity
        assert f'合计 {total}%' in got[0].detail
        if severity == 'low':
            assert '不足 100%' in got[0].detail


def test_clause_boundaries_currency_fill_and_rounding():
    assert results(('第五条', '付款'), ('1.', '支付合同总价的30%'), ('第六条', '违约责任'),
                   ('1.', '违约金为合同总价的80%')) == []
    assert results((None, '合同总价100,000元'), (None, '预付款为合同总价的30%，即USD 30,001')) == []
    assert results((None, '合同总价100,000元'), (None, '预付款为合同总价的【30】%，即人民币【3】万元')) == []
