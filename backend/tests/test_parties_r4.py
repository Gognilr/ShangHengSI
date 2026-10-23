"""R4-01：设计 2.7 逐条契约；第 1、11 条另由完整 golden 集锁定。"""
import pytest

from app.chat.service import _redact
from app.redaction.engine import NodeIn, compute
from app.redaction import parties
from app.redaction.parties import find_labeled_parties
from app.redaction.suspects import find_suspects


@pytest.mark.parametrize("text,stance,role,code", [
    ("采购人：江北市第一虚构人民医院", "sales", "买方", "客户_A"),
    ("承包人：青岚虚构建设工程有限公司", "purchase", "卖方", "供应商_A"),
    ("监理单位：东川市虚构工程咨询事务所", "sales", "监理方", "公司_A"),
    ("甲方：东川市虚构区教育局", "sales", "甲方", "公司_A"),
])
def test_r4_labeled_parties(text, stance, role, code):
    r = compute([NodeIn("F1-P1", text)], stance)
    assert len(r.parties) == 1
    p = r.parties[0]
    assert (p.role_label, p.code, p.country) == (role, code, "中国")
    assert not r.suspects


@pytest.mark.parametrize("text", ["甲方：负责提供场地所需的水电。", "乙方：本院另行通知"])
def test_r4_not_a_name(text):
    assert find_labeled_parties(text) == []
    assert find_suspects(text, []) == []


def test_r4_body_institution_is_only_suspect():
    text = "由江北市虚构大学负责组织验收"
    r = compute([NodeIn("F1-P1", text)], "sales")
    assert r.redacted["F1-P1"] == text
    assert [(text[s.char_start:s.char_end], s.reason) for s in r.suspects] == [("江北市虚构大学", "before_company")]


def test_r4_generic_institutions():
    assert find_suspects("该医院、本院、定点医院", []) == []


def test_r4_institution_alias():
    text = "江北市第一虚构人民医院（以下简称“江北医院”）……江北医院应……"
    r = compute([NodeIn("F1-P1", text)], "sales")
    assert len(r.parties) == 1
    assert r.redacted["F1-P1"] == "公司_A（以下简称“公司_A”）……公司_A应……"


def test_r4_chat_uses_shared_institution_rules():
    r = _redact("采购人：江北市虚构区教育局。请问付款条件有什么风险？", {}, [])
    assert r.text == "采购人：[公司_Q1]。请问付款条件有什么风险？"
    assert r.new_entries == {"[公司_Q1]": "江北市虚构区教育局"}


@pytest.mark.parametrize("role,word", [(role, word) for role, words in parties._LABELS for word in words])
def test_r4_label_variants_and_generic_aliases(role, word):
    text = f"{word}名称（项目一）：江北市虚构大学"
    assert find_labeled_parties(text) == [(role, text.index("江北"), len(text))]
    assert word.casefold() in parties.GENERIC_WORDS


@pytest.mark.parametrize("suffix", parties._INSTITUTION_SUFFIXES)
def test_r4_institution_suffix_value_end(suffix):
    name = "江北市虚构" + suffix
    assert find_labeled_parties("甲方：" + name) == [("甲方", 3, 3 + len(name))]
    assert find_labeled_parties("甲方：" + name + "另行通知") == []


@pytest.mark.parametrize("prefix", ["应提供", "须提供", "负责", "提供", "按照", "根据", "于", "的", "在"])
def test_r4_sentence_parts_are_not_labeled_institutions(prefix):
    assert find_labeled_parties("甲方：" + prefix + "江北市虚构医院。") == []
