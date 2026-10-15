"""内置审核要求与系统提示词（M2-06，SPEC 11.2、12）。纯函数测试，不需要数据库。"""
from collections import Counter

from app.review.requirements import (
    builtin_version,
    load_system_template,
    select_builtin,
)


def _ids(reqs):
    return [r.id for r in reqs]


def test_counts_per_prefix_match_spec():
    # 与 SPEC 第 12 节一致：G 12、C 14、Q 6、O 4、I 4、LT 4
    from app.review.requirements import _builtin_data

    ids = [item["id"] for item in _builtin_data()["requirements"]]
    prefixes = Counter(i.rsplit("-", 1)[0] for i in ids)
    assert prefixes == {"G": 12, "C": 14, "Q": 6, "O": 4, "I": 4, "LT": 4}
    assert len(ids) == len(set(ids))


def test_builtin_version_readable():
    assert builtin_version() == "2026.10-1"


def test_system_template_version_and_placeholder():
    version, body = load_system_template()
    assert version == "2026.10-1"
    assert body.startswith("你是企业商务文件审核助手")
    assert body.count("{JSON_SCHEMA}") == 1
    # front matter 不应出现在正文中
    assert "version:" not in body
    assert "---" not in body


def test_contract_sales_outgoing_includes_general_and_stance_items():
    ids = _ids(select_builtin("contract", "sales", "outgoing"))
    # C-13 只在销售立场出现；C-14 只在采购立场出现
    assert "C-13" in ids
    assert "C-14" not in ids
    # G-11 只在我方发出时出现
    assert "G-11" in ids
    assert "G-12" not in ids
    # 通用与合同条目都在
    assert "G-01" in ids and "C-01" in ids
    # 其他类型的条目不出现
    assert not any(i.startswith("Q-") or i.startswith("O-") for i in ids)


def test_contract_purchase_incoming_stance_and_source():
    ids = _ids(select_builtin("contract", "purchase", "incoming"))
    assert "C-14" in ids
    assert "C-13" not in ids
    # G-12 只在对方发来时出现；G-11 不出现
    assert "G-12" in ids
    assert "G-11" not in ids


def test_stance_other_excludes_sales_and_purchase_items():
    ids = _ids(select_builtin("contract", "other", "outgoing"))
    assert "C-13" not in ids
    assert "C-14" not in ids
    assert "C-01" in ids


def test_inquiry_source_restriction():
    # I-01、I-03 只在我方发出的询价函中；I-04 只在对方发来的询价函中
    outgoing = _ids(select_builtin("inquiry", "other", "outgoing"))
    incoming = _ids(select_builtin("inquiry", "other", "incoming"))
    assert "I-01" in outgoing and "I-03" in outgoing and "I-04" not in outgoing
    assert "I-04" in incoming and "I-01" not in incoming and "I-03" not in incoming
    # I-02 两边都有
    assert "I-02" in outgoing and "I-02" in incoming


def test_letter_only_letter_items_and_general():
    ids = _ids(select_builtin("letter", "other", "incoming"))
    assert {"LT-01", "LT-02", "LT-03", "LT-04"} <= set(ids)
    assert not any(i.startswith("C-") or i.startswith("Q-") for i in ids)
    # G-12 对方发来时出现
    assert "G-12" in ids


def test_order_only_order_items():
    ids = _ids(select_builtin("order", "other", "outgoing"))
    assert {"O-01", "O-02", "O-03", "O-04"} <= set(ids)
    assert not any(i.startswith(("C-", "Q-", "I-", "LT-")) for i in ids)


def test_selection_keeps_yaml_order():
    ids = _ids(select_builtin("contract", "sales", "outgoing"))
    assert ids == sorted(ids, key=lambda i: ["G", "C"].index(i.split("-")[0]) * 100 + int(i.split("-")[1]))


def test_requirement_fields():
    reqs = select_builtin("contract", "sales", "outgoing")
    first = reqs[0]
    assert first.id == "G-01"
    assert first.source == "builtin"
    assert first.dimension == "表述"
    assert first.text.startswith("错别字")


def test_unknown_doc_type_gets_only_general_items():
    ids = _ids(select_builtin("letter-unknown", "other", "outgoing"))
    assert ids and all(i.startswith("G-") for i in ids)
