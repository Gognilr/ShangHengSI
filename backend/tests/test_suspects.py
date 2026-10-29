"""疑似遗漏识别（M4-02，SPEC 9.5，设计 docs/design/M4.md 2.3，Q-69、Q-74）。四条规则及每个排除项都有正例和反例。"""
from pathlib import Path

import pytest
import yaml

from app.redaction.engine import NodeIn, compute
from app.redaction.suspects import find_suspects

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "redaction_golden"


def found(text: str, occupied=()) -> list[tuple[str, str]]:
    return [(text[s.start:s.end], s.reason) for s in find_suspects(text, list(occupied))]


# ---------------------------------------------------------------- org_suffix


@pytest.mark.parametrize("suffix", ["股份有限公司", "有限责任公司", "集团有限公司", "有限公司", "集团", "研究院", "研究所"])
def test_org_suffix_chinese(suffix):
    name = f"恒远示例{suffix}"
    assert found(f"请联系{name}确认") == [(name, "org_suffix")]
    assert found(f"{name}。") == [(name, "org_suffix")]


def test_org_suffix_chinese_boundary_words_and_length():
    assert found("货物由恒远虚构物流有限公司代为装运") == [("恒远虚构物流有限公司", "org_suffix")]
    assert found("委托星河研究院办理") == [("星河研究院", "org_suffix")]
    assert found("南方示例集团公司签署") == [("南方示例集团公司", "org_suffix")]
    # 名称取后缀前最多 10 个汉字
    assert found("甲乙丙丁戊己庚辛壬癸子丑寅卯示例有限公司") == [("庚辛壬癸子丑寅卯示例有限公司", "org_suffix")]
    # 后缀前不足 2 个汉字：不列
    assert found("某有限公司") == []
    assert found("有限公司") == []
    # 泛称：整段等于泛称时不列
    assert found("关联集团的子公司") == []  # “关联”是泛称；“的”之前的文字不并入名称，“子”不足 2 个汉字


def test_org_suffix_not_for_bare_company_or_factory():
    # 单独的“公司、厂”走 before_company，不是 org_suffix
    assert found("恒远示例公司")[0][1] == "before_company"
    assert found("恒远示例钢厂")[0][1] == "before_company"


def test_org_suffix_english():
    assert found("Contact Aurora Fictive Energy Co., Ltd. today") == [("Aurora Fictive Energy Co., Ltd.", "org_suffix")]
    assert found("shipped by Cedar Demo Trading Inc.") == [("Cedar Demo Trading Inc.", "org_suffix")]
    assert found("Delta Example GmbH") == [("Delta Example GmbH", "org_suffix")]
    assert found("Example (HK) Limited") == [("Example (HK) Limited", "org_suffix")]
    # 句首的 Contact 等虚词、称呼不并入名称
    assert found("Contact Aurora Fictive Energy Co., Ltd. today") == [("Aurora Fictive Energy Co., Ltd.", "org_suffix")]
    # 小写的 limited / ltd 不是企业后缀；前面没有首字母大写单词时不列
    assert found("the seller gives a limited warranty") == []
    assert found("a Ltd") == []
    assert found("Ltd.") == []


# ---------------------------------------------------------------- before_company


def test_before_company_positive_and_boundaries():
    assert found("交给恒远示例公司处理") == [("恒远示例公司", "before_company")]
    assert found("货物由恒远示例公司代为装运") == [("恒远示例公司", "before_company")]
    assert found("恒远示例钢厂出具质保书") == [("恒远示例钢厂", "before_company")]
    # 最多 10 个汉字
    long = "一二三四五六七八九十甲乙公司"
    assert found(long) == [("三四五六七八九十甲乙公司", "before_company")]


@pytest.mark.parametrize("text", [
    "本公司", "贵公司", "我方公司", "该公司", "对方公司", "双方公司", "各公司", "其公司", "任何公司", "子公司", "分公司",
    "母公司", "总公司", "关联公司", "船公司", "保险公司", "物流公司", "货运公司", "运输公司", "快递公司", "航空公司",
])
def test_before_company_generic_words_excluded(text):
    assert found(f"由{text}负责") == []


