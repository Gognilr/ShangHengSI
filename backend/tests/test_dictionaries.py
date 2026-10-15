"""国家、企业类型、常见用语词典（M2-05，SPEC 9.4、9.5）。纯函数测试，不需要数据库。"""
from collections import Counter

import yaml

from app.redaction.dictionaries import (
    RESOURCES_DIR,
    common_terms,
    find_country,
    find_org_type,
)


def _countries():
    with open(RESOURCES_DIR / "countries.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["countries"]


def test_countries_yaml_loads_and_has_enough_entries():
    countries = _countries()
    assert len(countries) >= 180
    for entry in countries:
        assert entry["name"]
        assert isinstance(entry.get("aliases", []), list)
        assert isinstance(entry.get("places", []), list)


def test_no_duplicate_terms_across_names_aliases_and_places():
    # 同一词条不能属于两个国家（大小写不敏感）
    terms = []
    for entry in _countries():
        terms.append(entry["name"])
        terms.extend(entry.get("aliases", []))
        terms.extend(entry.get("places", []))
    counts = Counter(t.casefold() for t in terms)
    duplicates = sorted(t for t, n in counts.items() if n > 1)
    assert duplicates == []


def test_org_types_yaml_structure():
    with open(RESOURCES_DIR / "org_types.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    names = [t["name"] for t in data["types"]]
    assert names == ["贸易商", "货代", "银行"]
    assert data["default"] == "类型未知"


def test_common_terms_count_and_comments_skipped():
    terms = common_terms()
    assert len(terms) >= 150
    assert "Force Majeure" in terms
    assert "Letter of Credit" in terms
    assert not any(t.startswith("#") for t in terms)
    assert "" not in terms


def test_find_country_chinese_and_english_places():
    assert find_country("货物经迪拜转运") == "阿联酋"
    assert find_country("Shipment via Jebel Ali") == "阿联酋"
    assert find_country("Port: Mersin") == "土耳其"
    assert find_country("Delivered to Hamburg") == "德国"


def test_find_country_english_word_boundary():
    # Iran 不应命中 Irani；Irani 本身也不是词条
    assert find_country("Irani merchant") is None
    assert find_country("Iran merchant") == "伊朗"


def test_find_country_case_insensitive():
    assert find_country("shipped from germany") == "德国"
    assert find_country("SAUDI ARABIA") == "沙特阿拉伯"


def test_find_country_longest_match_wins():
    # 南苏丹 包含 Sudan，应取更长的 South Sudan
    assert find_country("Juba, South Sudan") == "南苏丹"
    # Democratic Republic of the Congo 包含 Republic of the Congo
    assert find_country("Kinshasa, Democratic Republic of the Congo") == "刚果（金）"
    # 几内亚 与 巴布亚新几内亚
    assert find_country("Port Moresby, Papua New Guinea") == "巴布亚新几内亚"


def test_find_country_unknown():
    assert find_country("北京市朝阳区某某路") is None
    assert find_country("") is None


def test_find_country_chinese_name_substring():
    assert find_country("买方注册于中华人民共和国") == "中国"


def test_find_org_type_trading():
    assert find_org_type("ABC Trading Co., Ltd.") == "贸易商"
    assert find_org_type("上海某某贸易有限公司") == "贸易商"


def test_find_org_type_forwarder():
    assert find_org_type("Global Logistics Ltd") == "货代"
    assert find_org_type("Ocean Shipping Agency") == "货代"
    assert find_org_type("Fast Freight Inc") == "货代"
    assert find_org_type("深圳某某物流有限公司") == "货代"
    assert find_org_type("某某货运代理") == "货代"


def test_find_org_type_bank():
    assert find_org_type("Bank of Dubai") == "银行"
    assert find_org_type("某某银行股份有限公司") == "银行"


def test_find_org_type_unknown():
    assert find_org_type("XYZ Corp") == "类型未知"
    assert find_org_type("") == "类型未知"


def test_find_org_type_english_word_boundary():
    # Banking 不是 Bank 的单词，不命中
    assert find_org_type("Banking Partners") == "类型未知"
