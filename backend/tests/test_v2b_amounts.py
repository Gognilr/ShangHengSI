"""V2-B-10：设计 11.4 金额契约，先于实现提交。"""
from decimal import Decimal
from pathlib import Path
import itertools

import pytest

from app.redaction import detect, ner
from app.redaction.engine import NodeIn, UserTerm, compute, _manual_amount, RedactionInputError
from app.redaction.recheck import recheck
from app.review.requirements import load_system_template, select_builtin
from tests.review_support import env, ready_task, llm  # noqa: F401
from tests.test_user_note_redaction import alice, task_url  # noqa: F401


# 11.4 #2：11.1 七行示例逐条检查区间、类别、币种及换算值。
CASES = [
    ('日元 56,000', '56,000', 'JPY', '56000'),
    ('英镑 1,250', '1,250', 'GBP', '1250'),
    ('美金 7,800', '7,800', 'USD', '7800'),
    ('7800美金', '7800', 'USD', '7800'),
    ('港元1000', '1000', 'HKD', '1000'),
    ('港币1,000元', '1,000', 'HKD', '1000'),
    ('美金1,000元', '1,000', 'USD', '1000'),
    ('人民币1,000元', '1,000', 'CNY', '1000'),
    ('4870块', '4870', 'CNY', '4870'),
    ('4870 块钱', '4870', 'CNY', '4870'),
    ('50块整', '50', 'CNY', '50'),
    ('3.5千元', '3.5', 'CNY', '3500'),
    ('2百万美元', '2', 'USD', '2000000'),
    ('2千万元', '2', 'CNY', '20000000'),
    ('15万欧元', '15', 'EUR', '150000'),
    ('20万港元', '20', 'HKD', '200000'),
    ('3万日元', '3', 'JPY', '30000'),
    ('1亿日元', '1', 'JPY', '100000000'),
    ('2600余元', '2600', 'CNY', '2600'),
    ('68万多元', '68', 'CNY', '680000'),
    ('20多万元', '20', 'CNY', '200000'),
    ('9,350.00（元）', '9,350.00', 'CNY', '9350'),
    ('50(万元)', '50', 'CNY', '500000'),
    ('1,200（美元）', '1,200', 'USD', '1200'),
    ('三万五千元整', '三万五千元整', 'CNY', '35000'),
    ('两千元', '两千元', 'CNY', '2000'),
    ('十万元', '十万元', 'CNY', '100000'),
    ('一百二十万美元', '一百二十万美元', 'USD', '1200000'),
    ('人民币一千元', '一千元', 'CNY', '1000'),
    ('捌拾万元', '捌拾万元', 'CNY', '800000'),
]


@pytest.mark.parametrize('text,surface,currency,value', CASES)
def test_automatic_contract(text, surface, currency, value):
    hits = detect.find_amounts(text)
    assert len(hits) == 1
    h = hits[0]
    assert (h.start, h.end, h.category, h.currency, h.value) == (
        text.index(surface), text.index(surface) + len(surface), 'AMOUNT', currency, Decimal(value))
    assert any(r.category == 'AMOUNT' for r in recheck(text, real_values=[], terms=[]))


@pytest.mark.parametrize('text', ['100块砖', '第2块', '分为3块区域', '3 块区域', '一元化',
    '二元结构', '多元化经营', '第十元', '每百元收入', '1000万', '3千',
    '[金额_1]元', '[金额_12]块钱', '[金额_3]（元）', '[金额_1]千元',
    '[金额_2]百万美元', '[金额_3]（千元）'])
def test_negative_contract(text):
    assert detect.find_amounts(text) == []


def test_prompt_contract():
    from app.review.prompt import chat_system_prompt
    texts = [load_system_template()[1], chat_system_prompt()[1]]
    for kind, stance, source in itertools.product(
            ('contract', 'quotation', 'order', 'inquiry', 'letter'),
            ('sales', 'purchase', 'other'), ('outgoing', 'incoming')):
        texts.extend(r.text for r in select_builtin(kind, stance, source))
    for path in (Path(__file__).parents[1] / 'resources').rglob('*.txt'):
        if 'prompt' in str(path):
            texts.append(path.read_text(encoding='utf-8'))
    assert all(not any(r.category == 'AMOUNT' for r in recheck(t, real_values=[], terms=[])) for t in texts)


MANUAL = ['¥1,000', '1000元', '1,000.00（元）', 'USD 2,500', '3.5千元',
          '2600余元', '4870块钱', '三万五千元整']


@pytest.mark.parametrize('surface', MANUAL)
def test_manual_currency_and_fulltext(surface, monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [])
    h, = detect.find_amounts(surface)
    auto = compute([NodeIn('F1-P1', surface)], 'other')
    result = compute([NodeIn('F1-P1', surface + '；' + surface)], 'other',
                     user_terms=[UserTerm(surface, 'AMOUNT')])
    code = auto.items[0].placeholder
    expected = surface[:h.start] + code + surface[h.end:]
    assert result.redacted['F1-P1'] == expected + '；' + expected
    assert len(result.items) == 2
    assert all(i.detected_by == 'user' and i.placeholder == code for i in result.items)
    restored = result.redacted['F1-P1']
    for key, value in result.mapping.items():
        restored = restored.replace(key, value)
    assert restored == surface + '；' + surface


@pytest.mark.parametrize('surface', ['约1000元左右的费用', '1000元和2000元', 'abc', '1,00元'])
def test_invalid_selection(surface):
    with pytest.raises(RedactionInputError) as exc:
        _manual_amount(surface)
    assert (exc.value.status, exc.value.code) == (422, 'INVALID_AMOUNT')


