"""脱敏核心（M2-08、M4-04）：9.3 第 1–7 步、数量、疑似遗漏、金额比例、用户词条、代号分配、对照表、外发文本。不需要数据库。"""
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.parsing.docx import parse_docx
from app.redaction import detect, outbound
from app.redaction.engine import NodeIn, PartyOverride, Term, UserTerm, compute, outbound_text
from app.redaction.parties import find_labeled_parties, find_other_org_names, normalize
from app.redaction.placeholders import letters

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"
MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))["samples"]


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
        assert item["text"] not in text, item
    # 地址整段换成 [地址：国家]：地址中的英文国家名以代号中的中文国家名保留（M4-04）
    addresses = [i["text"] for i in sample.get("must_replace", []) if i["category"] == "ADDR"]
    for keep in sample.get("must_keep", []) + sample.get("must_keep_words", []):
        if any(keep in a for a in addresses):
            continue
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
    # 账号、SWIFT 带序号（Q-59）：IBAN 与本地账号写法不同，视为不同账号
    assert red["F1-P19"] == "开户行账号：[账号_1]" and red["F1-P21"] == "IBAN: [账号_2]" and red["F1-P20"] == "SWIFT: [SWIFT_1]"
    assert red["F1-P10"] == "Address: [地址：阿联酋]" and red["F1-P11"] == "Contact: 人员_2, [电话]"
    assert red["F1-P6"] == "联系人：人员_1" and red["F1-P26"] == "法定代表人（签字）：人员_3"
    # 单价与金额分开编号，币种保留；同一数值同一代号（大写与小写）
    assert red["F1-P15"] == "2.1 单价为 USD [单价_1]/MT CIF Jebel Ali Port，合同总价 Total Amount: USD [金额_1]。"
    assert red["F1-P17"] == "2.3 人民币参考价：人民币[金额_3]（¥[金额_3]）。"
    # 金额比例（9.6）：合同总价本身不加，同币种的其他金额加，不同币种的不加
    assert red["F1-P16"].startswith("2.2 预付款为合同总价的 30%，即 USD [金额_2]（=合同总价的 30.0%），")
    assert text.splitlines()[0] == "【主体说明】我方=卖方；客户_A：买方，阿联酋，贸易商"
    assert {(p.code, p.role_label, p.country, p.is_our_party) for p in r.parties} == {
        ("我方", "卖方", "中国", True), ("客户_A", "买方", "阿联酋", False)}
    # 对照表：唯一代号 → 主要写法；其他写法和共用代号的值以“代号#n”保存
    assert r.mapping["我方"] == "星河虚构进出口有限公司"
    assert r.mapping["客户_A"] == "Fictional Metals Trading FZE"
    assert r.mapping["客户_A#1"] == "FMT" and r.mapping["我方#1"] == "星河公司"
    assert r.mapping["[金额_3]"] == "捌拾万元整" and r.mapping["[金额_3]#1"] == "800,000.00"
    assert r.mapping["[账号_1]"] == "6222 0000 1111 2222 333" and r.mapping["[SWIFT_1]"] == "FICTAEAD"
    assert r.mapping["人员_3"] == "吴虚构"
    assert "[地址：阿联酋]" not in r.mapping  # 地址不能唯一还原，真实值只用于出口复检
    assert r.mapping["[地址：阿联酋]#1"] == "Plot 12, Jebel Ali Free Zone, Dubai, United Arab Emirates"
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
    # 没有合同总价标签时，同币种的最大金额（合计）为合同总价，其余金额附比例
    assert red["F1-T1-R2-C5"] == "USD [金额_1]（=合同总价的 65.6%）" and red["F1-T1-R4-C5"] == "USD [金额_3]"
    assert red["F1-T1-R2-C3"] == "320"  # 数量默认保留
    # 买方信息行之后是报价条款，不能把其中的港口当成买方国家
    buyer = next(p for p in r.parties if p.role_label == "买方")
    assert buyer.country == "国家未知"


