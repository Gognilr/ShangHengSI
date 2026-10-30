"""统一文档模型（SPEC 8.1，设计 docs/design/M2.md 第四节）。

各格式的解析器（DOCX、XLSX、PDF、图片）都输出 ParsedFile；pipeline 把节点写入 doc_nodes。
节点 text 是原文（入库时加密）；自动编号产生的编号只放在 clause_no，不放进 text。
"""
from dataclasses import dataclass


def strip_invisible(text: str) -> str:
    """删除复制文本中妨碍名称匹配的零宽字符（V2-F-01）。"""
    return text.translate(dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad")))


@dataclass
class ParsedNode:
    loc: str  # 位置编号，任务内唯一（SPEC 8.2、open_questions Q-15）
    ord: int  # 文件内的顺序，从 1 开始
    kind: str  # heading / paragraph / list_item / table_cell / sheet_cell / ocr_block
    part: str  # body / header / footer / comment / textbox / hidden_sheet
    text: str
    clause_no: str | None = None
    parent_loc: str | None = None  # 嵌套表格单元格：外层单元格；批注、文本框：所在的段落或单元格
    page: int | None = None
    bbox: list[float] | None = None
    sheet: str | None = None
    cell_ref: str | None = None
    ocr_conf: float | None = None
    has_revision: bool = False

    def __post_init__(self) -> None:
        self.text = strip_invisible(self.text)


@dataclass
class ParsedFile:
    nodes: list[ParsedNode]
    page_count: int | None
    has_revisions: bool
    warnings: list[str]
    ocr_pages: int = 0  # 本文件实际做了 OCR 的页数（图片记 1）


# 解析限制与阈值（SPEC 8.5、8.7）
MAX_PDF_PAGES = 200  # 单个 PDF 的页数上限
MAX_OCR_PAGES_PER_TASK = 100  # 整个任务合计的 OCR 页数上限
MAX_XLSX_CELLS = 20000  # 单个 XLSX 的非空单元格上限
LOW_OCR_CONF = 0.80  # 块平均置信度低于此值为低置信


class ParseError(Exception):
    """解析失败。消息是给用户看的中文原因，不得含原文。"""


def with_clause_no(text: str, clause_no: str | None) -> str:
    """显示和外发用的文本：text 不以 clause_no 开头时，在前面加上 clause_no 和一个空格。"""
    if not clause_no or text.startswith(clause_no):
        return text
    return f"{clause_no} {text}"
