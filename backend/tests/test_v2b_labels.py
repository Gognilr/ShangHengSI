"""V2-B-01：设计 1.5 逐条测试；迁移按已确认 V2Q-31 顺延。"""
import pytest

from app.chat.service import _redact
from app.redaction import detect
from app.redaction.engine import NodeIn, compute
from app.redaction.recheck import recheck
from tests.test_redaction_golden import load


def result(text, loc="F1-P1"):
    return compute([NodeIn(loc, text)], "sales")


def test_01_all_thirteen_labels():
    nodes, ann, r, text, sent = load("labels_extended")
    expected = dict(zip(
        ["COSU1234567890", "LC26FICT0091", "QL-2026-ZB-017", "ZB2026FICT0042", "QT-2026-0508",
         "E12345678", "110105012345678", "MA01XYQ1-5", "102100099996", "zhang_fiction88",
         "88001234", "fiction.trader.01", "江北市虚构医院信息化改造项目"],
        ["REFNO"] * 5 + ["IDNO"] * 3 + ["SWIFT"] + ["IM"] * 3 + ["PROJECT"]))
    for value, category in expected.items():
        node = next(n for n in nodes if value in n.text)
        assert any(i.category == category and node.text[i.char_start:i.char_end] == value
                   for i in r.items if i.loc == node.loc)
        assert value not in text and value not in sent


def test_02_customs_whole_value():
    r = result("报关单号：222120260000123456")
    assert r.redacted["F1-P1"] in ("报关单号：编号_1", "报关单号：[证件号]")


@pytest.mark.parametrize("value", ["见附件", "待定", "另行约定", "同上"])
def test_03_project_empty_values(value):
    assert result("项目名称：" + value).redacted["F1-P1"] == "项目名称：" + value


def test_04_project_semicolon():
    assert result("项目名称：东川市虚构管网改造工程；工期 90 天").redacted["F1-P1"] == "项目名称：项目_1；工期 90 天"


def test_05_repeated_im_mapping():
    r = result("微信：zhang_fiction88 微信：zhang_fiction88")
    assert r.redacted["F1-P1"] == "微信：[联系方式] 微信：[联系方式]"
    assert r.mapping == {"[联系方式]#1": "zhang_fiction88"}


def test_06_im_map_blocks_leak():
    r = result("微信：zhang_fiction88")
    reasons = recheck("备注 zhang_fiction88", real_values=r.mapping.values(), terms=[])
    assert any(x.category == "MAP" for x in reasons)


def test_07_chat_requires_colon():
    assert _redact("微信：zhang_fiction88", {}, []).text == "微信：[联系方式_Q1]"
    assert _redact("对方微信 zhang_fiction88 说可以", {}, []).text == "对方微信 zhang_fiction88 说可以"


def test_note_uses_same_rules():
    assert result("微信：zhang_fiction88", "NOTE").redacted["NOTE"] == "微信：[联系方式]"


def test_project_unique_and_information_boundary():
    r = result("项目名称：东川虚构项目 联系人：张示例")
    assert r.redacted["F1-P1"] == "项目名称：项目_1 联系人：人员_1"
    assert r.mapping["项目_1"] == "东川虚构项目"


@pytest.mark.parametrize("label", [
    "提单号", "提单编号", "B/L No", "Bill of Lading No", "信用证号", "信用证编号", "L/C No", "Credit No",
    "报关单号", "报关单编号", "Customs Declaration No", "项目编号", "项目号", "Project No", "招标编号",
    "招标项目编号", "Tender No", "Bid No", "报价单号", "报价编号", "Quotation No", "Quote No", "运单号",
    "Waybill No", "集装箱号", "Container No"])
@pytest.mark.parametrize("separator", ["：", " ", "【"])
def test_all_reference_labels(label, separator):
    text = label + separator + "FICT-2026/09_A.1"
    assert any(h.category == "REFNO" and text[h.start:h.end] == "FICT-2026/09_A.1"
               for h in detect.find_formatted(text))


@pytest.mark.parametrize("label", ["护照号", "护照号码", "Passport No", "营业执照注册号", "注册号", "工商注册号",
                                  "组织机构代码", "Organization Code", "税号", "纳税人识别号", "Tax ID", "VAT No", "TIN"])
def test_all_id_labels(label):
    text = label + ": FA12-5678"
    assert any(h.category == "IDNO" and text[h.start:h.end] == "FA12-5678" for h in detect.find_formatted(text))
    assert not any(h.category == "IDNO" for h in detect.find_formatted(label + ": FICTIONAL"))


@pytest.mark.parametrize("label", ["行号", "联行号", "开户行行号", "大额支付行号", "CNAPS", "Sort Code", "Routing No", "ABA", "IFSC"])
def test_all_bank_labels(label):
    text = label + ": FICT001234"
    assert any(h.category == "SWIFT" and text[h.start:h.end] == "FICT001234" for h in detect.find_formatted(text))


@pytest.mark.parametrize("label", ["微信", "微信号", "WeChat", "QQ", "QQ号", "WhatsApp", "Skype", "Telegram", "LINE", "钉钉"])
def test_all_im_labels(label):
    text = label + ": fiction.01；备注"
    assert any(h.category == "IM" and text[h.start:h.end] == "fiction.01" for h in detect.find_formatted(text))


@pytest.mark.parametrize("label", ["项目名称", "工程名称", "项目全称", "Project Name", "Project Title"])
def test_all_project_labels(label):
    assert result(label + ": 虚构工程").redacted["F1-P1"] == label + ": 项目_1"