def test_table_hints_header_row_below_title_rows():
    """Q-65：表头行取前 20 行中第一个含单价或金额表头的行；之上的行没有提示（DOCX / PDF 表格）。"""
    nodes = [
        NodeIn("F1-PG1-T1-R1-C1", "报价明细"),
        NodeIn("F1-PG1-T1-R1-C3", "999"),  # 表头之上：不按列处理
        NodeIn("F1-PG1-T1-R2-C1", "品名"),
        NodeIn("F1-PG1-T1-R2-C2", "单价"),
        NodeIn("F1-PG1-T1-R2-C3", "总价"),
        NodeIn("F1-PG1-T1-R3-C1", "煅后石油焦"),
        NodeIn("F1-PG1-T1-R3-C2", "1250"),
        NodeIn("F1-PG1-T1-R3-C3", "400000"),
        NodeIn("F1-PG1-T1-R4-C3", "Total"),  # 第二个“表头样”的行不再作为表头
        NodeIn("F1-PG1-T1-R5-C3", "610000"),
        NodeIn("F1-PG1-T2-R2-C1", "800"),  # 另一个表格没有表头：没有提示
    ]
    r, _ = run(nodes)
    red = r.redacted
    assert red["F1-PG1-T1-R3-C2"] == "[单价_1]" and red["F1-PG1-T1-R3-C3"] == "[金额_1]"
    assert red["F1-PG1-T1-R5-C3"] == "[金额_2]"
    assert red["F1-PG1-T1-R1-C3"] == "999" and red["F1-PG1-T2-R2-C1"] == "800"
    assert red["F1-PG1-T1-R4-C3"] == "Total"


def test_table_hints_xlsx_cells():
    """XLSX 单元格 F{n}-S{表}-{列}{行}：整个工作表为一个表格，不同工作表、不同文件互不影响。"""
    nodes = [
        NodeIn("F1-S1-A1", "报价单 Quotation"),
        NodeIn("F1-S1-A3", "卖方：星河虚构进出口有限公司"),
        NodeIn("F1-S1-C7", "数量(MT)"),
        NodeIn("F1-S1-D7", "Unit Price"),
        NodeIn("F1-S1-AB7", "Amount (USD)"),
        NodeIn("F1-S1-C8", "320"),
        NodeIn("F1-S1-D8", "1250"),
        NodeIn("F1-S1-AB8", "400000"),
        NodeIn("F1-S1-D12", "USD 2100"),
        NodeIn("F1-S2-D8", "1250"),  # 第 2 个工作表没有表头
        NodeIn("F2-S1-D8", "1250"),  # 另一个文件
    ]
    r, _ = run(nodes)
    red = r.redacted
    assert (red["F1-S1-D8"], red["F1-S1-AB8"], red["F1-S1-D12"]) == ("[单价_1]", "[金额_1]", "USD [单价_2]")
    assert red["F1-S1-C8"] == "320" and red["F1-S2-D8"] == "1250" and red["F2-S1-D8"] == "1250"


def test_table_hints_header_only_in_first_20_rows():
    nodes = [NodeIn("F1-S1-B21", "单价"), NodeIn("F1-S1-B22", "1250"),
             NodeIn("F1-S2-B20", "单价"), NodeIn("F1-S2-B22", "1250")]
    red = run(nodes)[0].redacted
    assert red["F1-S1-B22"] == "1250" and red["F1-S2-B22"] == "[单价_1]"


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
    # 向前取汉字遇到“由、与”等边界词即停止（Q-56），“本合同由”“与”保留
    assert r.redacted["F1-P1"] == "本合同由公司_A与公司_B签订，货物经 公司_C 承运。"
    assert {p.org_type for p in r.parties} == {"类型未知", "货代"}
    assert find_other_org_names("由星河虚构进出口有限公司提供") == [(1, 12)]
    # 宽泛后缀（公司、厂）在正文中不自动替换
    assert run(nodes_of("本公司与贵公司、该厂均同意。"))[0].items == []


def test_chinese_org_name_start_boundary():
    # Q-56（M4-01）：名称从最后一个边界词之后开始，边界词和前面的动词、介词保留
    def names(text):
        return [text[s:e] for s, e in find_other_org_names(text)]

    assert names("货物由恒远虚构物流（深圳）有限公司代为装运。") == ["恒远虚构物流（深圳）有限公司"]
    assert names("委托恒远虚构物流有限公司办理报关") == ["恒远虚构物流有限公司"]
    assert names("买方向星河虚构贸易有限公司支付货款") == ["星河虚构贸易有限公司"]
    assert names("通过上海示例国际货运代理有限公司订舱") == ["上海示例国际货运代理有限公司"]
    assert names("经由宏达虚构船务有限公司承运") == ["宏达虚构船务有限公司"]  # 多字边界词
    assert names("根据北方示例化工有限公司的要求") == ["北方示例化工有限公司"]
    assert names("代表南方示例集团有限公司签署") == ["南方示例集团有限公司"]
    assert names("卖方与买方及东方示例实业有限公司同意") == ["东方示例实业有限公司"]
    # 括号里的字不算边界；受保护词（经贸、自由贸易等）内部的字不算边界，整个名称一起替换
    assert names("委托华兴（对外）示例有限公司办理") == ["华兴（对外）示例有限公司"]
    assert names("货物由新兴经济贸易有限公司提供") == ["新兴经济贸易有限公司"]
    assert names("与自由贸易示例有限公司签订") == ["自由贸易示例有限公司"]
    # 边界词之后不足 2 个汉字时按 M2 的做法整段处理（宁多勿漏）
    assert names("本合同由某有限公司提供") == ["本合同由某有限公司"]
    # 连续两个名称：第二个名称的起点边界不越过第一个名称
    assert names("由甲示例有限公司向乙示例有限公司交付") == ["甲示例有限公司", "乙示例有限公司"]
    # 完整流程：只替换公司名
    r, text = run(nodes_of("货物由恒远虚构物流（深圳）有限公司代为装运，并委托星河虚构贸易有限公司办理。"), stance="other")
    assert r.redacted["F1-P1"] == "货物由公司_A代为装运，并委托公司_B办理。"


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