def test_manual_bare_currency_isolation(monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [])
    result = compute([NodeIn('F1-P1', '1000；1000元')], 'other', user_terms=[UserTerm('1000', 'AMOUNT')])
    assert len(result.items) == 2
    assert result.items[0].placeholder != result.items[1].placeholder


@pytest.mark.usefixtures('env')
def test_fixed_prompt_text(db_sessionmaker):
    from app.review.prompt import fixed_prompt_text
    with db_sessionmaker() as db:
        assert not any(r.category == 'AMOUNT' for r in recheck(
            fixed_prompt_text(db), real_values=[], terms=[]))


@pytest.mark.usefixtures('env')
@pytest.mark.parametrize('surface', MANUAL)
@pytest.mark.parametrize('entry', ['manual', 'suspect'])
def test_selection_entrypoints(surface, entry, db_sessionmaker, alice, ready_task, monkeypatch):
    """模拟旧任务未命中的选区；确认页与疑似决定均走实际事务/API。"""
    import io
    from docx import Document
    from sqlalchemy import delete
    from app.db.models import RedactionItem, RedactionSuspect
    monkeypatch.setattr(ner, 'recognize', lambda text: [])
    user, client, headers = alice
    document = Document()
    document.add_paragraph(surface + '；' + surface)
    stream = io.BytesIO()
    document.save(stream)
    task = ready_task(user, files=[('虚构金额.docx', stream.getvalue())])
    with db_sessionmaker() as db:
        db.execute(delete(RedactionItem).where(RedactionItem.task_id == task.id))
        if entry == 'suspect':
            row = RedactionSuspect(task_id=task.id, loc='F1-P1', char_start=0,
                char_end=len(surface), reason='ner', decision='pending')
            db.add(row)
            db.flush()
            sid = row.id
        db.commit()
    if entry == 'manual':
        response = client.post(task_url(task, '/redaction/manual'), headers=headers,
            json={'loc': 'F1-P1', 'char_start': 0, 'char_end': len(surface), 'category': 'AMOUNT'})
    else:
        response = client.post(task_url(task, f'/redaction/suspects/{sid}'), headers=headers,
            json={'decision': 'replace', 'category': 'AMOUNT'})
    assert response.status_code == 200, response.text
    items = response.json()['items']
    h, = detect.find_amounts(surface)
    assert len(items) == 2
    assert {(i['char_start'], i['char_end']) for i in items} == {
        (h.start, h.end), (len(surface) + 1 + h.start, len(surface) + 1 + h.end)}
    assert len({i['placeholder'] for i in items}) == 1
    overlap = client.post(task_url(task, '/redaction/manual'), headers=headers,
        json={'loc': 'F1-P1', 'char_start': 0, 'char_end': len(surface), 'category': 'AMOUNT'})
    assert (overlap.status_code, overlap.json()['code']) == (409, 'ALREADY_REDACTED')


@pytest.mark.usefixtures('env')
def test_amounts_fixture_wire_diagnostic(db_sessionmaker, alice, llm):
    """V2Q-58：实测固定编号中的短串与正文金额脱敏，A-03 仅按授权同步。"""
    import re
    from tests.acceptance_support import golden_samples, run_sample, outbound_records
    client, headers = alice[1:]
    requests = llm('normal')
    sample = next(s for s in golden_samples() if s.name == 'amounts_cn_forms.docx')
    run_sample(client, headers, db_sessionmaker, sample)
    record, = outbound_records(db_sessionmaker, requests)
    for source, text in [('llm_calls', record.request_text), ('request', record.wire_text)]:
        contexts = [text[max(0, m.start()-8):m.end()+8] for m in re.finditer('15', text)]
        print(source, '15 contexts:', contexts)
        assert contexts and all('[C-15]' in c for c in contexts)
        assert '15万欧元' not in text
        assert recheck(text, real_values=record.real_values, terms=record.terms) == []


# V2-B-11 审核补充：金额后紧跟“空格 + 数字”不再整笔漏掉（V1.0 起的漏洞）；只有量级的单位说明与“圆钢”不算金额。
@pytest.mark.parametrize('text,surfaces', [
    ('支付30000元 3日内付清', ['30000']),
    ('首付款30000元\n2. 尾款', ['30000']),
    ('100元 200元', ['100', '200']),
    ('1000元2000元', ['1000', '2000']),
    ('预付100美元 3日内', ['100', '3']),
    ('3.1 USD 100', ['100']),
])
def test_review_amount_followed_by_number(text, surfaces):
    hits = detect.find_amounts(text)
    assert [text[h.start:h.end] for h in hits] == surfaces
    assert any(r.category == 'AMOUNT' and r.position == text.index(surfaces[0])
               for r in recheck(text, real_values=[], terms=[]))


@pytest.mark.parametrize('text', ['金额单位：千元', '单位：百万元', '（千元）', '百元大钞', '千元以下采购',
                                  'Q235 Φ20圆钢', '直径20圆管'])
def test_review_unit_only_not_amount(text):
    assert detect.find_amounts(text) == []


@pytest.mark.parametrize('text,surface,value', [('十元', '十元', '10'), ('数十万元', '十万元', '100000'),
                                                ('一千圆整', '一千圆整', '1000')])
def test_review_lower_cn_still_hits(text, surface, value):
    h, = detect.find_amounts(text)
    assert (text[h.start:h.end], h.value) == (surface, Decimal(value))
