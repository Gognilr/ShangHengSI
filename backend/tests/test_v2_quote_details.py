"""V2-A-03：设计 3.5 逐条覆盖，样本与金额均为虚构。"""
import pytest

from app.redaction import detect, quotes, outbound
from app.redaction.engine import NodeIn, compute, outbound_text
from app.redaction.recheck import recheck
from app.review.coverage import describe
from tests.test_redaction_golden import load


def table_nodes(rows=3):
    nodes = [NodeIn('F2-S1-A1', '品名'), NodeIn('F2-S1-B1', '单价(USD)'),
             NodeIn('F2-S1-C1', '金额(USD)')]
    for row in range(2, rows + 2):
        nodes += [NodeIn(f'F2-S1-A{row}', '示例产品'), NodeIn(f'F2-S1-B{row}', '0' if row == 2 else '250'),
                  NodeIn(f'F2-S1-C{row}', '0.00' if row == 2 else '50,000.00')]
    return nodes


def test_golden_actual_outbound_contains_note_and_each_cell():
    nodes, ann, _, _, _ = load('quotation_xlsx')
    # 原样本只有两行；在内存中插入虚构的第三行，原 fixtures 与回归断言不变。
    shift = {'A10': 'A11', 'F10': 'F11', 'A11': 'A12', 'F11': 'F12'}
    nodes = [NodeIn('F1-S1-' + shift[n.loc[6:]], n.text, n.clause_no)
             if n.loc.startswith('F1-S1-') and n.loc[6:] in shift else n for n in nodes]
    index = next(i for i, n in enumerate(nodes) if n.loc == 'F1-S1-A11')
    nodes[index:index] = [NodeIn(f'F1-S1-{col}10', text) for col, text in
                         [('A', '3'), ('B', '虚构第三产品'), ('C', '定制规格'), ('D', '10'),
                          ('E', 'USD 750'), ('F', 'USD 7500'), ('G', '30%')]]
    result = compute(nodes, ann['stance'], main_prefix='F1-')
    sent = outbound_text(nodes, [], result.redacted)
    (table,) = quotes.find_quote_tables(nodes, result.redacted)
    note = next(line for line in sent.splitlines() if line.startswith(f'[{table.loc}]'))
    assert '明细逐格列在下方' in note and '产品：' not in note
    for loc in table.row_locs:
        assert sum(line.startswith(f'[{loc}] ') for line in sent.splitlines()) == 1
    assert '[单价_' in sent and '[金额_' in sent
    assert all(item['text'] not in sent for item in ann['must_replace'])


def test_original_two_row_golden_stays_ordinary_table():
    nodes, _, result, _, sent = load('quotation_xlsx')
    assert quotes.find_quote_tables(nodes, result.redacted) == []
    assert '报价明细表' not in sent
    assert all(f'[{n.loc}] ' in sent for n in nodes)


def test_zero_cells_preserved_and_not_mapped():
    nodes = table_nodes()
    result = compute(nodes, 'purchase')
    sent = outbound_text(nodes, [], result.redacted)
    assert '[F2-S1-B2] 0\n' in sent and '[F2-S1-C2] 0.00\n' in sent
    assert not any(i.loc in {'F2-S1-B2', 'F2-S1-C2'} for i in result.items)


@pytest.mark.parametrize('text', ['0', '0.00', '¥0', 'USD 0.00', '金额 0.00', '金额 0万元'])
def test_zero_detection_and_recheck(text):
    assert detect.find_amounts(text, 'AMOUNT') == []
    assert detect.find_bare_amounts(text) == []
    assert not any(r.category == 'AMOUNT' for r in recheck(text, real_values=[], terms=[]))


def test_recheck_zero_and_nonzero():
    assert recheck('单价 ¥0，金额 USD 0.00', real_values=[], terms=[]) == []
    assert any(r.category == 'AMOUNT' for r in recheck('单价 ¥5', real_values=[], terms=[]))


def test_ratios_remain_in_body_but_never_quote_cells():
    nodes = [NodeIn('F1-P1', '合同总价人民币100,000元'),
             NodeIn('F1-P2', '预付款为合同总价的30%，即人民币30,000元')] + table_nodes()
    result = compute(nodes, 'purchase', main_prefix='F1-')
    assert '30%' in result.redacted['F1-P2'] and '（=合同总价的' in result.redacted['F1-P2']
    assert '[金额_' in result.redacted['F2-S1-C3']
    assert '（=合同总价的' not in result.redacted['F2-S1-C3']


@pytest.mark.parametrize('rows,detailed', [(200, True), (201, False)])
def test_detail_boundary_and_fallback_coverage(rows, detailed):
    nodes = table_nodes(rows)
    result = compute(nodes, 'purchase')
    (table,) = quotes.find_quote_tables(nodes, result.redacted)
    assert table.detailed is detailed
    sent = outbound_text(nodes, [], result.redacted)
    assert ('[F2-S1-A1]' in sent) is detailed
    assert ('产品：' in sent) is not detailed
    if not detailed:
        assert '超过 200 行' in describe({'quote_tables': [{'file': 2, 'loc': table.loc, 'rows': rows}]}, {})[0]
