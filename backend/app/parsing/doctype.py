"""文件类型识别（M2-04，SPEC 8.6）。

只看文件名和正文前 1000 字，按类型顺序匹配关键词，先命中先定。
英文关键词不区分大小写；中文关键词按原样匹配。都不命中时返回 letter。
"""

# 按 SPEC 8.6 的顺序排列：类型顺序即优先级
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("contract", ("合同", "协议", "Contract", "Agreement")),
    ("quotation", ("报价", "报价单", "Quotation", "Offer", "Proforma")),
    ("order", ("订单", "采购单", "供货单", "Purchase Order", "P.O.")),
    ("inquiry", ("询价", "Inquiry", "Enquiry", "RFQ")),
)

_HEAD_LIMIT = 1000


def detect_doc_type(filename: str, text_head: str) -> str:
    """返回 contract / quotation / order / inquiry / letter 之一。"""
    # 调用方可能传入更长的正文，这里再截一次，保证与 SPEC 一致
    haystack = f"{filename}\n{text_head[:_HEAD_LIMIT]}".casefold()
    for doc_type, keywords in _KEYWORDS:
        for keyword in keywords:
            if keyword.casefold() in haystack:
                return doc_type
    return "letter"