@pytest.mark.parametrize("text", ["以下模块仅本公司不限", "由我公司负责", "须经贵公司确认", "仅限我方公司", "仅对方公司使用"])
def test_before_company_generic_word_directly_before_company(text):
    assert found(text) == []


def test_before_company_fictional_name_still_listed():
    assert found("由虚构科技公司负责") == [("虚构科技公司", "before_company")]


@pytest.mark.parametrize(("text", "name"), [("由示例资本公司负责", "示例资本公司"), ("委托虚构日本公司代理", "虚构日本公司")])
def test_before_company_single_char_generic_inside_name_still_listed(text, name):
    """单字泛指词（本）前面是名称的一部分（资本、日本）时不是泛指，仍列（R1-10）。"""
    assert found(text) == [(name, "before_company")]


@pytest.mark.parametrize("text", ["本公司负责", "，我公司负责", "仅限本公司使用"])
def test_before_company_single_char_generic_after_function_word_or_start(text):
    assert found(text) == []


def test_before_company_factory_words_excluded():
    assert found("出厂价格") == []
    assert found("货物出厂前检验") == []
    assert found("厂家与厂商") == []
    assert found("工厂内部") == []  # 单字“工”不足 2 个汉字


# ---------------------------------------------------------------- capitalized


def test_capitalized_positive():
    assert found("Supplier is Aurora Fictive today") == [("Aurora Fictive", "capitalized")]
    assert found("Ship via Northwind Demo Freight now") == [("Northwind Demo Freight", "capitalized")]
    assert found("Zhang Wei will sign") == [("Zhang Wei", "capitalized")]
    # 句末句号不属于名称；中间用多个空格
    assert found("signed by Zhang Wei.") == [("Zhang Wei", "capitalized")]
    assert found("signed by Zhang   Wei") == [("Zhang   Wei", "capitalized")]


def test_capitalized_negative_single_and_abbreviations():
    assert found("Aurora only") == []
    assert found("price FOB CIF USD HS") == []  # 全大写 2–5 字母缩写不算首字母大写的单词
    assert found("Annex A and Annex B") == []  # 单个大写字母不算
    assert found("lowercase words only") == []
    # 换行、标点把单词隔开
    assert found("Aurora\nFictive") == []
    assert found("Aurora, Fictive") == []
    assert found("Aurora 中文 Fictive") == []


def test_capitalized_long_all_caps_counts():
    assert found("by FICTIVE ENERGY today") == [("FICTIVE ENERGY", "capitalized")]


def test_capitalized_common_terms_excluded():
    assert found("subject to Force Majeure") == []
    assert found("under Letter Of Credit terms") == []  # 不区分大小写
    assert found("payment by Letter of Credit") == []
    assert found("The Governing Law applies") == []  # 句首虚词去掉后整段属于常见用语


def test_capitalized_places_months_weekdays_excluded():
    assert found("shipped from Jebel Ali Port") == []
    assert found("arrive in Dubai") == []
    assert found("loading at Mersin Turkey") == []  # 城市 + 国家
    assert found("on Monday October") == []
    assert found("Hamburg Germany") == []
    assert found("in Hong Kong") == []


def test_capitalized_function_words_stripped_from_start():
    assert found("The Seller shall") == []  # Seller 是常见单词，The 去掉后只剩一个词
    assert found("The Aurora Fictive shall") == [("Aurora Fictive", "capitalized")]
    assert found("This Agreement") == []


# ---------------------------------------------------------------- long_digits


@pytest.mark.parametrize("text", [
    "《智能建筑设计规范》（GB 50312-2007）", "GB50312-2007", "GB/T 22239-2019", "GB / T 22239-2019", "ISO 20000-2018",
    "JGJ 12345678-2008", "依据 DL/T 12345-2008 执行", "ASTM 12345-2010",
])
def test_long_digits_standard_numbers_excluded(text):
    assert found(text) == []


