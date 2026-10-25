"""V2-B-10：设计 11.4 金额契约，先于实现提交。"""
from decimal import Decimal
from pathlib import Path
import itertools

import pytest

from app.redaction import detect, ner
from app.redaction.engine import NodeIn, UserTerm, compute, _manual_amount, RedactionInputError
from app.redaction.recheck import recheck
from app.review.requirements import load_system_template, select_builtin


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
    '[金额_1]元', '[金额_12]块钱', '[金额_3]（元）'])
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