def test_english_org_name_with_parenthesized_region():
    # M2 验收评审：正文中带括号地区名的英文名称（不在签约方标签后）曾整个漏替换
    def names(text):
        return [text[s:e] for s, e in find_other_org_names(text)]

    assert names("货物由 Northwind Fictive (Hong Kong) Limited 代为装运。") == ["Northwind Fictive (Hong Kong) Limited"]
    assert names("抄送：Bluefin Example Shipping (HK) Co., Ltd.") == ["Bluefin Example Shipping (HK) Co., Ltd."]
    assert names("Example Steel (Shanghai) Co., Ltd.") == ["Example Steel (Shanghai) Co., Ltd."]
    # 括号组后紧跟连接词、括号组在开头、括号内是小写说明：括号不并入名称
    assert names("according to the Contract (Annex A) and Sample Trading LLC agreed.") == ["Sample Trading LLC"]
    assert names("付款至 (HK) Co., Ltd. 账户") == []
    assert names('Fictional Steel Works Co., Ltd. (the "Seller")') == ["Fictional Steel Works Co., Ltd."]
    r, text = run(nodes_of("货物由 Northwind Fictive (Hong Kong) Limited 代为装运。"), stance="other")
    for word in ("Northwind", "Fictive"):
        assert word not in text
    assert "货物由" in text and "代为装运" in text


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


def test_fill_brackets_patterns():
    """填空括号【】［］[]〖〗：标签与值、币种与数字之间各允许一个（B-01、Q-134 ①）。"""
    assert amounts("人民币【222063】元") == [("222063", "AMOUNT", "AMOUNT|CNY|222063")] * 2  # 前置、后置各命中一次
    assert amounts("￥【 250000 】")[0][:2] == ("250000", "AMOUNT")
    assert amounts("服务款［62500］元")[0][:2] == ("62500", "AMOUNT")
    assert amounts("USD〖1,250.00〗/MT")[0][:2] == ("1,250.00", "PRICE")
    assert amounts("人民币【22.5】万元")[0][2] == "AMOUNT|CNY|225000"
    upper = amounts("大写：人民币【贰拾贰万贰仟零陆拾叁】元整")
    assert upper == [("贰拾贰万贰仟零陆拾叁", "AMOUNT", "AMOUNT|CNY|222063")]
    assert amounts("人民币【陆万陆仟陆佰壹拾捌元玖角】整")[0][0] == "陆万陆仟陆佰壹拾捌元玖角"
    # 代号的序号不是金额（出口复检的重扫用同一函数）
    assert amounts("即人民币[金额_10]元，大写[金额_3]元整") == []
    # 括号里的份数、期数、比例、天数不是金额
    for text in ("一式【4】份", "第【2】期", "合同总价的【30】％", "第【3】日", "【2026】年【3】月"):
        assert amounts(text) == [], text
    assert ("3700 1842 5010 5000 7788", "ACCOUNT") in cats("账号：【 3700 1842 5010 5000 7788 】")
    assert ("HX26-JK077", "REFNO") in cats("合同编号：【  HX26-JK077 】")
    assert ("ABCDCNBJ001", "SWIFT") in cats("SWIFT：［ABCDCNBJ001］")
    persons = [text[h.start:h.end] for text in ("法定代表人：【郑虚构】", "授权代表：【 柳示例 】")
               for h in detect.find_persons(text)]
    assert persons == ["郑虚构", "柳示例"]
    text = "法定地址：【山东省示例县城示例路 18 号】"
    assert [text[h.start:h.end] for h in detect.find_addresses(text)] == ["山东省示例县城示例路 18 号"]


