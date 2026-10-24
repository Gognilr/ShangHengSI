"""人名、地址、数量识别（M4-01，设计 docs/design/M4.md 2.1）。每个标签、称谓、停止条件、单位都有正例和反例。"""
import pytest

from app.redaction.detect import find_addresses, find_persons, find_quantities


def persons(text: str) -> list[str]:
    return [text[h.start:h.end] for h in find_persons(text)]


def addresses(text: str) -> list[str]:
    return [text[h.start:h.end] for h in find_addresses(text)]


def quantities(text: str, column: str | None = None) -> list[str]:
    return [text[h.start:h.end] for h in find_quantities(text, column)]


# ---------------------------------------------------------------- 人名


@pytest.mark.parametrize("label", [
    "联系人", "授权代表", "法定代表人", "委托代理人", "经办人", "签字", "签名", "签字人", "代表人", "收件人",
])
def test_chinese_labels(label):
    assert persons(f"{label}：张三") == ["张三"]
    assert persons(f"{label}: 李四丰") == ["李四丰"]


def test_chinese_label_without_colon_only_for_unambiguous_labels():
    # 不带冒号时标签与人名之间须有空白或填空括号（Q-134 ②）：“联系人张三”不再识别，避免“联系人电话”“授权代表应签署”
    assert persons("联系人 张三，电话") == ["张三"]
    assert persons("联系人张三，电话") == []
    assert persons("法定代表人 王小明") == ["王小明"]
    assert persons("法定代表人【王小明】") == ["王小明"]


@pytest.mark.parametrize("text", [
    "授权代表：\t授权代表：", "授权代表：            授权代表：", "联系人电话：0531-6612345", "联系人：电话：0531-6612345",
    "联系人姓名：", "法定代表人或授权委托人姓名：", "双方授权代表应签署初验证书", "授权代表：签字", "法定代表人（签字）：盖章",
    "经办人：日期：", "委托代理人：（签章）", "联系人：【】",
])
def test_signature_blocks_have_no_person(text):
    """签字栏、标签连写、标签后接另一个标签时不识别人名（B-03，Q-134 ②）。"""
    assert persons(text) == []


def test_signature_block_with_names_still_found():
    assert persons("授权代表：郑虚构\t授权代表：柳示例") == ["郑虚构", "柳示例"]
    assert persons("联系人：【李虚构】，电话：【0531-6612345】") == ["李虚构"]
    # OCR 把人名与下一个标签连成一串：截断后的冒号属于“电话”
    assert persons("联系人：周示例电话：13800138000") == ["周示例"]
    # 签字、签名必须有冒号：“签字盖章”“签名页”不是标签
    assert persons("签字盖章") == []
    assert persons("签名页") == []


def test_chinese_title_stripped_and_length_rules():
    assert persons("联系人：张三先生") == ["张三"]
    assert persons("联系人：李四女士") == ["李四"]
    assert persons("联系人：王五总经理") == ["王五"]
    assert persons("联系人：赵六经理，电话") == ["赵六"]
    assert persons("联系人：欧阳小明") == ["欧阳小明"]
    # V2Q-33：姓加称谓单独识别；原输入不变，其余姓名长度规则仍保留。
    assert persons("联系人：王经理") == ["王经理"]
    assert persons("联系人：欧阳小明明白") == []
    assert persons("联系人：张") == []


def test_chinese_stops_at_punctuation_and_info_words():
    assert persons("联系人：张三（销售经理）") == ["张三"]
    assert persons("联系人：张三 13800000000") == ["张三"]
    assert persons("联系人：张三电话13800000000") == ["张三"]
    assert persons("联系人：张三，手机") == ["张三"]


def test_chinese_not_person():
    assert persons("联系人：采购部") == []
    assert persons("联系人：同上") == []
    assert persons("联系人：恒远公司") == []
    assert persons("联系人：") == []


