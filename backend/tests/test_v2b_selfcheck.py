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


@pytest.mark.parametrize('text', ['经理同意后生效', '总经理高兴地表示',
    '授权代表应签署', '代表应签字', '经理须确认', '代表应及时签字'])
def test_t54_non_person(text):
    assert not detect.find_persons(text)


def test_v2q166_modal_surname_with_honorific():
    text = '销售代表应采薇女士'
    hits = detect.find_persons(text)
    assert [(text[h.start:h.end], h.category) for h in hits] == [('应采薇', 'PERSON')]


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
@pytest.mark.parametrize('label', ['联系人 Contact', 'Contact联系人',
    '授权代表 Authorized Representative', 'Authorized Representative 授权代表'])
def test_t54_10_bilingual_person_label(label):
    text = label + '：王立军'
    result = compute([NodeIn('F1-P1', text)], 'other')
    assert result.redacted['F1-P1'] == label + '：人员_1'
    assert len(result.items) == 1 and result.items[0].category == 'PERSON'


@pytest.mark.usefixtures('no_ner')
def test_t54_10_bilingual_seller():
    result = compute([NodeIn('F1-P1', 'Seller卖方：澄川虚构科技有限公司')], 'other')
    assert result.redacted['F1-P1'] == 'Seller卖方：' + result.parties[0].code


@pytest.mark.parametrize('text,value,category', [
    ('合同编号 Contract No.：CCX-2026-0815', 'CCX-2026-0815', 'REFNO'),
    ('Contract No. 合同编号：CCX-2026-0815', 'CCX-2026-0815', 'REFNO'),
    ('银行账号 Account No.：6222000011112222', '6222000011112222', 'ACCOUNT'),
    ('地址 Address：上海市虚构路88号', '上海市虚构路88号', 'ADDR'),
    ('Address地址：上海市虚构路88号', '上海市虚构路88号', 'ADDR')])
def test_t54_10_other_bilingual_labels(text, value, category):
    hits = detect.find_addresses(text) if category == 'ADDR' else detect.find_formatted(text)
    assert any(h.category == category and text[h.start:h.end] == value for h in hits)


@pytest.mark.parametrize('text', ['开户行 Bank：中国工商银行苏州虚构支行',
    '受益人银行 Beneficiary Bank：中国工商银行苏州虚构支行'])
def test_t54_10_bilingual_bank(text):
    from app.redaction import parties
    assert any(text[a:b] == '中国工商银行苏州虚构支行' for label, a, b in parties.find_labeled_parties(text))


@pytest.mark.parametrize('text,name', [('To: 王立军 <wang@fictive.cn>', '王立军'),
    ('"Lorna Fictive" <lorna@fictive.com>', 'Lorna Fictive'),
    ('From: Lorna Fictive', 'Lorna Fictive')])
def test_t54_11_mail_names(text, name):
    assert any(text[h.start:h.end] == name for h in detect.find_persons(text))


@pytest.mark.usefixtures('no_ner')
@pytest.mark.parametrize('name,replaced', [('Purchasing Department', False), ('Lorna Fictive', True)])
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
@pytest.mark.parametrize('text,name', [('Messrs. Northwind Fictive Trading', 'Northwind Fictive Trading'),
    ('M/s Northwind Fictive Trading', 'Northwind Fictive Trading'),
    ('Company Name: Velmora Fictive', 'Velmora Fictive')])
def test_t54_13_org_prefix(text, name):
    result = compute([NodeIn('F1-P1', text)], 'other')
    assert len(result.items) == 1 and result.items[0].category == 'ORG'
    item = result.items[0]
    assert text[item.char_start:item.char_end] == name
    assert 'Trading' not in result.redacted['F1-P1']
    if text.startswith('Messrs.'):
        assert result.redacted['F1-P1'].startswith('Messrs. ')


@pytest.mark.parametrize('text,value,category', [('Account 072-123456-7', '072-123456-7', 'ACCOUNT'),
    ('ИНН 7701234567', '7701234567', 'IDNO'), ('КПП 770101001', '770101001', 'IDNO'),
    ('UEN 201912345K', '201912345K', 'IDNO')])
def test_t54_14_account_and_foreign_tax(text, value, category):
    assert any(h.category == category and text[h.start:h.end] == value for h in detect.find_formatted(text))


def test_t54_14_not_account_manager():
    assert not detect.find_formatted('account manager')


def test_role_word_is_not_name_inside_department_note():
    text = '联系人：张三（销售经理）'
    assert [text[h.start:h.end] for h in detect.find_persons(text)] == ['张三']


@pytest.mark.usefixtures('no_ner')
@pytest.mark.parametrize('name', ['Công ty TNHH Hư Cấu Việt Nam', 'Công ty Cổ phần Hư Cấu Việt Nam',
    'Công ty CP Hư Cấu Việt Nam', 'Tổng công ty Hư Cấu Việt Nam', 'Công ty Trách nhiệm hữu hạn Hư Cấu Việt Nam'])