def test_postcode_patterns():
    """R1-11（B-18、Q-139 ①）：只认标签后的邮编；可跨一个填空括号；不按“6 位数字”猜测。"""
    assert cats("邮政编码：【251500】") == [("251500", "POSTCODE")]
    assert cats("邮编：200000") == [("200000", "POSTCODE")]
    assert cats("Post Code: 90210") == [("90210", "POSTCODE")]
    assert cats("Postal Code:K1A 0B1.") == [("K1A 0B1", "POSTCODE")]
    assert cats("邮编：100000-1234") == [("100000-1234", "POSTCODE")]
    assert cats("Zip Code: SW1A 1AA") == [("SW1A 1AA", "POSTCODE")]
    assert cats("ZIP 〖 10001 〗") == [("10001", "POSTCODE")]
    assert cats("邮编【251500】") == [("251500", "POSTCODE")]
    # 末尾不含数字的词不属于邮编；邮编后的电话照常识别
    assert cats("ZIP: 10001 New York") == [("10001", "POSTCODE")]
    assert cats("邮编：251500 电话：0531-6612345") == [("251500", "POSTCODE"), ("0531-6612345", "PHONE")]
    # 不识别：无值、标签后不是邮编、没有标签、超过 10 位、不含数字、单词内部的 zip
    for text in ("邮编为空", "邮编：", "邮政编码：【    】", "正文 251500 无标签", "邮编：2515001234567",
                 "Zip Code: ABCDE", "unzip: 12345", "邮编为 251500"):
        assert cats(text) == [], text


def test_postcode_fixed_code_and_mapping():
    """[邮编] 为共用代号（与电话相同）：真实值写入对照表“[邮编]#n”；同一行的地址在邮编标签前结束。"""
    nodes = nodes_of("通讯地址：山东省示例县示例路 18 号 邮政编码：【251500】",
                     "收货地址：上海市示例区虚构路 99 号，邮编：200000",
                     "Post Code: SW1A 1AA")
    r, out = run(nodes, "purchase")
    assert r.redacted["F1-P1"] == "通讯地址：[地址：中国] 邮政编码：【[邮编]】"
    assert r.redacted["F1-P2"] == "收货地址：[地址：中国]，邮编：[邮编]"
    assert r.redacted["F1-P3"] == "Post Code: [邮编]"
    assert "[邮编]" not in r.mapping  # 不能唯一还原
    assert sorted(v for k, v in r.mapping.items() if k.startswith("[邮编]#")) == ["200000", "251500", "SW1A 1AA"]
    assert {i.category for i in r.items if i.placeholder == "[邮编]"} == {"POSTCODE"}
    for value in ("251500", "200000", "SW1A 1AA"):
        assert value not in out


def test_fill_brackets_party_name():
    nodes = [NodeIn("F1-P1", "甲方：【青岚虚构石化股份有限公司】"), NodeIn("F1-P2", "乙方： 〖 远川虚构通信有限公司〗")]
    result = compute(nodes, "purchase")
    assert {result.mapping[p.code] for p in result.parties} == {"青岚虚构石化股份有限公司", "远川虚构通信有限公司"}
    assert result.redacted["F1-P1"] == "甲方：【公司_A】"


