"""T-53：真实反馈对应虚构用例，先写测试再实现。"""
import io

import pytest
from docx import Document
from sqlalchemy import delete, select

from app.redaction import detect, ner, parties, suspects
from app.redaction.engine import NodeIn, Term, compute
from app.db.models import RedactionSuspect
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_user_note_redaction import alice, task_url  # noqa: F401


@pytest.fixture(autouse=True)
def offline_ner(monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [])


def test_t53_02_company_boundaries():
    text = '我方澄川虚构碳材料股份有限公司，现指定青岚虚构国际贸易有限公司为代理'
    spans = parties.find_other_org_names(text)
    assert [text[a:b] for a, b in spans] == ['澄川虚构碳材料股份有限公司', '青岚虚构国际贸易有限公司']
    result = compute([NodeIn('F1-P1', text)], 'other')
    assert '我方' in result.redacted['F1-P1'] and '现指定' in result.redacted['F1-P1']


@pytest.mark.parametrize('name', ['现代虚构重工有限公司', '特变虚构电工有限公司'])
def test_t53_03_preserve_company_start(name):
    assert [(a, b) for a, b in parties.find_other_org_names(name)] == [(0, len(name))]


def test_t53_04_existing_learned_name():
    name = '我方虚构科技有限公司'
    result = compute([NodeIn('F1-P1', name)], 'other', terms=[Term(text=name, kind='learned', category='ORG')])
    assert result.items and any(i.char_start == 0 and i.char_end == len(name) for i in result.items)


@pytest.mark.parametrize('text,name', [('尊敬的柯墨兰先生：', '柯墨兰'),
    ('请欧阳明月女士确认', '欧阳明月'), ('叶先生', '叶先生'),
    ('DEAR MR.KE MOLAN:', 'KE MOLAN'), ('Dear Mrs Lorna Fictive,', 'Lorna Fictive'),
    ('Attn: MISS JANE FICTIVE', 'JANE FICTIVE'), ('Contact Name: Lorna Fictive', 'Lorna Fictive')])
def test_t53_05_07_09_persons(text, name):
    hits = detect.find_persons(text)
    assert [text[h.start:h.end] for h in hits] == [name]


@pytest.mark.parametrize('text', ['各位先生', '女士们', 'Dr Street 12', 'Main Dr. ',
    'Product Name: Calcined Needle Coke', 'Item Name: Graphite Electrode'])
def test_t53_06_08_09_non_persons(text):
    assert detect.find_persons(text) == []


def test_t53_09_company_name_unchanged():
    text = 'Company Name: Northwind Fictive'
    assert [text[h.start:h.end] for h in detect.find_persons(text)] == ['Northwind Fictive']


@pytest.mark.usefixtures('env')
@pytest.mark.parametrize('decision', ['ignore', 'replace'])
def test_t53_10_group_and_decide(alice, ready_task, db_sessionmaker, decision):
    user, client, headers = alice
    doc = Document()
    for _ in range(5):
        doc.add_paragraph('Velmora Refinery')
    stream = io.BytesIO()
    doc.save(stream)
    task = ready_task(user, files=[('虚构分组.docx', stream.getvalue())])
    with db_sessionmaker() as db:
        db.execute(delete(RedactionSuspect).where(RedactionSuspect.task_id == task.id))
        for i in range(1, 6):
            db.add(RedactionSuspect(task_id=task.id, loc=f'F1-P{i}', char_start=0, char_end=16,
                reason='capitalized' if i % 2 else 'ner', decision='pending'))
        db.commit()
    response = client.get(task_url(task, '/redaction'), headers=headers)
    assert response.status_code == 200
    rows = response.json()['suspects']
    assert len(rows) == 1 and rows[0]['occurrences'] == 5
    assert rows[0]['loc'] == 'F1-P1' and rows[0]['reason'] == 'capitalized'
    response = client.post(task_url(task, f"/redaction/suspects/{rows[0]['id']}"), headers=headers,
        json={'decision': decision, 'category': 'ORG' if decision == 'replace' else None})
    assert response.status_code == 200, response.text
    assert not [s for s in response.json()['suspects'] if s['decision'] == 'pending']
    if decision == 'replace':
        assert len(response.json()['items']) == 5
    else:
        with db_sessionmaker() as db:
            assert len(list(db.scalars(select(RedactionSuspect).where(
                RedactionSuspect.task_id == task.id, RedactionSuspect.decision == 'ignore')))) == 5


@pytest.mark.usefixtures('env')
def test_t53_11_two_groups(alice, ready_task):
    user, client, headers = alice
    doc = Document()
    for text in ['Velmora Refinery', 'Hardgrave Office'] * 3:
        doc.add_paragraph(text)
    stream = io.BytesIO()
    doc.save(stream)
    task = ready_task(user, files=[('虚构两组.docx', stream.getvalue())])
    rows = client.get(task_url(task, '/redaction'), headers=headers).json()['suspects']
    assert len(rows) == 2 and all(r['occurrences'] == 3 for r in rows)


@pytest.mark.parametrize('text', ['approved suppliers', 'ASTM D4052', 'GB/T 24526',
    'T/ZGTS 002-2022', 'ASTM', 'We', 'The inspector'])
@pytest.mark.parametrize('score', [0.5, 0.95])
def test_t53_12_ner_filter(text, score):
    assert not ner.keep_entity(text, ner.Entity(0, len(text), 'ORG', score))


@pytest.mark.parametrize('text', ['Velmora Refinery', 'CCIC'])
@pytest.mark.parametrize('score', [0.5, 0.95])
def test_t53_13_ner_kept(text, score):
    assert ner.keep_entity(text, ner.Entity(0, len(text), 'ORG', score))


def test_t53_14_15_lowercase_evidence():
    nodes = [NodeIn('F1-P1', 'B. Feedstock Base'), NodeIn('F1-P2', 'feedstock base')]
    assert not compute(nodes, 'other').suspects
    text = 'B. Velmora Base'
    result = compute([NodeIn('F1-P1', text)], 'other')
    assert [text[s.char_start:s.char_end] for s in result.suspects] == ['Velmora Base']
    text = 'Kinematic Viscosity'
    assert compute([NodeIn('F1-P1', text)], 'other').suspects
    assert not compute([NodeIn('F1-P1', text), NodeIn('F1-P2', text.lower())], 'other').suspects


def test_t53_16_chat_evidence():
    from app.chat.service import _redact
    assert not _redact('Bulk Density 偏低怎么办，bulk density 指什么？', {}, []).suspects


@pytest.mark.parametrize('text', ['Calcined Needle Coke', 'Product Name: Calcined Needle Coke'])
def test_v2q167_standard_product_preserved(text):
    result = compute([NodeIn('F1-P1', text)], 'other')
    assert result.redacted['F1-P1'] == text
    start = text.index('Calcined Needle Coke')
    assert not result.items
    assert not any(s.char_start < len(text) and s.char_end > start for s in result.suspects)


def test_v2q167_product_label_does_not_exempt_proper_name():
    text = 'Product Name: Velmora Needle Coke'
    result = compute([NodeIn('F1-P1', text)], 'other')
    start = text.index('Velmora')
    assert any(s.char_start <= start and s.char_end >= start + len('Velmora') for s in result.suspects)