@pytest.mark.parametrize("text", ["GB 12345678", "账号 1234 5678 9012", "编号 50312-2007", "GB 50312-20071"])
def test_long_digits_not_standard_still_listed(text):
    assert [r for _t, r in found(text)] == ["long_digits"]


def test_long_digits_positive():
    assert found("账号 62220000111122223333 已付") == [("62220000111122223333", "long_digits")]
    assert found("编号12345678") == [("12345678", "long_digits")]
    assert found("12345678") == [("12345678", "long_digits")]


def test_long_digits_groups_with_space_or_hyphen():
    # Q-69：分组的账号（19 位）
    assert found("6222 0000 1111 2222 333") == [("6222 0000 1111 2222 333", "long_digits")]
    assert found("收款账号：6222 0000 1111 2222 333 户名") == [("6222 0000 1111 2222 333", "long_digits")]
    assert found("2026-0925-1234") == [("2026-0925-1234", "long_digits")]
    assert found("400-820-8820") == [("400-820-8820", "long_digits")]
    # 两个空格或其他字符会打断
    assert found("1234  5678") == []
    assert found("1234, 5678") == []
    assert found("1234567") == []  # 7 位


def test_long_digits_ocr_garbled_label():
    # OCR 把“收款账号”识别成别的字，账号前没有可识别的标签（Q-69）
    assert found("牧歉帐辱：6217 0000 1234 5678 901") == [("6217 0000 1234 5678 901", "long_digits")]


@pytest.mark.parametrize("text", [
    "2026-10-20", "2026/10/20", "2026.10.20", "20261020", "签订日期 20261020 止",
])
def test_long_digits_dates_excluded(text):
    assert found(text) == []


def test_long_digits_invalid_date_still_listed():
    assert found("20261340") == [("20261340", "long_digits")]  # 13 月 40 日不是日期
    assert found("2026-13-40") == [("2026-13-40", "long_digits")]


@pytest.mark.parametrize("text", [
    "HS 27131100 coke", "HS Code: 2713 1100", "H.S. 27131100", "海关编码 2713110000", "商品编码：27131100", "税则号 27131100",
])
def test_long_digits_hs_excluded(text):
    assert found(text) == []


def test_long_digits_hs_must_be_a_word():
    # “months”里的 hs 不是 HS 字样
    assert found("12 months 12345678") == [("12345678", "long_digits")]
    assert found("HS编码 27131100") == []  # 12 个字符内有“HS”（后面紧跟汉字，不是字母）


@pytest.mark.parametrize("text", ["12345678 MT", "12345678吨", "12345678 kg", "12,345,678 pcs", "10000000 件"])
def test_long_digits_quantity_excluded(text):
    assert found(text) == []


def test_long_digits_amounts_excluded():
    assert found("12,345,678") == []
    assert found("12345678.90") == []
    assert found("USD 12,345,678.00") == []
    assert found("0.12345678") == []


def test_long_digits_does_not_join_dates_with_neighbours():
    assert found("2026-10-20 12345678") == [("12345678", "long_digits")]


# ---------------------------------------------------------------- 组合规则

def test_occupied_regions_are_not_listed_and_not_crossed():
    text = "联系恒远示例有限公司 6222 0000 1111 2222 333"
    org_end = text.index(" 6222")
    # 公司名称已被替换：只剩账号
    assert found(text, [(2, org_end)]) == [("6222 0000 1111 2222 333", "long_digits")]
    # 候选不跨越已替换的区间：中间一段被替换后，前后不会连成一个名称
    t2 = "Aurora Fictive Energy"
    assert found(t2, [(7, 14)]) == []
    t3 = "12345678 12345678"
    assert found(t3, [(8, 9)]) == [("12345678", "long_digits"), ("12345678", "long_digits")]
    t4 = "恒远示例有限公司"
    assert found(t4, [(0, 8)]) == []
    assert found(t4, [(3, 4)]) == []  # 名称中间被替换，剩下的部分够不成名称


