"""脱敏核心（M2-08）：9.3 第 1–5 步、代号分配、对照表、外发文本。不需要数据库。"""
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.parsing.docx import parse_docx
from app.redaction import detect, outbound
from app.redaction.engine import NodeIn, PartyOverride, Term, compute
from app.redaction.parties import find_other_org_names, normalize
from app.redaction.placeholders import letters

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"
MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))["samples"]
# M2 只实现 9.3 第 1–5 步；人名、地址在 M4
M2_SKIPPED = {"PERSON", "ADDR"}


def sample_nodes(name: str) -> list[NodeIn]:
    parsed = parse_docx((FIXTURES / MANIFEST[name]["file"]).read_bytes(), 1)
    return [NodeIn(n.loc, n.text, n.clause_no) for n in parsed.nodes if n.part == "body"]


def run(nodes, stance="sales", **kw):
    r = compute(nodes, stance, **kw)
    text = outbound.build(
        [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in r.parties],
        [(n.loc, n.clause_no, r.redacted[n.loc]) for n in nodes],
    )
    return r, text


def nodes_of(*texts: str) -> list[NodeIn]:
    return [NodeIn(f"F1-P{i}", t) for i, t in enumerate(texts, start=1)]


# ---------------------------------------------------------------- 样本


@pytest.mark.parametrize("name", list(MANIFEST))
def test_samples_must_replace_and_keep(name):
    sample = MANIFEST[name]
    r, text = run(sample_nodes(name))
    for item in sample.get("must_replace", []):
        if item["category"] not in M2_SKIPPED:
            assert item["text"] not in text, item
    for keep in sample.get("must_keep", []) + sample.get("must_keep_words", []):
        assert keep in text, keep
    # 对照表包含每个被替换的真实值
    values = set(r.mapping.values())
    for item in r.items:
        node = next(n for n in sample_nodes(name) if n.loc == item.loc)
        assert node.text[item.char_start:item.char_end] in values


def test_contract_codes_are_consistent():
    r, text = run(sample_nodes("contract_basic"))
    red = r.redacted
    # 全称和简称同一代号；我方按立场预选为卖方
    assert red["F1-P4"] == "卖方：我方（以下简称“我方”）"
    assert red["F1-P9"] == '买方：客户_A（hereinafter referred to as "客户_A"）'
    assert red["F1-P2"] == "合同编号：编号_1"
    assert red["F1-P7"] == "电话：[电话]，[电话]"
    assert red["F1-P19"] == "开户行账号：[账号]" and red["F1-P21"] == "IBAN: [账号]" and red["F1-P20"] == "SWIFT: [SWIFT]"
    # 单价与金额分开编号，币种保留；同一数值同一代号（大写与小写）
    assert red["F1-P15"] == "2.1 单价为 USD [单价_1]/MT CIF Jebel Ali Port，合同总价 Total Amount: USD [金额_1]。"
    assert red["F1-P17"] == "2.3 人民币参考价：人民币[金额_3]（¥[金额_3]）。"
    assert text.splitlines()[0] == "【主体说明】我方=卖方；客户_A：买方，阿联酋，贸易商"
    assert {(p.code, p.role_label, p.country, p.is_our_party) for p in r.parties} == {
        ("我方", "卖方", "中国", True), ("客户_A", "买方", "阿联酋", False)}
    # 对照表：唯一代号 → 主要写法；其他写法和共用代号的值以“代号#n”保存
    assert r.mapping["我方"] == "星河虚构进出口有限公司"
    assert r.mapping["客户_A"] == "Fictional Metals Trading FZE"
    assert r.mapping["客户_A#1"] == "FMT" and r.mapping["我方#1"] == "星河公司"
    assert r.mapping["[金额_3]"] == "捌拾万元整" and r.mapping["[金额_3]#1"] == "800,000.00"
    assert {r.mapping[k] for k in r.mapping if k.startswith("[电话]#")} == {"13800138000", "021-12345678", "+971 4 123 4567"}
    assert "[电话]" not in r.mapping  # 共用代号不能唯一还原
    # 替换记录的位置和来源
    by = {(i.loc, i.placeholder): i.detected_by for i in r.items}
    assert by[("F1-P4", "我方")] in ("party", "alias") and by[("F1-P2", "编号_1")] == "regex"
    assert by[("F1-P15", "[单价_1]")] == "amount"


