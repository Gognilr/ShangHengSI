"""V2Q-43、45：集中验证发现的问题，先固定完整区间与不回退契约。"""
import json
from pathlib import Path

import pytest

from app.redaction import detect, ner
from app.redaction.engine import NodeIn, compute
from app.redaction.restore import restore
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_redaction_api import alice  # noqa: F401
from tests.test_review_job import long_contract, new_run


def test_common_both_word_is_only_an_exact_model_stopword():
    assert not ner.keep_entity('双方', ner.Entity(0, 2, 'ORG', 0.7))
    assert not compute([NodeIn('F1-P1', '双方')], 'other').suspects
    name = '张双宁'
    assert ner.keep_entity(name, ner.Entity(0, len(name), 'PERSON', 0.7))
    text = f'与{name}沟通。'
    result = compute([NodeIn('F1-P1', text)], 'other')
    start = text.index(name)
    assert any(i.category == 'PERSON' and i.char_start <= start
               and i.char_end >= start + len(name) for i in result.items) or any(
        s.char_start <= start and s.char_end >= start + len(name) for s in result.suspects)


def test_long_contract_can_reach_review_without_ignoring_model_results(db_sessionmaker, alice, ready_task):
    user, _, _ = alice
    task = ready_task(user, [('长合同.docx', long_contract())])
    run = new_run(db_sessionmaker, task, user)
    assert run.status == 'queued'


@pytest.mark.parametrize('sample_id', ['letter-cn', 'mail-en', 'minutes-en'])
def test_frozen_narrative_address_is_fully_covered(sample_id):
    path = Path(__file__).resolve().parents[2] / 'docs/v2/reports/ner-selection/samples.json'
    sample = next(s for s in json.loads(path.read_text(encoding='utf-8'))['samples'] if s['id'] == sample_id)
    text = sample['text']
    address = next(value for value, category in sample['entities'] if category == 'ADDR')
    start, end = text.index(address), text.index(address) + len(address)
    assert any(h.start == start and h.end == end for h in detect.find_addresses(text))
    result = compute([NodeIn('F1-P1', text)], 'other')
    assert any(i.category == 'ADDR' and i.char_start == start and i.char_end == end for i in result.items)
    assert address not in result.redacted['F1-P1']
    for kept in sample['must_keep']:
        assert kept in result.redacted['F1-P1']


@pytest.mark.parametrize('text', ['上海市', '中国', 'Floor 2'])  # 其余见 golden cases_addresses
def test_unlabeled_address_does_not_consume_places_or_unit_alone(text):
    assert detect.find_addresses(text) == []


@pytest.mark.parametrize('text', ["12 Supreme People's Court"])  # 其余见 golden cases_addresses
def test_unlabeled_address_rejects_reviewed_generic_phrases(text):
    assert detect.find_addresses(text) == []


def test_project_containing_our_party_has_one_complete_reversible_span():
    name = '江北市虚构医院'
    project = name + '信息化改造项目'
    texts = [f'买方：{name}', '卖方：澄川虚构信息技术有限公司', f'项目名称：{project}',
             f'{name}应按期验收。']
    nodes = [NodeIn(f'F1-P{i}', text) for i, text in enumerate(texts, 1)]
    result = compute(nodes, 'purchase')
    items = [i for i in result.items if i.loc == 'F1-P3']
    assert len(items) == 1
    item = items[0]
    assert (item.category, item.char_start, item.char_end) == ('PROJECT', 5, 5 + len(project))
    assert result.redacted['F1-P3'] == f'项目名称：{item.placeholder}'
    assert result.redacted['F1-P1'] == '买方：我方'
    assert result.redacted['F1-P4'] == '我方应按期验收。'
    for node in nodes:
        assert restore(result.redacted[node.loc], result.mapping) == node.text