@pytest.mark.parametrize("label", [
    "Attn", "Attention", "Contact", "Contact Person", "Signed by", "Signature", "Name", "Representative",
    "attn", "ATTENTION", "contact person",
])
def test_english_labels(label):
    assert persons(f"{label}: John Smith") == ["John Smith"]


def test_english_label_without_colon_only_for_unambiguous_labels():
    assert persons("Attn Mr. John Smith") == ["John Smith"]
    assert persons("Signed by John Smith") == ["John Smith"]
    assert persons("Attention Zhang Wei") == ["Zhang Wei"]
    # Contact / Name / Signature 等必须有冒号
    assert persons("Contact us at once") == []
    assert persons("Name of the Buyer") == []


def test_english_titles_and_word_count():
    assert persons("Attn: Mr. John Smith") == ["John Smith"]
    assert persons("Attn: Mrs Mary Jones") == ["Mary Jones"]
    assert persons("Attn: Ms.Li Hua") == ["Li Hua"]
    assert persons("Attn: Dr. Wang") == ["Wang"]
    assert persons("Attn: Prof. Anna Lee") == ["Anna Lee"]
    assert persons("Attn: John Albert Smith Junior") == ["John Albert Smith"]  # 最多 3 个单词
    assert persons("Signed by John A. Smith") == ["John A. Smith"]
    # 称谓只在单词边界上去掉：Drake 不是 Dr + ake
    assert persons("Attn: Drake Chen") == ["Drake Chen"]


def test_english_stop_conditions():
    assert persons("Attn: John Smith, Sales Manager") == ["John Smith"]
    assert persons("Attn: John Smith Sales Manager") == ["John Smith"]
    assert persons("Attn: John Smith TEL: 12345") == ["John Smith"]
    assert persons("Attn: John Smith Tel 12345") == ["John Smith"]
    assert persons("Contact: Purchasing Department") == []
    assert persons("Attn: Sales Team") == []
    assert persons("Attn: Mr. Smith.") == ["Smith"]
    # 小写开头的单词不是姓名
    assert persons("Name: of the buyer") == []
    assert persons("Attn: john smith") == []
    # common_terms 中的用语（整句）开头的单词停止
    assert persons("Attn: Smith Force Majeure") == ["Smith"]


def test_english_company_after_label_is_not_person():
    assert persons("Company Name: ABC Trading Ltd") == []
    assert persons("Name: Example Co., Ltd.") == []


def test_person_hit_fields():
    assert persons("联系人：Zhang Wei") == ["Zhang Wei"]  # 中文标签后可以是英文姓名
    hit = find_persons("Attn: Zhang   Wei")[0]
    assert hit.category == "PERSON"
    assert hit.key == "zhangwei"  # 规范形式：去空白、不区分大小写
    hit2 = find_persons("联系人：张三")[0]
    assert (hit2.start, hit2.end, hit2.key) == (4, 6, "张三")


# ---------------------------------------------------------------- 地址


@pytest.mark.parametrize("label", [
    "地址", "注册地址", "通讯地址", "办公地址", "联系地址", "收货地址", "Address", "Registered Address", "Add.", "Add", "address",
])
def test_address_labels(label):
    assert addresses(f"{label}：Room 1, Fictive Road") == ["Room 1, Fictive Road"]
    assert addresses(f"{label}: Room 1, Fictive Road") == ["Room 1, Fictive Road"]


def test_address_requires_colon():
    assert addresses("地址 上海市示例路1号") == ["上海市示例路1号"]  # V2Q-32：无标签中国地址新规则，用户确认同步期望。
    assert addresses("Address Room 1") == []
    assert addresses("our address is Room 1") == []


