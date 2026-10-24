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


# V2-A-09（V2Q-30）：审核时用常见写法实测发现的误报与漏报
def test_penalty_and_deposit_ratios_not_summed():
    clause = [('第三条', '付款方式'), ('1.', '签约后支付合同总价的30%'), ('2.', '到货后支付合同总价的60%'),
              ('3.', '验收后支付合同总价的10%')]
    assert results(*clause, ('4.', '甲方逾期付款的，每日按合同总价的0.05%支付违约金')) == []
    assert results(*clause[:2], ('2.', '到货后支付合同总价的70%'),
                   ('3.', '乙方应提交合同总价的10%作为履约保证金')) == []
    # 质量保证金是付款的一部分，照常计入
    got = results(*clause[:3], ('3.', '合同总价的20%作为质量保证金，质保期满后支付'))
    assert len(got) == 1 and '合计 110%' in got[0].detail


def test_amount_stops_at_fullwidth_comma_and_contract_total_price_label():
    got = results((None, '合同总价人民币100,000元'),
                  (None, '预付款为合同总价的30%，即人民币33,000元，余款人民币70,000元验收后支付'))
    assert len(got) == 1 and '30000.00' in got[0].detail
    got = results((None, '合同总价人民币100,000元'), (None, '支付合同总价款的30%，即人民币33,000元'))
    assert len(got) == 1
