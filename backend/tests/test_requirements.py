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
    assert prefixes == {"G": 12, "C": 19, "Q": 6, "O": 4, "I": 4, "LT": 4}
    assert len(ids) == len(set(ids))


def test_builtin_version_readable():
    assert builtin_version() == "2026.11-1"


def test_system_template_version_and_placeholder():
    version, body = load_system_template()
    assert version == "2026.11-3"  # V2-E-05/06：法条依据及公司范本；仅同步提示词版本（V2Q-96）。
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


def test_select_builtin_any_type_filters_only_stance_and_source():
    from app.review.requirements import select_builtin_any_type

    got = select_builtin_any_type("sales", "outgoing")
    ids = _ids([r for r, _ in got])
    # 任何类型下的结果是各类型结果的并集，且保持 YAML 顺序
    union = set()
    for t in ["contract", "quotation", "order", "inquiry", "letter"]:
        union |= set(_ids(select_builtin(t, "sales", "outgoing")))
    assert set(ids) == union
    assert all(isinstance(types, list) and types for _, types in got)


def test_v2_requirement_texts_verbatim():
    from app.review.requirements import _builtin_data
    expected = {
    'G-06': '交易对象性质（如军方、军工单位、受限政府机构）、目的地及转运迹象（如买方与收货人不在同一国家、由货代收货）、最终用途描述，是否存在制裁或出口管制风险；交易各方均为境内主体、货物不出境且不涉及出口管制物项时判不适用',
    'G-07': '是否出现可能被视为规避制裁或出口管制的表述，如安排第三国转运、品名或原产地描述不实、拆分发货、要求特殊标识；不涉及进出口时判不适用',
    'G-08': '跨境付款的路径与币种风险，如由第三方付款、经受限银行付款；不涉及跨境付款时判不适用',
    'G-09': '产品是否可能属于中国出口管制物项、是否需要出口许可（结合公司说明；最新规定以合规人员确认为准）；货物不出境且无出口迹象时判不适用',
    'G-10': '是否要求我方承诺遵守外国制裁法律或配合外国制裁措施，可能与中国反外国制裁相关规定冲突；文件未涉及外国法律或制裁措施时判不适用',
    'C-01': '价格条款是否明确：是否含税及适用税率、币种、价格调整机制；跨境交易还看贸易术语',
    'C-03': '交付（交货、安装、实施）的时间、地点、方式，风险转移时点；涉及运输的，运输与保险责任',
    'C-04': '数量、包装与标识要求；大宗散装货物还看溢短装比例',
    'C-10': '保密条款；知识产权的归属与许可范围（含定制开发成果、软件源代码、技术资料与数据），我方交付后的使用、修改与转让权利',
    'C-12': '涉及境外主体、进出口或出口管制物项时：是否有出口管制与制裁合规条款、最终用户声明、禁止转售条款，相关条款对我方是否过苛；不涉及时判不适用',
    'C-14': '供应商质量保证与退换货；延迟交付违约金及其上限；预付款风险与担保；供应商免责条款',
    'Q-02': '价格条款：是否含税及适用税率、币种是否明确；跨境报价还看贸易术语',
    'I-01': '需求描述是否清楚：规格、数量、交期；跨境询价还看贸易术语',
    'C-15': '质保期的起算点与期限、质保金比例与退还条件、保修响应时限与费用承担',
    'C-16': '发票类型（增值税专用发票或普通发票）、税率与开票时点，开票与付款节点的先后（如先票后款）；税率是否与现行规定一致需人工确认',
    'C-17': '验收标准、程序与期限是否明确；“视为验收合格”的条件；验收与付款、质保起算的关系',
    'C-18': '是否允许分包或转包、是否须经我方书面同意；关键人员更换与履约资质要求',
    'C-19': '涉及现场施工、安装或驻场服务时，安全生产、现场管理与人身财产损害责任的划分；不涉及时判不适用',
    }
    actual = {r['id']: r['text'] for r in _builtin_data()['requirements']}
    assert {k: actual[k] for k in expected} == expected


def test_v2_contract_selection_count_and_order():
    assert _ids(select_builtin('contract', 'purchase', 'incoming')) == (
        [f'G-{i:02}' for i in range(1, 11)] + ['G-12'] +
        [f'C-{i:02}' for i in range(1, 13)] + [f'C-{i:02}' for i in range(14, 20)])
    sales = _ids(select_builtin('contract', 'sales', 'outgoing'))
    assert len(sales) == 29 and 'C-13' in sales and 'C-14' not in sales  # V2-A-09 补齐用例 4