def test_address_ends_at_line_end_or_next_info_label():
    assert addresses("地址：上海市示例路1号\n电话：021-0000") == ["上海市示例路1号"]
    assert addresses("地址：上海市示例路1号 电话：021-00000000") == ["上海市示例路1号"]
    assert addresses("Address: Room 1, Fictive Road, Dubai  Tel: +971 4 000 0000") == ["Room 1, Fictive Road, Dubai"]
    assert addresses("地址：A路1号；联系人：张三") == ["A路1号"]
    assert addresses("Address: Unit 5 Fax: 123") == ["Unit 5"]
    assert addresses("Address: Unit 5 E-mail: a@b.com") == ["Unit 5"]
    assert addresses("地址：A路1号 地址：B路2号") == ["A路1号", "B路2号"]


def test_address_strips_edges_and_empty_is_ignored():
    assert addresses("地址：  上海市示例路1号。 ") == ["上海市示例路1号"]
    assert addresses("地址：") == []
    assert addresses("地址：  \n电话：1") == []
    assert addresses("地址： ，") == []


def test_address_key_is_country():
    assert find_addresses("Address: Jebel Ali Free Zone, Dubai")[0].key == "阿联酋"
    assert find_addresses("地址：中国上海市示例路1号")[0].key == "中国"
    assert find_addresses("地址：示例路1号")[0].key == "国家未知"
    assert find_addresses("地址：示例路1号")[0].category == "ADDR"


# ---------------------------------------------------------------- 数量


@pytest.mark.parametrize("unit", [
    "MT", "吨", "公斤", "千克", "kg", "KG", "件", "套", "台", "箱", "桶", "个", "批", "pcs", "PCS", "sets", "units", "cartons",
    "bags", "drums", "mt",
])
def test_quantity_units(unit):
    text = f"共 1,250.5 {unit} 货物"
    assert quantities(text) == ["1,250.5"]
    assert quantities(f"共1250{unit}") == ["1250"]


def test_quantity_not_matched_without_unit_or_with_trailing_letter():
    assert quantities("共 1250 货物") == []
    assert quantities("1250 MTS") == []  # 单位后紧跟字母
    assert quantities("100 kgs") == []
    assert quantities("100 setsX") == []
    assert quantities("USD 100/MT") == []


def test_quantity_excludes_durations():
    assert quantities("5 个工作日内付款") == []
    assert quantities("3个月") == []
    assert quantities("2 个自然日") == []
    assert quantities("共 5 个") == ["5"]


def test_quantity_multiple_and_ten_thousand_tons():
    assert quantities("A 10 MT，B 20 吨") == ["10", "20"]
    assert quantities("年产 2万吨") == ["2"]  # “万吨”是一个单位，key 中单位为“万吨”
    assert find_quantities("年产 2万吨")[0].key == "QTY|万吨|2"


def test_quantity_keys_same_unit_same_value():
    a = find_quantities("100 MT")[0]
    b = find_quantities("100.00 mt")[0]
    c = find_quantities("100 吨")[0]
    d = find_quantities("200 MT")[0]
    assert a.key == b.key == "QTY|mt|100"
    assert c.key != a.key and d.key != a.key
    assert a.category == "QTY"


def test_quantity_column():
    assert quantities("1,200", "QTY") == ["1,200"]
    assert quantities("1200.5", "QTY") == ["1200.5"]
    assert find_quantities("1200", "QTY")[0].key == "QTY||1200"
    # 不在数量列时整格数字不算数量
    assert quantities("1200") == []
    assert quantities("1200", "PRICE") == []
    # 数量列中带单位的仍只替换数字
    assert quantities("1200 MT", "QTY") == ["1200"]
    # 数量列中不是整格数字的不算
    assert quantities("约 1200", "QTY") == []


def test_person_label_with_parenthetical_note():
    # M4-04 评审补充：标签后带括号说明再接冒号（签字栏常见写法）
    assert persons("法定代表人（签字）：吴虚构") == ["吴虚构"]
    assert persons("授权代表(签章)：Kevin Lee") == ["Kevin Lee"]
    assert persons("签字（盖章）：李示例") == ["李示例"]
    # 括号说明后没有冒号、括号过长时不算标签
    assert persons("法定代表人（签字）吴虚构") == []
    assert persons("联系人（以下各项均须填写完整）：王示例") == []