def test_org_name_extends_to_last_suffix():
    # R1-04（B-07、Q-134 ⑤）：名称延伸到同一名称内的最后一个后缀，延伸时认分公司、支行等
    def labeled(text):
        return [text[s:e] for _, s, e in find_labeled_parties(text)]

    def names(text):
        return [text[s:e] for s, e in find_other_org_names(text)]

    assert labeled("乙方：【恒岳虚构集团山东有限公司德州分公司】") == ["恒岳虚构集团山东有限公司德州分公司"]
    assert labeled("开户行：远川虚构商业银行股份有限公司示例路支行") == ["远川虚构商业银行股份有限公司示例路支行"]
    assert labeled("开户行：虚构银行股份有限公司和平路支行营业部") == ["虚构银行股份有限公司和平路支行营业部"]
    assert labeled("乙方：华兴（深圳）示例有限公司（深圳）分公司") == ["华兴（深圳）示例有限公司（深圳）分公司"]
    assert labeled("乙方：示例集团北方研究院第三分厂") == ["示例集团北方研究院第三分厂"]
    # 简称结构不并入名称
    assert labeled("乙方：远川虚构有限公司（以下简称“远川分公司”）") == ["远川虚构有限公司"]
    assert labeled("乙方：远川虚构有限公司(hereinafter \"Branch\")") == ["远川虚构有限公司"]
    assert labeled("乙方：远川虚构有限公司（下称“乙方”）") == ["远川虚构有限公司"]
    # 不跨越分隔符、空白、填空括号、边界词
    for text in ("甲方：A示例有限公司与B示例有限公司", "甲方：A示例有限公司，B示例有限公司", "甲方：A示例有限公司 B示例分公司",
                 "甲方：【A示例有限公司】【B示例分公司】", "甲方：A示例有限公司、B示例有限公司"):
        assert labeled(text) == ["A示例有限公司"], text
    # 最长 60 字：超出的后缀不延伸
    long_gap = "甲" * 60
    assert labeled(f"乙方：示例有限公司{long_gap}分公司") == ["示例有限公司"]
    # 正文：“与”隔开的第二个名称单独识别；从属、泛指词后不延伸；正文不据宽泛后缀“公司、集团”延伸
    assert names("由甲示例有限公司与乙示例集团山东有限公司德州分公司签订") == ["甲示例有限公司", "乙示例集团山东有限公司德州分公司"]
    assert names("款项汇至远川虚构商业银行股份有限公司示例路支行。") == ["款项汇至远川虚构商业银行股份有限公司示例路支行"]
    assert names("星河虚构有限公司下属分公司") == ["星河虚构有限公司"]
    assert names("星河虚构有限公司负责成立分公司") == ["星河虚构有限公司"]
    assert names("星河虚构有限公司委托示例公司") == ["星河虚构有限公司"]
    assert names("星河虚构有限公司（以下简称“星河分公司”）") == ["星河虚构有限公司"]
    # 完整流程：签约方与正文同一代号，名称后半不留在正文
    r, text = run(nodes_of("乙方：【恒岳虚构集团山东有限公司德州分公司】",
                           "本合同由甲方与恒岳虚构集团山东有限公司德州分公司签订。"), stance="purchase")
    assert r.redacted["F1-P1"] == "乙方：【公司_A】"
    assert r.redacted["F1-P2"] == "本合同由甲方与公司_A签订。"
    assert "分公司" not in text


def test_bank_label_value_without_company_suffix():
    """R1-12（B-20、Q-139 ②）：开户行标签后以银行类结尾、没有企业后缀的中文值作为银行主体。"""
    def labeled(text):
        return [(label, text[s:e]) for label, s, e in find_labeled_parties(text)]

    assert labeled("开户银行：示例行虚构园区支行") == [("银行", "示例行虚构园区支行")]
    assert labeled("开户行：【虚构银行】") == [("银行", "虚构银行")]
    assert labeled("收款银行：虚构农村商业银行城东分理处") == [("银行", "虚构农村商业银行城东分理处")]
    assert labeled("Beneficiary Bank: 示例银行（虚构）营业部") == [("银行", "示例银行（虚构）营业部")]
    assert labeled("开户行名称：示例行虚构园区支行储蓄所") == [("银行", "示例行虚构园区支行储蓄所")]
    # 不跨越分隔符、空白、简称结构；取到最后一个银行类结尾
    assert labeled("开户行：示例行虚构园区支行，账号：123") == [("银行", "示例行虚构园区支行")]
    assert labeled("开户行：示例行虚构园区支行账号见下") == [("银行", "示例行虚构园区支行")]
    assert labeled("开户行：示例行虚构支行（以下简称“虚构支行”）") == [("银行", "示例行虚构支行")]
    # 不识别：无值、不是银行、不足 4 个汉字、英文值、单独的 Bank 标签、其他标签、空白隔开、超过 60 字
    for text in ("开户行：见附件", "开户行：", "开户行：【    】", "开户行：某银行", "Bank Name: Example Bank Branch",
                 "Bank: 示例银行虚构支行", "收款人：示例行虚构园区支行", "开户行：示例行 虚构园区支行",
                 "开户行：" + "示" * 60 + "支行"):
        assert labeled(text) == [], text
    # 已有的“取到企业后缀并延伸”的结果优先
    assert labeled("开户行：远川虚构商业银行股份有限公司示例路支行") == [("银行", "远川虚构商业银行股份有限公司示例路支行")]
    # 完整流程：全文替换为银行代号，类型为银行，国家为中国
    r, text = run(nodes_of("乙方：远川虚构通信有限公司", "开户银行：示例行虚构园区支行",
                           "货款汇入示例行虚构园区支行，以银行到账日为准。"), stance="purchase")
    bank = next(p for p in r.parties if p.role_label == "银行")
    assert bank.code.startswith("银行_") and bank.org_type == "银行" and bank.country == "中国"
    assert r.redacted["F1-P2"] == f"开户银行：{bank.code}"
    assert r.redacted["F1-P3"] == f"货款汇入{bank.code}，以银行到账日为准。"
    assert "虚构园区" not in text


