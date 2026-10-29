"""代号还原（M2-09、M4-04，SPEC 9.9）。"""
from pathlib import Path

import yaml

from app.parsing.docx import parse_docx
from app.redaction.engine import NodeIn, compute
from app.redaction.restore import restore

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"


def test_long_codes_first_and_boundaries():
    mapping = {"客户_A": "甲公司", "客户_AB": "乙公司", "人员_1": "张三", "人员_12": "李四", "我方": "星河公司"}
    assert restore("客户_AB 与 客户_A、人员_12、人员_1 和我方", mapping) == "乙公司 与 甲公司、李四、张三 和星河公司"
    # 代号后紧跟字母、数字时不是这个代号
    assert restore("客户_AC 人员_13", {"客户_A": "甲", "人员_1": "张"}) == "客户_AC 人员_13"
    assert restore("客户_A的付款", {"客户_A": "甲公司"}) == "甲公司的付款"


def test_bracket_codes_and_fullwidth():
    mapping = {"[金额_1]": "400,000.00", "[金额_12]": "9.00", "编号_1": "HT-1"}
    assert restore("USD [金额_1]（[金额_12]），USD ［金额_1］，编号_1", mapping) == "USD 400,000.00（9.00），USD 400,000.00，HT-1"


def test_all_nine_bracket_variants_and_long_codes():
    assert restore('账号为[账号_1】，金额【金额_2]', {'[账号_1]': 'X', '[金额_2]': 'Y'}) == '账号为X，金额Y'
    for left in '[［【':
        for right in ']］】':
            assert restore(left + '金额_12' + right, {'[金额_1]': 'A', '[金额_12]': 'B'}) == 'B'


def test_inserted_values_not_replaced_again():
    # 真实值里恰好含有另一个代号的文字，不会被二次替换
    mapping = {"客户_A": "客户_B 贸易", "客户_B": "乙"}
    assert restore("客户_A；客户_B", mapping) == "客户_B 贸易；乙"


def test_ignores_recheck_only_keys_and_empty():
    mapping = {"[电话]#1": "13800138000", "客户_A#1": "FMT", "客户_A": "Fictional Metals Trading FZE"}
    assert restore("[电话] 客户_A", mapping) == "[电话] Fictional Metals Trading FZE"
    assert restore("", mapping) == "" and restore("无代号", {}) == "无代号"


def test_round_trip_with_engine():
    manifest = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))["samples"]
    parsed = parse_docx((FIXTURES / manifest["parties_labels"]["file"]).read_bytes(), 1)
    nodes = [NodeIn(n.loc, n.text) for n in parsed.nodes if n.part == "body"]
    r = compute(nodes, "sales")
    plain = {k: v for k, v in r.mapping.items() if "#" not in k}
    # 只出现全称（没有简称）的节点，还原后与原文一致
    for loc in ("F1-P4", "F1-P5", "F1-P6", "F1-P8"):
        original = next(n.text for n in nodes if n.loc == loc)
        assert restore(r.redacted[loc], plain) == original


def test_numbered_accounts_restore_and_address_stays():
    """Q-59：不同账号、SWIFT 得到不同代号且各自还原；地址代号不能唯一还原，保持原样。"""
    nodes = [NodeIn("F1-P1", "账号：6222 0000 1111 2222，SWIFT: ABCDCNBJ"),
             NodeIn("F1-P2", "IBAN: DE89 3704 0044 0532 0130 00，SWIFT: EFGHDEFF"),
             NodeIn("F1-P3", "Address: Unit 5, Example Tower, Hamburg, Germany"),
             NodeIn("F1-P4", "联系人：张示例")]
    r = compute(nodes, "sales")
    plain = {k: v for k, v in r.mapping.items() if "#" not in k}
    reply = "请核对 [账号_1] 与 [账号_2]、[SWIFT_1] 与 ［SWIFT_2］，寄往 [地址：德国]，联系 人员_1。"
    assert restore(reply, plain) == ("请核对 6222 0000 1111 2222 与 DE89 3704 0044 0532 0130 00、ABCDCNBJ 与 EFGHDEFF，"
                                     "寄往 [地址：德国]，联系 张示例。")
    for loc in ("F1-P1", "F1-P2", "F1-P4"):
        assert restore(r.redacted[loc], plain) == next(n.text for n in nodes if n.loc == loc)


def test_codes_in_typical_model_output():
    """模型回复里常见的写法：加粗、引号、英文所有格、紧挨标点和英文单词、表格与列表。"""
    mapping = {"客户_A": "Fictional Metals Trading FZE", "人员_1": "张三", "[金额_1]": "400,000.00"}
    text = ("**客户_A** 应在“人员_1”签字后付款；客户_A's obligation, see 客户_A.\n"
            "| 付款方 | 客户_A |\n- 金额：USD【金额_1】(含税)\n`人员_1`")
    assert restore(text, mapping) == (
        "**Fictional Metals Trading FZE** 应在“张三”签字后付款；Fictional Metals Trading FZE's obligation, "
        "see Fictional Metals Trading FZE.\n| 付款方 | Fictional Metals Trading FZE |\n"
        "- 金额：USD400,000.00(含税)\n`张三`")
