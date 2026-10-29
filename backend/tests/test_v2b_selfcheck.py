"""V2-B-16：T-54 外发漏洞契约，测试先行。"""
from decimal import Decimal

import pytest

from app.redaction import detect, ner
from app.redaction.engine import NodeIn, compute
from app.redaction.recheck import recheck
from tests.test_v2b_amounts import test_prompt_contract, test_fixed_prompt_text, env  # noqa: F401


@pytest.mark.parametrize('text', ['+1 (713) 555-0142', '(021) 6688-1234',
    '138-1234-5678', '138 1234 5678', '0512-6688 1234', '0512 6688 1234'])
def test_t54_phone(text):
    assert any(h.category == 'PHONE' and h.start == 0 and h.end == len(text)
               for h in detect.find_formatted(text))
    assert any(h.category == 'PHONE' for h in recheck(text, real_values=[], terms=[]))


@pytest.mark.parametrize('text', ['2026-0512-1234', '订单号 0512 6688 1234 5',
    '账号：0110 2345 6789 012', 'x138-1234-5678', '138-1234-5678-x'])
def test_t54_phone_boundaries(text):
    assert not any(h.category == 'PHONE' for h in detect.find_formatted(text))


def test_t54_amount_alias(monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [])
    text = 'USD 480,000.00 (Say US Dollars Four Hundred Eighty Thousand Only)'
    hits = detect.find_amounts(text)
    assert len(hits) == 2
    assert {h.value for h in hits} == {Decimal('480000')}
    assert {h.currency for h in hits} == {'USD'}
    assert len({h.key for h in hits}) == 1
    result = compute([NodeIn('F1-P1', text)], 'other')
    assert len(result.items) == 2
    assert len({i.placeholder for i in result.items}) == 1
    assert not recheck(result.redacted['F1-P1'], real_values=[], terms=[])


def test_t54_cents():
    h, = detect.find_amounts('EUR One Million Two Hundred Thousand and Fifty Cents')
    assert (h.currency, h.value) == ('EUR', Decimal('1200000.50'))


@pytest.mark.parametrize('text', ['two thousand tons', 'one hundred percent',
    '[金额_1]', 'Say US Dollars [金额_1] Only'])
def test_t54_non_amount(text):
    assert not detect.find_amounts(text)


@pytest.mark.parametrize('text,value', [('微信 zw_fictive88', 'zw_fictive88'),
    ('QQ 12345678', '12345678'), ('微信号lan_fictive01', 'lan_fictive01')])
def test_t54_im(text, value):
    assert any(h.category == 'IM' and text[h.start:h.end] == value for h in detect.find_formatted(text))


@pytest.mark.parametrize('text', ['微信沟通后确认', 'WeChat Pay 付款', 'QQ群'])
def test_t54_non_im(text):
    assert not any(h.category == 'IM' for h in detect.find_formatted(text))


@pytest.mark.parametrize('text,value', [('我司总经理孙明亮先生已同意', '孙明亮'),
    ('采购总监周女士确认', '周女士'), ('由项目经理刘洋负责', '刘洋')])
def test_t54_role_person(text, value):
    assert any(text[h.start:h.end] == value for h in detect.find_persons(text))


@pytest.mark.parametrize('text', ['经理同意后生效', '总经理高兴地表示'])
def test_t54_non_person(text):
    assert not detect.find_persons(text)


@pytest.mark.parametrize('text,surface', [('+1 (713) 555-0142', '+1 (713) 555-0142'),
    ('微信 zw_fictive88', 'zw_fictive88'), ('总经理孙明亮先生已同意', '孙明亮'),
    ('Say US Dollars Four Hundred Eighty Thousand Only', 'Four Hundred Eighty Thousand')])
def test_t54_three_entrypoints(text, surface, monkeypatch):
    from app.chat.service import _redact
    from app.redaction.restore import restore
    monkeypatch.setattr(ner, 'recognize', lambda text: [])
    for loc in ('F1-P1', 'NOTE'):
        result = compute([NodeIn(loc, text)], 'other')
        assert surface not in result.redacted[loc]
        # 电话、即时通信按既有契约只遮盖；人名和金额可逐字还原。
        if surface in ('孙明亮', 'Four Hundred Eighty Thousand'):
            assert restore(result.redacted[loc], result.mapping) == text
        else:
            assert surface in result.mapping.values()
    chat = _redact(text, {}, [])
    assert surface not in chat.text
    if surface in ('孙明亮', 'Four Hundred Eighty Thousand'):
        assert restore(chat.text, chat.new_entries) == text
    else:
        assert surface in chat.new_entries.values()


@pytest.mark.parametrize('text,value', [
    ('USD One Hundred and cents fifty', '100.50'),
    ('USD One Hundred and 25/100', '100.25'),
    ('Say One Hundred Only; USD 100', '100'),
    ('one-hundred dollars', '100'),
])
def test_t54_english_variants(text, value):
    hits = detect.find_amounts(text)
    assert hits and all(h.value == Decimal(value) and h.currency == 'USD' for h in hits)


def test_t54_prompt_phone_and_amount_contract():
    from pathlib import Path
    from itertools import product
    from app.review.requirements import load_system_template, select_builtin
    from app.review.prompt import chat_system_prompt
    texts = [load_system_template()[1], chat_system_prompt()[1]]
    for kind, stance, source in product(('contract', 'quotation', 'order', 'inquiry', 'letter'),
            ('sales', 'purchase', 'other'), ('outgoing', 'incoming')):
        texts.extend(r.text for r in select_builtin(kind, stance, source))
    for path in (Path(__file__).parents[1] / 'resources').rglob('*.txt'):
        if 'prompt' in str(path):
            texts.append(path.read_text(encoding='utf-8'))
    assert all(not any(r.category in ('PHONE', 'AMOUNT') for r in recheck(t, real_values=[], terms=[]))
               for t in texts)