def test_broad_suffix_name_with_alias():
    """R1-13（B-21、Q-139 ③）：宽泛后缀名称紧跟简称结构时作为主体，简称一并替换；不带简称的仍只是疑似遗漏。"""
    def one(text):
        r = compute([NodeIn("F1-P1", text)], "other")
        return r.redacted["F1-P1"], [p.code for p in r.parties], [text[s.char_start:s.char_end] for s in r.suspects]

    red, codes, sus = one("由示例通信集团公司（以下简称“示例通信”）负责，示例通信应按期交付。")
    assert (red, codes, sus) == ("由公司_A（以下简称“公司_A”）负责，公司_A应按期交付。", ["公司_A"], [])
    # 名称起点与疑似遗漏相同：“的”、边界词处截断
    assert one("我单位对于所申请的示例通信集团公司（以下简称“示例通信”）的入网资质")[0] == \
        "我单位对于所申请的公司_A（以下简称“公司_A”）的入网资质"
    assert one("配套设备由远山虚构机械厂（下称“远山厂”）生产，远山厂负责。")[0] == "配套设备由公司_A（下称“公司_A”）生产，公司_A负责。"
    assert one("示例虚构研究所 （以下简称\"示例所\"）提供数据。")[0] == "公司_A （以下简称\"公司_A\"）提供数据。"
    # 泛称、泛指词：不登记
    for text in ("本公司（以下简称“我方”）保证。", "由我公司（以下简称“乙方”）负责。", "该研究院（以下简称“研究院”）"):
        assert one(text)[:2] == (text, []), text
    # 简称为通用角色词：名称照常登记，简称不替换
    assert one("示例通信集团公司（以下简称“乙方”）负责，乙方应按期交付。")[0] == "公司_A（以下简称“乙方”）负责，乙方应按期交付。"
    # 不带简称、简称结构前有其他文字：仍只是疑似遗漏（Q-137 ①）
    assert one("示例科技公司负责安装。")[1:] == ([], ["示例科技公司"])
    assert one("示例科技公司负责安装（以下简称“安装”）")[1] == []
    # 明确后缀的名称不受影响
    assert one("星河虚构有限公司（以下简称“星河”）负责")[0] == "公司_A（以下简称“公司_A”）负责"


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


# ---------------------------------------------------------------- M4-04：engine 整合


def item_at(r, loc, text, needle):
    start = text.index(needle)
    return next(i for i in r.items if i.loc == loc and i.char_start <= start and i.char_end >= start + len(needle))


def test_user_terms_three_categories_full_text():
    nodes = nodes_of("本项目由 Bluefin Example 负责，项目代号 ALPHA-7。", "联络人 王示例 与 BLUEFIN  EXAMPLE 确认 alpha-7。")
    r, text = run(nodes, stance="other", user_terms=[
        UserTerm("Bluefin Example", "ORG"), UserTerm("王示例", "PERSON"), UserTerm("ALPHA-7", "CUSTOM")])
    assert r.redacted["F1-P1"] == "本项目由 公司_A 负责，项目代号 [敏感词_1]。"
    assert r.redacted["F1-P2"] == "联络人 人员_1 与 公司_A 确认 [敏感词_1]。"  # 英文不区分大小写、忽略多余空格
    assert {i.detected_by for i in r.items} == {"user"}
    # 公司名登记为一方（角色“其他”），出现在签约方列表中，可以设为我方（Q-54）
    assert [(p.code, p.role_label) for p in r.parties] == [("公司_A", "其他")]
    assert r.mapping["公司_A"] == "Bluefin Example" and r.mapping["公司_A#1"] == "BLUEFIN  EXAMPLE"
    r2, text2 = run(nodes, stance="other", user_terms=[UserTerm("Bluefin Example", "ORG")],
                    overrides={normalize("Bluefin Example"): PartyOverride(True, None, None)})
    assert r2.redacted["F1-P1"].startswith("本项目由 我方 负责") and text2.splitlines()[0] == "【主体说明】我方"
    # 用户选中的数字串在字母后面也能替换（OCR 文字中常见）
    r3, _ = run(nodes_of("A/C6217000012345678 请核对"), user_terms=[UserTerm("6217000012345678", "CUSTOM")])
    assert r3.redacted["F1-P1"] == "A/C[敏感词_1] 请核对"


