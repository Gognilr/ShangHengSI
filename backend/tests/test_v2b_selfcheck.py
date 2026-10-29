"""V2-B-16～18 自查：golden 样本表达不了的部分（金额数值与币种、NOTE 与追问入口、还原、模型打桩、真实实体识别下会列为疑似的反例）。

纯正文的识别正反例在 tests/fixtures/redaction_golden/cases_*。
"""
from decimal import Decimal

import pytest

from app.redaction import detect, ner
from app.redaction.engine import NodeIn, compute
from app.redaction.recheck import recheck
from tests.test_v2b_amounts import test_prompt_contract, test_fixed_prompt_text, env  # noqa: F401


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


@pytest.mark.parametrize('text', ['微信沟通后确认', 'WeChat Pay 付款', 'QQ群'])
def test_t54_non_im(text):
    assert not any(h.category == 'IM' for h in detect.find_formatted(text))


@pytest.mark.parametrize('text', ['总经理高兴地表示'])
def test_t54_non_person(text):
    assert not detect.find_persons(text)


def test_t54_existing_phone_has_no_duplicate():
    hits = detect.find_formatted('邮编：251500 电话：0531-6612345')
    assert [(h.start, h.end) for h in hits if h.category == 'PHONE'] == [(13, 25)]


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


@pytest.fixture
def no_ner(monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [])


@pytest.mark.usefixtures('no_ner')
@pytest.mark.parametrize('name,replaced', [('Purchasing Department', False)])
def test_t54_12_signoff(name, replaced):
    result = compute([NodeIn('F1-P1', 'Best regards,'), NodeIn('F1-P2', ''),
        NodeIn('F1-P3', name)], 'other')
    assert (name not in result.redacted['F1-P3']) is replaced
    if replaced:
        assert result.items[0].category == 'PERSON'


@pytest.mark.usefixtures('no_ner')
def test_t54_12_same_english_person_everywhere():
    result = compute([NodeIn('F1-P1', 'From: Lorna Fictive'), NodeIn('NOTE', 'LORNA FICTIVE')], 'other')
    assert len(result.items) == 2 and len({i.placeholder for i in result.items}) == 1


@pytest.mark.usefixtures('no_ner')
def test_t54_15_vietnamese_company():
    # 只核对国家；五种企业形式的替换见 golden cases_org_prefixes
    name = 'Công ty TNHH Hư Cấu Việt Nam'
    result = compute([NodeIn('F1-P1', 'Notify party: ' + name + ', Hai Phong')], 'other')
    assert len(result.parties) == 1 and result.parties[0].country == '越南'
    assert any(v == name for v in result.mapping.values())
    assert 'Hai Phong' in result.redacted['F1-P1'] and name not in result.redacted['F1-P1']


@pytest.mark.usefixtures('no_ner')
def test_t54_17_english_brand():
    nodes = [NodeIn('F1-P1', 'Velmora Fictive Industries Pte. Ltd.'), NodeIn('F1-P2', 'Velmora shall'),
        NodeIn('NOTE', "Velmora's bank"), NodeIn('F1-P3', 'Global Fictive Trading Ltd.'),
        NodeIn('F1-P4', 'Global shall')]
    result = compute(nodes, 'other')
    code = result.redacted['F1-P1']
    assert result.redacted['F1-P2'] == code + ' shall'
    assert result.redacted['NOTE'] == code + "'s bank"
    assert result.redacted['F1-P4'] == 'Global shall'


def test_t54_18_model_public_institution(monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [ner.Entity(0, 3, 'ORG', 0.99)])
    result = compute([NodeIn('F1-P1', 'SGS')], 'other')
    assert result.redacted['F1-P1'] == 'SGS' and not result.items and not result.suspects


def test_v2q170_unlisted_chinese_city_known_limitation():
    from app.redaction.parties import derived_names
    automatic, short = derived_names('无锡澄川虚构机电设备有限公司')
    assert '澄川虚构' not in automatic and '澄川虚构' not in short


@pytest.mark.parametrize('score', [0.7, 0.99])
def test_v2q171_known_place_model_kept_in_body_note_chat(monkeypatch, score):
    from app.chat.service import _redact
    text = 'Hai Phong'
    monkeypatch.setattr(ner, 'recognize', lambda value: [ner.Entity(0, len(text), 'ORG', score)])
    for loc in ['F1-P1', 'NOTE']:
        result = compute([NodeIn(loc, text)], 'other')
        assert result.redacted[loc] == text and not result.items and not result.suspects
    chat = _redact(text, {}, [])
    assert chat.text == text and not chat.new_entries and not chat.suspects