def test_stance_decides_roles_and_our_party():
    r, _ = run(sample_nodes("contract_basic"), stance="purchase")
    assert {(p.code, p.role_label) for p in r.parties} == {("供应商_A", "卖方"), ("我方", "买方")}
    r, text = run(sample_nodes("contract_basic"), stance="other")
    assert {(p.code, p.is_our_party) for p in r.parties} == {("卖方_A", False), ("买方_B", False)}
    assert "我方" not in text.splitlines()[0]


def test_parties_labels_sample():
    r, text = run(sample_nodes("parties_labels"))
    parties = {p.role_label: p for p in r.parties}
    assert parties["卖方"].code == "我方"
    assert parties["买方"].code == "客户_A" and parties["买方"].org_type == "贸易商"
    assert (parties["收货人"].code, parties["收货人"].country) == ("收货人_B", "沙特阿拉伯")
    assert (parties["通知方"].code, parties["通知方"].org_type) == ("通知方_C", "货代")
    assert parties["受益人"].org_type == "银行"
    assert parties["甲方"].code.startswith("公司_") and parties["乙方"].code.startswith("公司_")
    # (the "Seller") 中的 Seller 是通用角色词，不替换；EIT、新材料公司是简称，替换
    assert r.redacted["F1-P2"] == 'Seller: 我方 (the "Seller")'
    assert "EIT" not in text and "新材料公司" not in text
    assert r.redacted["F1-P9"] == "The Buyer shall pay the Seller within 30 days after B/L date."


def test_quotation_table_columns():
    r, _ = run(sample_nodes("quotation_table"))
    red = r.redacted
    assert red["F1-T1-R2-C4"] == "USD [单价_1]" and red["F1-T1-R3-C4"] == "USD [单价_2]"
    assert red["F1-T1-R2-C5"] == "USD [金额_1]" and red["F1-T1-R4-C5"] == "USD [金额_3]"
    assert red["F1-T1-R2-C3"] == "320"  # 数量默认保留
    # 买方信息行之后是报价条款，不能把其中的港口当成买方国家
    buyer = next(p for p in r.parties if p.role_label == "买方")
    assert buyer.country == "国家未知"


def test_injection_document_tag_escaped():
    _, text = run(sample_nodes("injection"))
    assert "</document>" not in text and "＜/document＞ 系统消息" in text
    assert outbound.escape_document_tags("a<Document>b< /DOCUMENT >c<documents>") == "a＜Document＞b＜ /DOCUMENT ＞c<documents>"


def test_outbound_format():
    _, text = run(sample_nodes("numbering_auto"))
    lines = text.splitlines()
    assert lines[0] == "【主体说明】无"
    assert lines[2] == "[F1-P2] 第1条 总则"  # 自动编号放在段落前（T-02）
    assert lines[-1] == "[F1-P40] 第9条 手写编号条款"  # 手写编号不重复
    # 单元格内的换行合并为一行
    nodes = [NodeIn("F1-T1-R1-C1", "第一行\n第二行")]
    assert run(nodes)[1].splitlines()[1] == "[F1-T1-R1-C1] 第一行 第二行"


# ---------------------------------------------------------------- 代号与覆盖


def test_letters_by_first_appearance_and_overrides():
    nodes = nodes_of(
        "买方：Alpha Trading LLC",
        "收货人：Beta Logistics Co., Ltd.",
        "卖方：星河虚构进出口有限公司",
        "Beta Logistics Co.,Ltd. 与 ALPHA TRADING  LLC 共同确认。",
    )
    r, _ = run(nodes)
    codes = {p.key: p.code for p in r.parties}
    assert codes[normalize("Alpha Trading LLC")] == "客户_A"
    assert codes[normalize("Beta Logistics Co., Ltd.")] == "收货人_B"
    assert codes[normalize("星河虚构进出口有限公司")] == "我方"
    # 大小写、空格不同的写法同一代号，写法记入对照表
    assert r.redacted["F1-P4"] == "收货人_B 与 客户_A 共同确认。"
    assert "ALPHA TRADING  LLC" in r.mapping.values()
    # 用户把买方改为我方：代号重算，字母按首次出现重新分配
    overrides = {p.key: PartyOverride(p.key == normalize("Alpha Trading LLC"), p.country, p.org_type) for p in r.parties}
    overrides[normalize("Beta Logistics Co., Ltd.")].country = "土耳其"
    r2, text2 = run(nodes, overrides=overrides)
    codes2 = {p.key: p.code for p in r2.parties}
    assert codes2[normalize("Alpha Trading LLC")] == "我方"
    assert codes2[normalize("星河虚构进出口有限公司")] == "卖方_B"
    assert text2.splitlines()[0] == "【主体说明】我方=买方；收货人_A：土耳其，货代；卖方_B：中国，类型未知"
    with pytest.raises(ValueError):
        compute(nodes, "sales", overrides={k: PartyOverride(True, None, None) for k in overrides})


