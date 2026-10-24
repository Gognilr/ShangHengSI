"""V2-B-02：设计 2.5 的逐条用例。"""
import pytest

from app.chat.service import _redact
from app.redaction import parties
from app.redaction.engine import NodeIn, compute
from tests.test_redaction_golden import load


@pytest.mark.parametrize("name,category", [("foreign_suffixes", "ORG"), ("cn_address_unlabeled", "ADDR")])
def test_01_02_golden_gaps(name, category):
    nodes, ann, r, text, _ = load(name)
    values = (["Anadolu Fictive Kimya Sanayi ve Ticaret A.Ş.", "ООО «Ромашка Фиктив»",
               "Wisla Fictive Trading Sp. z o.o.", "Vltava Fictive Logistics s.r.o.", "Duna Fictive Chemicals Kft.",
               "Gulf Fictive Trading Co. W.L.L.", "Lumiere Fictive Industries SARL", "Tevere Fictive Impianti S.p.A.",
               "PT Nusantara Fictive Kimia"] if category == "ORG" else
              ["江苏省南京市江宁区虚构路 88 号", "东川市虚构区示例大道 1200 号"])
    for value in values:
        n = next(n for n in nodes if value in n.text)
        assert any(i.category in ({"ORG", "OUR"} if category == "ORG" else {category}) and n.text[i.char_start:i.char_end] == value
                   for i in r.items if i.loc == n.loc), value
        assert value not in text
    if category == "ADDR":
        assert all(i.placeholder == "[地址：中国]" for i in r.items if i.category == "ADDR")


def test_03_turkey():
    r = compute([NodeIn("F1-P1", "Buyer: Anadolu Fictive Kimya Sanayi ve Ticaret A.Ş.")], "sales")
    assert r.parties[0].country == "土耳其"


@pytest.mark.parametrize("text", ["The AB test result", "see section AB"])
def test_04_short_suffix_noise(text):
    assert not compute([NodeIn("F1-P1", text)], "sales").items


def test_05_short_suffix_name():
    r = compute([NodeIn("F1-P1", "Partner: Nordic Fictive Systems AB")], "sales")
    assert r.mapping["公司_A"] == "Nordic Fictive Systems AB"


def test_06_cyrillic_chat():
    assert _redact("ООО «Ромашка Фиктив»", {}, []).text == "[公司_Q1]"


@pytest.mark.parametrize("text", ["货物运至青岛港", "在江苏省南京市签订"])
def test_07_places_retained(text):
    assert compute([NodeIn("F1-P1", text)], "sales").redacted["F1-P1"] == text


def test_08_labeled_address_unchanged():
    r = compute([NodeIn("F1-P1", "地址：江苏省南京市江宁区虚构路 88 号")], "sales")
    assert r.redacted["F1-P1"] == "地址：[地址：中国]"


@pytest.mark.parametrize("suffix", ["A.Ş.", "Ltd. Şti.", "Ltd. Sti.", "Sp. z o.o.", "Sp. z o. o.", "s.r.o.", "a.s.",
    "Kft.", "Zrt.", "Nyrt.", "S.R.L.", "SRL", "SARL", "S.A.R.L.", "S.A.S.", "S.p.A.", "SpA", "Ltda.",
    "S.A. de C.V.", "W.L.L.", "WLL", "Co. W.L.L.", "GmbH & Co. KG", "ApS", "A/S", "Oy", "AB", "ASA", "K.K.", "Bhd."])
def test_suffix_inventory(suffix):
    value = "Fictive Example Trading " + suffix
    assert (0, len(value)) in parties.find_other_org_names(value)


@pytest.mark.parametrize("value", ["Fictive Example Ticaret A.S.", "Fictive Example Trading Est.",
                                  "Fictive Example General Est.", "Fictive Example Commercial Est."])
def test_conditional_suffixes(value):
    assert (0, len(value)) in parties.find_other_org_names(value)


@pytest.mark.parametrize("value", ["Fictive Example Other A.S.", "Fictive Example Other Est.",
                                  "Fictive AB", "Fictive Example ab", "Fictive Example AB equipment"])
def test_conditional_suffix_noise(value):
    assert not parties.find_other_org_names(value)


@pytest.mark.parametrize("prefix", ["ООО", "АО", "ПАО", "ЗАО", "ОАО", "ИП", "ТОО", "OOO", "ZAO", "OAO", "TOO"])
def test_quoted_prefixes(prefix):
    value = prefix + ' «Ромашка Фиктив»'
    assert (0, len(value)) in parties.find_other_org_names(value)


def test_address_narrative_boundaries():
    text = "货物运至江苏省南京市江宁区虚构路 88 号，工期不变。"
    assert compute([NodeIn("F1-P1", text)], "sales").redacted["F1-P1"] == "货物运至[地址：中国]，工期不变。"
