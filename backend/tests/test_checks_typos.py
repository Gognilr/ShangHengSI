"""E-05：固定错写命中与法律易混词白名单。"""
from datetime import date

from app.checks.model import CheckFile, CheckNode
from app.checks.service import evaluate
from app.checks.typos import check_typos


def test_l22_typo_summary_and_whitelist():
    nodes = [CheckNode('F1-P9', '星河虚构公司按装设备，保正金为12345元，按装后验收。', 1, None)]
    result, = check_typos(nodes)
    assert (result.code, result.severity, result.locs) == ('L-22', 'low', ['F1-P9'])
    assert result.ai_summary == 'F1-P9 疑似错别字：‘按装’应为‘安装’；‘保正金’应为‘保证金’'
    assert not any(word in result.ai_summary for word in ('星河', '12345'))
    files = [CheckFile(1, True, 'docx', 'contract', [])]
    assert any(r.code == 'L-22' for r in evaluate(nodes, files, date(2026, 11, 2)).results)
    clean = '定金与订金、权利与权力、截止与截至、需与须、账户、订单、安装、交货期、保证金、违约金。'
    assert check_typos([CheckNode('F1-P10', clean, 1, None)]) == []