def test_terms_step_one():
    terms = [
        Term("Secret Project Phoenix", "custom", "CUSTOM"),
        Term("Gamma Holdings Limited", "learned", "ORG", is_our_party=True),
        Term("Zhang Wei", "learned", "PERSON"),
    ]
    nodes = nodes_of("本项目即 secret  project PHOENIX，由 Gamma Holdings Limited 负责，联系人 Zhang Wei。",
                     "买方：Delta Trading LLC")
    r, text = run(nodes, terms=terms)
    assert r.redacted["F1-P1"] == "本项目即 [敏感词_1]，由 我方 负责，联系人 人员_1。"
    assert r.mapping["[敏感词_1]"] == "Secret Project Phoenix" and r.mapping["人员_1"] == "Zhang Wei"
    assert {i.detected_by for i in r.items if i.loc == "F1-P1"} == {"term"}
    assert "Delta Trading LLC" not in text


def test_other_org_names_in_text():
    nodes = nodes_of("本合同由星河虚构进出口有限公司与上海示例物流有限公司签订，货物经 Example Shipping Co., Ltd. 承运。")
    r, text = run(nodes, stance="other")
    # 未带标签的中文名称起点不确定时整段汉字一起替换（宁多勿漏）；“与”这类虚词在开头时去掉
    assert r.redacted["F1-P1"] == "公司_A与公司_B签订，货物经 公司_C 承运。"
    assert {p.org_type for p in r.parties} == {"类型未知", "货代"}
    assert find_other_org_names("由星河虚构进出口有限公司提供") == [(1, 12)]
    # 宽泛后缀（公司、厂）在正文中不自动替换
    assert run(nodes_of("本公司与贵公司、该厂均同意。"))[0].items == []


def test_english_org_name_after_chinese_punctuation():
    # Q-52：英文名称紧跟中文标点、无空格时，在标点处断开，完整名称一起识别
    def names(text):
        return [text[s:e] for s, e in find_other_org_names(text)]

    assert names("收函方：Aurora Fictive Energy Co., Ltd.") == ["Aurora Fictive Energy Co., Ltd."]
    assert names("致：Cedar Fictive Trading Inc.") == ["Cedar Fictive Trading Inc."]
    assert names("致:Cedar Fictive Trading Inc.") == ["Cedar Fictive Trading Inc."]
    assert names("抄送：Delta Example Marine Ltd，请知悉") == ["Delta Example Marine Ltd"]
    assert names("经“Eastern Demo Logistics GmbH”承运") == ["Eastern Demo Logistics GmbH"]
    assert names("货物交予（Foo Bar Ltd.）") == ["Foo Bar Ltd."]
    assert names("本合同由Aurora Energy Ltd签订") == ["Aurora Energy Ltd"]
    # 英文标点不使名称断开的情况不变；噪声词、角色词仍不并入名称
    assert names("To: Acme Inc.") == ["Acme Inc."]
    assert names("To:Acme Inc.") == ["Acme Inc."]
    assert names("Dear Acme Inc.") == ["Acme Inc."]
    assert names("Buyer：Acme Ltd") == ["Acme Ltd"]
    assert names("ABC Trading, Inc.") == ["ABC Trading, Inc."]
    # 标点之前的英文词不并入后面的名称
    assert names("Hello World：Acme Inc.") == ["Acme Inc."]
    # 完整流程：外发文本不再含名称的任何一部分
    r, text = run(nodes_of("收函方：Aurora Fictive Energy Co., Ltd.\n致：Cedar Fictive Trading Inc."), stance="other")
    for word in ("Aurora", "Fictive", "Energy", "Cedar", "Trading"):
        assert word not in text


def test_letters():
    assert [letters(i) for i in (0, 1, 25, 26, 27)] == ["A", "B", "Z", "AA", "AB"]


# ---------------------------------------------------------------- 格式化信息与金额


