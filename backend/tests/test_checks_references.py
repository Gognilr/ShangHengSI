"""V2-A-05：引用完整书名与增值税语境；全部内容虚构。"""
from datetime import date

import pytest

from app.checks.model import CheckNode, CheckFile
from app.checks.service import evaluate
from app.db.models import Law
from app.laws.index import load


@pytest.fixture
def builtin_index(db_sessionmaker):
    with db_sessionmaker() as db:
        db.add(Law(title='中华人民共和国合同法', category='法律', status='repealed', source='builtin',
                   published_on=date(1999, 3, 15), repealed_on=date(2021, 1, 1), replaced_by='中华人民共和国民法典'))
        db.commit()
        return load(db, {'合同法', '中华人民共和国合同法'})


def results(*texts, laws=None):
    nodes = [CheckNode(f'F1-P{i}', text, 1, None) for i, text in enumerate(texts, 1)]
    return [r for r in evaluate(nodes, [CheckFile(1, True, 'docx', 'contract', [])], date(2026, 10, 24), laws=laws).results
            if r.code in ('L-10', 'L-11')]


@pytest.mark.parametrize('text,count', [('本合同依据《中华人民共和国合同法》订立', 1), ('依据《合同法》第五十二条', 1),
                                      ('依据合同法', 0), ('《最高人民法院关于适用〈中华人民共和国担保法〉若干问题的解释》', 0)])
def test_abolished_law_titles(text, count, builtin_index):
    got = results(text, laws=builtin_index)
    assert len(got) == count
    if got:
        title = '中华人民共和国合同法' if '中华人民共和国合同法' in text else '合同法'
        assert got[0].severity == 'medium'
        assert got[0].detail == f'引用《{title}》，该法已于 2021-01-01 废止，现行规定见《中华人民共和国民法典》'
        assert got[0].detail == got[0].ai_summary


def test_law_aliases_merge_all_locations(builtin_index):
    got = results('依据《合同法》', '依据《中华人民共和国合同法》', laws=builtin_index)
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


# V2-A-09（V2Q-30）：增值税语境中“{基数}的 N%”是付款或违约金比例，不当作税率
@pytest.mark.parametrize('text,count', [('验收合格后，乙方开具增值税专用发票，甲方支付合同总价的10%', 0),
                                      ('合同价款为含税价，逾期交货违约金累计不超过合同总价的 10%', 0),
                                      ('乙方开具税率为10%的增值税专用发票', 1), ('税率：11%', 1)])
def test_vat_ignores_ratio_of_base(text, count):
    assert len(results(text)) == count


@pytest.mark.parametrize('statuses,expected', [(['amended', 'valid'], False), (['repealed', 'repealed'], True),
                                              (['repealed', 'not_yet'], False), (['repealed', 'amended'], False),
                                              (['repealed', 'valid'], False)])
def test_t42_multiple_versions(db_sessionmaker, statuses, expected):
    with db_sessionmaker() as db:
        for year, status in enumerate(statuses, 2020):
            db.add(Law(title='示例供货合同法', category='法律', status=status, source='import', published_on=date(year, 1, 1)))
        db.commit()
        laws = load(db, {'示例供货合同法'})
    got = results('依据《示例供货合同法》', laws=laws)
    assert bool(got) == expected
    if expected:
        assert got[0].detail == '引用《示例供货合同法》，该法已废止'


@pytest.mark.parametrize('category,source,expected', [('法律', 'import', True), ('行政法规', 'import', True),
    ('司法解释', 'import', True), ('地方性法规', 'import', False), ('地方法规', 'sync', False),
    ('修改、废止的决定', 'import', False), ('内置分类', 'builtin', True)])
def test_t42_categories(db_sessionmaker, category, source, expected):
    with db_sessionmaker() as db:
        db.add(Law(title='示例法', category=category, status='repealed', source=source, published_on=date(2020, 1, 1)))
        db.commit()
        laws = load(db, {'示例法', '不存在法'})
    assert bool(results('依据《示例法》', laws=laws)) == expected
    assert not results('依据《不存在法》', laws=laws)


def test_t42_prefix_only_aliases_and_query_scope(db_sessionmaker):
    with db_sessionmaker() as db:
        for title in ['中华人民共和国示例法', '示例采购管理规定', '无关法']:
            db.add(Law(title=title, category='法律', status='repealed', source='import', published_on=date(2020, 1, 1)))
        db.commit()
        laws = load(db, {'示例法', '采购规定'})
    assert results('《示例法》', laws=laws)[0].detail == '引用《示例法》，该法已废止'
    assert not results('《采购规定》', laws=laws)
    assert not results('《无关法》', laws=laws)


def test_t42_latest_replacement_max_date_and_file_scope(db_sessionmaker):
    with db_sessionmaker() as db:
        for year, repeal, replacement in [(2018, date(2023, 1, 1), '示例旧规定'),
                                           (2020, date(2022, 1, 1), '示例新规定'), (2021, None, None)]:
            db.add(Law(title='中华人民共和国示例法', category='法律', source='import', status='repealed',
                       published_on=date(year, 1, 1), repealed_on=repeal, replaced_by=replacement))
        db.commit()
        laws = load(db, {'示例法', '中华人民共和国示例法'})
    nodes = [CheckNode('F1-P1', '《示例法》', 1, None), CheckNode('F1-P2', '《中华人民共和国示例法》', 1, None),
             CheckNode('F2-P1', '《示例法》', 2, None)]
    from app.checks.references import check_references
    got = [r for r in check_references(nodes, laws=laws) if r.code == 'L-10']
    assert len(got) == 2 and got[0].locs == ['F1-P1', 'F1-P2'] and got[1].locs == ['F2-P1']
    assert got[0].detail == '引用《示例法》，该法已于 2023-01-01 废止，现行规定见《示例新规定》'


@pytest.mark.parametrize('category', ['宪法相关法', '民法商法', '行政法', '经济法',
                                    '社会法', '生态环境法', '刑法', '诉讼与非诉讼程序法'])
def test_e08_law_subcategories_in_l10(db_sessionmaker, category):
    with db_sessionmaker() as db:
        law = Law(title='示例法', category=category, status='repealed', source='import',
                  published_on=date(2020, 1, 1))
        db.add(law); db.commit()
        assert results('依据《示例法》', laws=load(db, {'示例法'}))[0].code == 'L-10'
        assert law.category == category
        law.status = 'valid'; db.commit()
        assert not results('依据《示例法》', laws=load(db, {'示例法'}))