def test_no_half_name_left():
    full = "Aurora Fictive Energy Co., Ltd."
    nodes = nodes_of(f"收函方：{full}", "抄送：Aurora Fictive 财务部", f"再次确认 {full} 的地址")
    # 用户选中名称的前半；全称也在文中：前半作为全称的简称，同一代号，不出现“公司_C Fictive Energy”这类结果
    r, text = run(nodes, stance="other", user_terms=[UserTerm("Aurora Fictive", "ORG")])
    assert r.redacted["F1-P1"] == "收函方：公司_A"
    assert r.redacted["F1-P2"] == "抄送：公司_A 财务部"
    assert r.redacted["F1-P3"] == "再次确认 公司_A 的地址"
    assert len(r.parties) == 1 and item_at(r, "F1-P2", nodes[1].text, "Aurora Fictive").detected_by == "user"
    for word in ("Aurora", "Fictive", "Energy"):
        assert word not in text
    # 已记住的名称是名称的一部分、全称未带标签出现在正文中：同样整体替换
    nodes = nodes_of(f"货物由 {full} 发运。", "Aurora Fictive 负责清关。")
    r, text = run(nodes, stance="other", terms=[Term("Aurora Fictive", "learned", "ORG")])
    assert r.redacted["F1-P1"] == "货物由 公司_A 发运。" and r.redacted["F1-P2"] == "公司_A 负责清关。"
    assert r.matched_terms == {normalize("Aurora Fictive")}


def test_persons_collected_then_full_text():
    nodes = nodes_of("联系人：张示例", "Attn: Mr. Kevin Lee", "张示例将于下周到访，Kevin Lee 负责接待；Leeds 和 lee 不是人名。",
                     "Contact: Fictive Trading LLC")
    r, text = run(nodes, stance="other")
    assert r.redacted["F1-P1"] == "联系人：人员_1"
    assert r.redacted["F1-P2"] == "Attn: Mr. 人员_2"
    assert r.redacted["F1-P3"] == "人员_1将于下周到访，人员_2 负责接待；Leeds 和 lee 不是人名。"
    assert r.redacted["F1-P4"] == "Contact: 公司_A"  # 标签后是公司名称：已按第 2 步替换，不再当人名
    assert {i.detected_by for i in r.items if i.category == "PERSON"} == {"person"}
    assert r.mapping["人员_2"] == "Kevin Lee"


def test_addresses_segments_country_and_repeat():
    nodes = nodes_of(
        "卖方：星河虚构进出口有限公司",
        "地址：上海市浦东新区示例路 88 号星河虚构进出口有限公司大楼，电话：021-12345678",
        "Address: Dubai",
        "Registered Address: Unit 5, Example Tower, Hamburg, Germany",
        "Documents to be sent to Unit 5, Example Tower, Hamburg, Germany by courier.",
    )
    r, text = run(nodes)
    # 与公司名重叠的部分已替换，剩下的各段用同一代号
    assert r.redacted["F1-P2"] == "地址：[地址：中国]我方[地址：中国]，电话：[电话]"
    assert r.redacted["F1-P3"] == "Address: Dubai"  # 只有城市名，保留（9.2 保留国家和港口）
    assert r.redacted["F1-P4"] == "Registered Address: [地址：德国]"
    assert r.redacted["F1-P5"] == "Documents to be sent to [地址：德国] by courier."  # 同一地址在别处也替换
    assert {i.detected_by for i in r.items if i.category == "ADDR"} == {"address"}
    assert "[地址：德国]" not in r.mapping
    assert {r.mapping[k] for k in r.mapping if k.startswith("[地址：德国]#")} == {"Unit 5, Example Tower, Hamburg, Germany"}


def test_accounts_and_swift_numbered():
    nodes = nodes_of("账号：6222 0000 1111 2222", "Account No.: 6222-0000-1111-2222", "账号：6217 0000 1234 5678",
                     "IBAN: DE89 3704 0044 0532 0130 00", "SWIFT: ABCDDEFF", "BIC: ABCDDEFFXXX", "SWIFT: ABCDDEFF001")
    r, _ = run(nodes)
    red = [r.redacted[n.loc] for n in nodes]
    assert red == ["账号：[账号_1]", "Account No.: [账号_1]", "账号：[账号_2]", "IBAN: [账号_3]",
                   "SWIFT: [SWIFT_1]", "BIC: [SWIFT_1]", "SWIFT: [SWIFT_2]"]
    assert r.mapping["[账号_1]"] == "6222 0000 1111 2222" and r.mapping["[账号_1]#1"] == "6222-0000-1111-2222"
    assert r.mapping["[SWIFT_1]"] == "ABCDDEFF" and r.mapping["[SWIFT_2]"] == "ABCDDEFF001"