@pytest.mark.usefixtures('no_ner')
def test_public_abbreviation_inside_private_company_still_redacted():
    from app.chat.service import _redact
    text = 'SGS Fictive Trading Ltd.'
    body = compute([NodeIn('F1-P1', text)], 'other')
    assert text not in body.redacted['F1-P1']
    assert text not in _redact(text, {}, []).text


@pytest.mark.usefixtures('no_ner')
def test_cross_node_ocr_rule_also_applies_to_multiline_chat():
    from app.chat.service import _redact
    text = '买方 Buyer : NORTHWIND FICTIVE\nTRADING LLC'
    result = _redact(text, {}, [])
    assert 'NORTHWIND' not in result.text and 'FICTIVE' not in result.text and 'TRADING' not in result.text


@pytest.mark.usefixtures('no_ner')
@pytest.mark.parametrize('text', ['Email: export@haiyue-example.com',
    'Meridian Fictive Inspection', 'Address: Office 405, Fictive Tower, Dubai'])
def test_v2q171_chat_saved_brand_alias_does_not_split_context(text):
    from app.chat.service import _redact
    mapping = {'公司_A': 'Haiyue Fictive Trading Ltd.', '公司_A#1': 'Haiyue',
        '公司_B': 'Fictive Metals Ltd.', '公司_B#1': 'Fictive'}
    result = _redact(text, mapping, [])
    if text == 'Meridian Fictive Inspection':
        assert result.text == text
        assert any(s['start'] == 0 and s['end'] == len(text) for s in result.suspects)
    else:
        # 完整格式化 / 地址代号取代原字段，不遗留被别名拆开的部分。
        assert 'export@' not in result.text and 'Tower' not in result.text


def test_v2q171_longer_model_entity_with_place_is_not_filtered(monkeypatch):
    from app.chat.service import _redact
    from app.redaction import parties
    text = 'Hai Phong Fictive Port Co.'
    monkeypatch.setattr(parties, 'find_other_org_names', lambda value: [])
    monkeypatch.setattr(ner, 'recognize', lambda value: [ner.Entity(0, len(text), 'ORG', 0.99)])
    for loc in ['F1-P1', 'NOTE']:
        result = compute([NodeIn(loc, text)], 'other')
        assert text not in result.redacted[loc]
        assert any(i.category == 'ORG' and i.char_start == 0 and i.char_end == len(text) for i in result.items)
    result = _redact(text, {}, [])
    assert text not in result.text and text in result.new_entries.values()


@pytest.mark.usefixtures('no_ner')
def test_v2q171_explicit_person_and_user_term_override_place():
    from app.chat.service import _redact
    from app.redaction.engine import UserTerm
    tagged = 'Contact person: Hai Phong'
    result = compute([NodeIn('F1-P1', tagged)], 'other')
    assert 'Hai Phong' not in result.redacted['F1-P1'] and result.items[0].category == 'PERSON'
    result = compute([NodeIn('NOTE', 'Hai Phong')], 'other', user_terms=[UserTerm('Hai Phong', 'CUSTOM')])
    assert 'Hai Phong' not in result.redacted['NOTE'] and result.items[0].detected_by == 'user'
    assert 'Hai Phong' not in _redact(tagged, {}, []).text
    assert 'Hai Phong' not in _redact('Hai Phong', {}, [('Hai Phong', 'CUSTOM')]).text


@pytest.mark.usefixtures('no_ner')
def test_v2q171_brand_equal_to_formatted_value_keeps_category():
    from app.chat.service import _redact
    name, field = 'HARBORXX Fictive Trading Ltd.', 'SWIFT: HARBORXX'
    result = compute([NodeIn('F1-P1', name), NodeIn('F1-P2', field)], 'other')
    assert any(i.loc == 'F1-P2' and i.category == 'SWIFT' for i in result.items)
    chat = _redact(field, {'公司_A': name}, [])
    assert chat.text.startswith('SWIFT: [SWIFT_')