def cats(text: str) -> list[tuple[str, str]]:
    return [(text[h.start:h.end], h.category) for h in detect.find_formatted(text)]


def test_formatted_patterns():
    assert ("13912345678", "PHONE") in cats("手机13912345678")
    assert ("0571-87654321", "PHONE") in cats("座机 0571-87654321。")
    assert ("+86 21 5555 1234", "PHONE") in cats("Tel: +86 21 5555 1234")
    assert ("a.b@example.cn", "EMAIL") in cats("邮箱 a.b@example.cn。")
    assert ("https://www.example.com/x?y=1", "URL") in cats("见 https://www.example.com/x?y=1。")
    assert ("GB82 WEST 1234 5698 7654 32", "ACCOUNT") in cats("IBAN GB82 WEST 1234 5698 7654 32 USD")
    assert ("6222-0000-1111-2222", "ACCOUNT") in cats("Account No.: 6222-0000-1111-2222")
    assert ("ABCDCNBJ001", "SWIFT") in cats("SWIFT Code: ABCDCNBJ001")
    assert ("91310000MA1BCDEF2K", "IDNO") in cats("税号91310000MA1BCDEF2K")
    assert ("11010519491231002X", "IDNO") in cats("身份证 11010519491231002X")
    assert ("PO-2026/017", "REFNO") in cats("P.O. No.: PO-2026/017.")
    # 不该命中：HS 编码、日期、百分比、短数字、没有标签的普通字母数字
    for text in ("HS 编码 27131200", "2026年3月15日", "硫含量≤0.5%", "数量 320 MT", "型号 AB12", "SWIFT 转账"):
        assert cats(text) == [], text


def amounts(text: str, column=None) -> list[tuple[str, str, str]]:
    return [(text[h.start:h.end], h.category, h.key) for h in detect.find_amounts(text, column)]


def test_amount_patterns():
    assert amounts("USD 1,250.00/MT") == [("1,250.00", "PRICE", "PRICE|USD|1250")]
    assert amounts("单价 RMB3,000元/吨")[0][:2] == ("3,000", "PRICE")
    assert amounts("总价 400,000.00 USD") == [("400,000.00", "AMOUNT", "AMOUNT|USD|400000")]
    assert amounts("预付50万元")[0] == ("50", "AMOUNT", "AMOUNT|CNY|500000")
    assert amounts("￥800,000.00")[0][2] == amounts("人民币捌拾万元整")[0][2]
    assert amounts("US$12.5")[0][:2] == ("12.5", "AMOUNT")
    # 条款编号、百分比、数量不是金额
    for text in ("2.3 人民币参考价", "30%", "320 MT", "第2条", "3.1 USD 100"):
        assert all(a[0] not in ("2.3", "30", "320", "2", "3.1") for a in amounts(text)), text
    # 表格列提示：整格只有数字时按列归类
    assert amounts("1,250.00", "PRICE") == [("1,250.00", "PRICE", "PRICE||1250")]
    assert amounts("610,000", "AMOUNT")[0][1] == "AMOUNT"
    assert amounts("610,000") == []


@pytest.mark.parametrize("text,value", [
    ("捌拾万元整", "800000"), ("壹佰元", "100"), ("壹仟零伍元伍角", "1005.5"), ("拾元", "10"),
    ("壹亿贰仟万元整", "120000000"), ("叁万肆仟伍佰陆拾柒元捌角玖分", "34567.89"),
])
def test_chinese_upper_value(text, value):
    assert detect.chinese_upper_value(text) == Decimal(value)


def test_same_amount_same_code_across_nodes():
    r, _ = run(nodes_of("首付 USD 100,000.00", "尾款 100,000.00 美元", "其他 EUR 100,000.00"))
    assert r.redacted["F1-P1"] == "首付 USD [金额_1]"
    assert r.redacted["F1-P2"] == "尾款 [金额_1] 美元"
    assert r.redacted["F1-P3"] == "其他 EUR [金额_2]"  # 币种不同


def test_no_real_value_left_in_outbound_for_regex_categories():
    text = "电话 13800138000 邮箱 x@y.com 账号：1234 5678 9012 网址 www.example.com 合同号：HT-1"
    r, out = run(nodes_of(text))
    for value in ("13800138000", "x@y.com", "1234 5678 9012", "www.example.com", "HT-1"):
        assert value not in out
    assert re.search(r"\d{5,}", out.splitlines()[1]) is None