def test_quantity_masking_on_and_off():
    nodes = [NodeIn("F1-P1", "数量 500 MT，另加 500 MT 备用，3 个工作日内发货"),
             NodeIn("F1-T1-R1-C1", "品名"), NodeIn("F1-T1-R1-C2", "数量"), NodeIn("F1-T1-R1-C3", "单价"),
             NodeIn("F1-T1-R2-C1", "针状焦"), NodeIn("F1-T1-R2-C2", "320"), NodeIn("F1-T1-R2-C3", "1250")]
    off, _ = run(nodes)
    assert off.redacted["F1-P1"] == nodes[0].text and off.redacted["F1-T1-R2-C2"] == "320"
    on, _ = run(nodes, mask_quantity=True)
    assert on.redacted["F1-P1"] == "数量 [数量_1] MT，另加 [数量_1] MT 备用，3 个工作日内发货"
    assert on.redacted["F1-T1-R2-C2"] == "[数量_2]" and on.redacted["F1-T1-R2-C3"] == "[单价_1]"
    assert {i.detected_by for i in on.items if i.category == "QTY"} == {"amount"}


def test_suspects_after_all_replacements():
    nodes = nodes_of("卖方：星河虚构进出口有限公司", "货物由 Northwind Fictive Holdings 承运，收款 6222 0000 1111 2222 333。",
                     "HS 编码 27131200，2026-03-15 装运，Force Majeure 条款适用，星河虚构进出口有限公司确认。")
    r, _ = run(nodes)
    by_loc = {n.loc: n.text for n in nodes}
    found = {(s.loc, by_loc[s.loc][s.char_start:s.char_end], s.reason) for s in r.suspects}
    assert found == {("F1-P2", "Northwind Fictive Holdings", "capitalized"),
                     ("F1-P2", "6222 0000 1111 2222 333", "long_digits")}


def test_ratio_in_redacted_only():
    nodes = [NodeIn("F2-P1", "附件总价 USD 1,000.00"), NodeIn("F1-P1", "合同总价：USD 200,000.00，预付 USD 60,000.00"),
             NodeIn("F1-P2", "尾款 140,000.00 美元，另付 EUR 5,000.00")]
    r, _ = run(nodes, main_prefix="F1-")
    assert r.redacted["F1-P1"] == "合同总价：USD [金额_2]，预付 USD [金额_3]（=合同总价的 30.0%）"
    assert r.redacted["F1-P2"] == "尾款 [金额_4] 美元（=合同总价的 70.0%），另付 EUR [金额_5]"
    assert r.redacted["F2-P1"] == "附件总价 USD [金额_1]（=合同总价的 0.5%）"  # 先在主文件中找合同总价
    assert all("合同总价的" not in v for v in r.mapping.values())
    assert all(i.placeholder.startswith("[金额_") for i in r.items)
    # 多个币种、没有合同总价标签：不加比例（Q-72）
    r, _ = run(nodes_of("USD 100.00 与 EUR 50.00"))
    assert "合同总价" not in r.redacted["F1-P1"]


def test_matched_terms_only_hits():
    terms = [Term("Secret Project Phoenix", "custom", "CUSTOM"), Term("未出现的名称有限公司", "learned", "ORG")]
    r, _ = run(nodes_of("本项目即 secret project phoenix。"), terms=terms)
    assert r.matched_terms == {normalize("Secret Project Phoenix")}
    assert run(nodes_of("无关内容"), terms=terms)[0].matched_terms == set()


def test_outbound_text_with_quote_table():
    nodes = [NodeIn("F1-P1", "报价单"),
             NodeIn("F1-T1-R1-C1", "品名"), NodeIn("F1-T1-R1-C2", "单价 Unit Price"), NodeIn("F1-T1-R1-C3", "金额")]
    rows = [("针状焦", "USD 1,250.00", "USD 400,000.00"), ("石油焦", "USD 900.00", "USD 90,000.00"),
            ("针状焦", "USD 1,100.00", "USD 110,000.00")]
    for row, (name, price, amount) in enumerate(rows, start=2):
        nodes += [NodeIn(f"F1-T1-R{row}-C1", name), NodeIn(f"F1-T1-R{row}-C2", price), NodeIn(f"F1-T1-R{row}-C3", amount)]
    nodes += [NodeIn("F1-T1-R5-C1", "合计"), NodeIn("F1-T1-R5-C3", "USD 600,000.00"), NodeIn("F1-P2", "以上价格含税")]
    r = compute(nodes, "sales")
    parties = [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in r.parties]
    text = outbound_text(nodes, parties, r.redacted)
    assert text.splitlines() == [
        "【主体说明】无", "[F1-P1] 报价单", "[F1-T1] 【报价明细表：共 3 行，币种 USD，产品：针状焦等 2 种】",
        "[F1-T1-R5-C1] 合计", "[F1-T1-R5-C3] USD [金额_4]", "[F1-P2] 以上价格含税"]