@pytest.mark.usefixtures('no_ner')
def test_address_extension_is_also_a_recheck_value():
    from app.chat.service import _redact
    text = '货物运至苏州工业园区星湖街328号创意产业园5栋。'
    tail = '创意产业园5栋'
    for loc in ['F1-P1', 'NOTE']:
        result = compute([NodeIn(loc, text)], 'other')
        assert tail in result.mapping.values()
        assert any(r.category == 'MAP' for r in recheck(result.redacted[loc] + '\n补充：' + tail,
            real_values=result.mapping.values(), terms=[]))
    result = _redact(text, {}, [])
    assert tail in result.new_entries.values()
    assert any(r.category == 'MAP' for r in recheck(result.text + '\n补充：' + tail,
        real_values=result.new_entries.values(), terms=[]))


# ---------------------------------------------------------------- V2-B-15 审核修正（V2Q-173）

def _persons(text):
    return [text[h.start:h.end] for h in detect.find_persons(text) if h.category == 'PERSON']


@pytest.mark.usefixtures('no_ner')
def test_v2q173_f1_single_word_english_person_is_case_sensitive_in_chat():
    # 正文见 golden cases_english_single_word_names
    from app.chat.service import _redact
    text = _redact('Dear Mr. Price, the price is high. Mr. Price agrees.', {}, []).text
    assert 'the price is high' in text and 'Price' not in text


# 以下反例在真实实体识别下会被列为疑似，无法写成 golden 的 must_keep，保留识别器层面的断言；其余正反例见 golden cases_*。

@pytest.mark.parametrize('text', ['主管单位：某局'])
def test_v2q173_f2_role_word_before_noun_is_not_person(text):
    assert _persons(text) == []


@pytest.mark.parametrize('text', ['The U.S. market is large.'])
def test_v2q173_f3_abbreviations_are_not_initials(text):
    assert _persons(text) == []


@pytest.mark.parametrize('text', ['See line 1234 of the spec.', 'Production Line 100 is idle.', 'line no_12 ok'])
def test_v2q173_f4_lowercase_line_is_not_im(text):
    assert not any(h.category == 'IM' for h in detect.find_formatted(text))


@pytest.mark.parametrize('text', ['mr Brown said'])
def test_v2q173_f5_ms_without_dot_is_not_title(text):
    assert _persons(text) == []


def test_v2q173_f7_british_and_and_named_dollars():
    text = 'USD 120,000.50 (Say US Dollars One Hundred and Twenty Thousand and Fifty Cents Only)'
    hits = detect.find_amounts(text)
    assert {h.value for h in hits} == {Decimal('120000.50')} and len({h.key for h in hits}) == 1
    for text, code in [('SGD 100,000 (Say Singapore Dollars One Hundred Thousand Only)', 'SGD'),
                       ('HKD 5,000 (Say Hong Kong Dollars Five Thousand Only)', 'HKD'),
                       ('AUD 2,000 (Say Australian Dollars Two Thousand Only)', 'AUD')]:
        hits = detect.find_amounts(text)
        words = [h for h in hits if not text[h.start].isdigit()]
        assert words and words[0].currency == code
        assert len({h.key for h in hits}) == 1


@pytest.mark.usefixtures('no_ner')
def test_v2q173_f8_shared_english_brand_is_not_assigned_but_suspected():
    from app.chat.service import _redact
    lines = ['卖方 Seller: Velmora Fictive Trading Co., Ltd.', '买方 Buyer: Velmora Northwind Industries Ltd.',
             'Velmora shall deliver.']
    result = compute([NodeIn(f'F1-P{i + 1}', t) for i, t in enumerate(lines)], 'other')
    assert not any(i.loc == 'F1-P3' for i in result.items)
    assert any(s.loc == 'F1-P3' and lines[2][s.char_start:s.char_end] == 'Velmora' for s in result.suspects)
    chat = _redact('Velmora shall deliver.', {'卖方_A': 'Velmora Fictive Trading Co., Ltd.',
                                              '买方_B': 'Velmora Northwind Industries Ltd.'}, [])
    assert 'Velmora shall' in chat.text and any(s['text'] == 'Velmora' for s in chat.suspects)


@pytest.mark.parametrize('lines', [['Best regards,', 'Quality Control'], ['Best regards,', 'Purchasing Office']])
def test_v2q173_f10_signoff_department_is_not_person(lines):
    assert detect.find_signature_persons(lines) == []