def test_t54_15_vietnamese_company(name):
    result = compute([NodeIn('F1-P1', 'Notify party: ' + name + ', Hai Phong')], 'other')
    assert len(result.parties) == 1 and result.parties[0].country == '越南'
    assert any(v == name for v in result.mapping.values())
    assert 'Hai Phong' in result.redacted['F1-P1'] and name not in result.redacted['F1-P1']


@pytest.mark.parametrize('text,name', [('Contact person: Иванов Иван Иванович', 'Иванов Иван Иванович'),
    ('Signed by Ivanov I.I., General Director', 'Ivanov I.I.'), ('Иванов И.И.', 'Иванов И.И.')])
def test_t54_16_cyrillic_and_initials(text, name):
    hits = detect.find_persons(text)
    assert [text[h.start:h.end] for h in hits] == [name]


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


@pytest.mark.usefixtures('no_ner')
@pytest.mark.parametrize('text', ['SGS/CCIC 在装货港取样', '提交上海市虚构区人民法院管辖',
    'Disputes: Singapore International Arbitration Centre', '采用 ISO 和 ASTM 标准'])
def test_t54_18_public_institutions(text):
    result = compute([NodeIn('F1-P1', text)], 'other')
    assert result.redacted['F1-P1'] == text
    assert not result.items and not result.suspects


@pytest.mark.usefixtures('no_ner')
def test_t54_18_institution_party_still_private():
    result = compute([NodeIn('F1-P1', '检验方：SGS'), NodeIn('F1-P2', 'SGS 在装货港取样')], 'other')
    assert len(result.parties) == 1
    assert all('SGS' not in t for t in result.redacted.values())
    assert len({i.placeholder for i in result.items}) == 1


def test_t54_18_model_public_institution(monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [ner.Entity(0, 3, 'ORG', 0.99)])
    result = compute([NodeIn('F1-P1', 'SGS')], 'other')
    assert result.redacted['F1-P1'] == 'SGS' and not result.items and not result.suspects


@pytest.mark.usefixtures('no_ner')
def test_t54_19_industry_brand():
    result = compute([NodeIn('F1-P1', '乙方：苏州澄川虚构机电设备有限公司'),
        NodeIn('F1-P2', '澄川虚构负责安装')], 'other')
    assert result.redacted['F1-P2'] == result.parties[0].code + '负责安装'


@pytest.mark.usefixtures('no_ner')
def test_t54_20_cross_node_company():
    result = compute([NodeIn('F1-P1', '买方 Buyer : NORTHWIND FICTIVE'),
        NodeIn('F1-P2', 'TRADING LLC')], 'other')
    assert len(result.parties) == 1 and len(result.items) == 2
    code = result.parties[0].code
    assert result.redacted['F1-P1'] == '买方 Buyer : ' + code
    assert result.redacted['F1-P2'] == code


@pytest.mark.parametrize('text,value', [('货物运至苏州工业园区星湖街328号创意产业园5栋。',
    '苏州工业园区星湖街328号创意产业园5栋'), ('12 Fictive Street, Singapore #02-12',
    '12 Fictive Street, Singapore #02-12')])
def test_t54_18_address_tail(text, value):
    assert any(text[h.start:h.end] == value for h in detect.find_addresses(text))


def test_t54_18_ocr_person():
    text = '联系人 : 王 立军'
    assert any(text[h.start:h.end] == '王 立军' for h in detect.find_persons(text))


def test_v2q170_unlisted_chinese_city_known_limitation():
    from app.redaction.parties import derived_names
    automatic, short = derived_names('无锡澄川虚构机电设备有限公司')
    assert '澄川虚构' not in automatic and '澄川虚构' not in short


@pytest.mark.usefixtures('no_ner')
@pytest.mark.parametrize('text,category,value', [
    ('Email: export@haiyue-example.com', 'EMAIL', 'export@haiyue-example.com'),
    ('Address: Office 405, Fictive Tower, Business Bay, Dubai, United Arab Emirates', 'ADDR',
     'Office 405, Fictive Tower, Business Bay, Dubai, United Arab Emirates')])
def test_v2q171_brand_does_not_split_formatted_or_address(text, category, value):
    result = compute([NodeIn('F1-P1', 'Haiyue Fictive Trading Ltd.'),
        NodeIn('F1-P2', 'Fictive Metals Ltd.'), NodeIn('F1-P3', text)], 'other')
    assert any(i.loc == 'F1-P3' and i.category == category
               and text[i.char_start:i.char_end] == value for i in result.items)


@pytest.mark.usefixtures('no_ner')
def test_v2q171_brand_does_not_split_other_capitalized_name():
    text = 'Meridian Fictive Inspection'
    result = compute([NodeIn('F1-P1', 'Fictive Metals Ltd.'), NodeIn('F1-P2', text)], 'other')
    assert result.redacted['F1-P2'] == text
    assert any(s.loc == 'F1-P2' and s.char_start == 0 and s.char_end == len(text) for s in result.suspects)


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
