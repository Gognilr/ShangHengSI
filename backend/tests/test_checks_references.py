"""V2-A-05：引用完整书名与增值税语境；全部内容虚构。"""
from datetime import date

import pytest

from app.checks.model import CheckNode, CheckFile
from app.checks.service import evaluate


def results(*texts):
    nodes = [CheckNode(f'F1-P{i}', text, 1, None) for i, text in enumerate(texts, 1)]
    return [r for r in evaluate(nodes, [CheckFile(1, True, 'docx', 'contract', [])], date(2026, 10, 24)).results
            if r.code in ('L-10', 'L-11')]


@pytest.mark.parametrize('text,count', [('本合同依据《中华人民共和国合同法》订立', 1), ('依据《合同法》第五十二条', 1),
                                      ('依据合同法', 0), ('《最高人民法院关于适用〈中华人民共和国担保法〉若干问题的解释》', 0)])
def test_abolished_law_titles(text, count):
    got = results(text)
    assert len(got) == count
    if got:
        assert got[0].severity == 'medium' and '2021-01-01' in got[0].detail and '民法典' in got[0].detail
        assert got[0].detail == got[0].ai_summary


def test_law_aliases_merge_all_locations():
    got = results('依据《合同法》', '依据《中华人民共和国合同法》')
    assert len(got) == 1 and got[0].locs == ['F1-P1', 'F1-P2']


@pytest.mark.parametrize('text,count', [('增值税税率为17%，乙方开具增值税专用发票', 1), ('进口关税税率10%', 0),
                                      ('税率13%', 0), ('含税税率8%', 0), ('折扣17%', 0), ('所得税税率17%', 0)])
def test_vat_context(text, count):
    got = results(text)
    assert len(got) == count
    if got:
        assert got[0].code == 'L-11' and got[0].severity == 'medium'


def test_vat_rate_table_column_and_safe_summary():
    nodes = [CheckNode(loc, text, 1, None) for loc, text in [('F1-S1-A1', '单价'), ('F1-S1-B1', '税率'),
                                                          ('F1-S1-A2', '100'), ('F1-S1-B2', '16%')]]
    got = [r for r in evaluate(nodes, [CheckFile(1, True, 'xlsx', 'quotation', [])], date(2026, 10, 24)).results
           if r.code == 'L-11']
    assert len(got) == 1 and got[0].locs == ['F1-S1-B2']
    assert '100' not in got[0].ai_summary
