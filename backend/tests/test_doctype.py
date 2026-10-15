"""文件类型识别（M2-04，SPEC 8.6）。纯函数测试，不需要数据库。"""
import pytest

from app.parsing.doctype import detect_doc_type


@pytest.mark.parametrize(
    "filename, text, expected",
    [
        # contract：中文与英文关键词，文件名或正文命中均可
        ("基础合同.docx", "", "contract"),
        ("abc.docx", "本协议由双方签署", "contract"),
        ("terms.pdf", "This Agreement is made", "contract"),
        ("x.docx", "CONTRACT NO. 1", "contract"),
        # quotation
        ("报价单.xlsx", "", "quotation"),
        ("offer.pdf", "", "quotation"),
        ("x.docx", "Proforma Invoice", "quotation"),
        ("x.docx", "报价如下", "quotation"),
        # order
        ("采购单.docx", "", "order"),
        ("x.docx", "供货单编号 12", "order"),
        ("x.docx", "Purchase Order No. 9", "order"),
        ("x.docx", "PO 见附件，P.O. 2026", "order"),
        ("订单.xlsx", "", "order"),
        # inquiry
        ("询价函.docx", "", "inquiry"),
        ("RFQ-001.pdf", "", "inquiry"),
        ("x.docx", "Enquiry for steel", "inquiry"),
        ("x.docx", "inquiry", "inquiry"),
        # letter：都不命中
        ("函件.docx", "特此说明", "letter"),
        ("x.docx", "", "letter"),
    ],
)
def test_each_type(filename, text, expected):
    assert detect_doc_type(filename, text) == expected


def test_order_priority_contract_before_quotation():
    # 同时含合同和报价关键词：按 8.6 顺序，contract 先定
    assert detect_doc_type("报价合同.docx", "") == "contract"


def test_order_priority_quotation_before_order():
    assert detect_doc_type("x.docx", "报价与订单") == "quotation"


def test_filename_and_text_both_checked():
    # 文件名不命中，正文命中
    assert detect_doc_type("file.docx", "询价说明") == "inquiry"


def test_english_case_insensitive():
    assert detect_doc_type("x.docx", "aGrEeMeNt") == "contract"
    assert detect_doc_type("OFFER.PDF", "") == "quotation"


def test_only_first_1000_chars_of_text_checked():
    text = "无" * 1000 + "合同"
    assert detect_doc_type("x.docx", text) == "letter"
    # 关键词完整落在前 1000 字内时命中
    assert detect_doc_type("x.docx", "无" * 998 + "合同") == "contract"
    # 关键词只有一半落在前 1000 字内时不命中
    assert detect_doc_type("x.docx", "无" * 999 + "合同") == "letter"