def test_same_position_keeps_one_rule_with_priority():
    # 带“有限公司”的名称同时满足 org_suffix 和 before_company：只留 org_suffix
    assert found("恒远示例有限公司") == [("恒远示例有限公司", "org_suffix")]
    # 英文：企业后缀名称同时满足连续大写单词：只留 org_suffix（较长）
    assert found("Aurora Fictive Co., Ltd.") == [("Aurora Fictive Co., Ltd.", "org_suffix")]


def test_results_sorted_and_non_overlapping():
    text = "Aurora Fictive 与恒远示例公司，账号 6222 0000 1111 2222 333，Cedar Demo Inc."
    result = find_suspects(text, [])
    assert [s.start for s in result] == sorted(s.start for s in result)
    assert all(a.end <= b.start for a, b in zip(result, result[1:]))
    assert {s.reason for s in result} == {"capitalized", "before_company", "long_digits", "org_suffix"}


def test_offsets_are_in_original_text():
    text = "ab 编号12345678 cd"
    (s,) = find_suspects(text, [])
    assert text[s.start:s.end] == "12345678"


# ---------------------------------------------------------------- 回归集：must_keep 不被列为疑似遗漏


def load(name):
    lines = (GOLDEN / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    ann = yaml.safe_load((GOLDEN / f"{name}.yaml").read_text(encoding="utf-8"))
    if ann.get("loc_prefixed"):
        nodes = [NodeIn(*line.split(" ", 1)) for line in lines if line.strip()]
    else:
        nodes = [NodeIn(f"F1-P{i}", line) for i, line in enumerate(lines, start=1) if line.strip()]
    return nodes, ann


@pytest.mark.parametrize("name", sorted(p.stem for p in GOLDEN.glob("*.txt")))
def test_golden_must_keep_not_suspect(name):
    nodes, ann = load(name)
    result = compute(nodes, ann["stance"])
    for n in nodes:
        occupied = [(i.char_start, i.char_end) for i in result.items if i.loc == n.loc]
        for sus in find_suspects(n.text, occupied):
            listed = n.text[sus.start:sus.end]
            for keep in ann["must_keep"]:
                assert not (keep in listed or listed in keep) or len(listed) < 2, (
                    f"must_keep 被列为疑似遗漏：{keep!r} ← {listed!r}（{n.loc}，{sus.reason}）")


def test_r4_person_narrative_all_occurrences():
    """11.5：样本中的两处陆虚岚都列出；已由标签替换的周示例不重复列出。"""
    nodes, ann = load('persons_narrative')
    result = compute(nodes, ann['stance'])
    names = []
    for n in nodes:
        occupied = [(i.char_start, i.char_end) for i in result.items if i.loc == n.loc]
        names.extend(n.text[s.start:s.end] for s in find_suspects(n.text, occupied) if s.reason == 'person_name')
    # V2Q-44：只对林虚接受真实 NER 的更严格替换，其余姓名仍逐项比较。
    if '林虚' not in names:
        assert any(i.detected_by == 'ner' and i.category == 'PERSON'
                   and i.loc == n.loc and i.char_start <= n.text.index('林虚')
                   and i.char_end >= n.text.index('林虚') + len('林虚')
                   for n in nodes if '林虚' in n.text for i in result.items)
        assert all('林虚' not in text for text in result.redacted.values())
        names.append('林虚')
    assert names == ['陆虚岚', '陆虚岚', '欧阳虚明', '何虚芳', '林虚']
    # V2Q-168：全名加敬称已提升为规则自动替换，敬称及后文保留。
    assert any(i.loc == 'F1-P7' and i.char_start == 0 and i.char_end == 3
               and i.category == 'PERSON' and i.detected_by != 'user' for i in result.items)
    assert all('宋虚婷' not in text for text in result.redacted.values())
    assert result.redacted['F1-P7'].endswith('女士已确认样品合格。')
