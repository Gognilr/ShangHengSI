"""统一文档模型（SPEC 8.1，设计 docs/design/M2.md 第四节）。

各格式的解析器（DOCX、XLSX、PDF、图片）都输出 ParsedFile；pipeline 把节点写入 doc_nodes。
节点 text 是原文（入库时加密）；自动编号产生的编号只放在 clause_no，不放进 text。
"""
from dataclasses import dataclass


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


@dataclass
class ParsedFile:
    nodes: list[ParsedNode]
    page_count: int | None
    has_revisions: bool
    warnings: list[str]


class ParseError(Exception):
    """解析失败。消息是给用户看的中文原因，不得含原文。"""


def with_clause_no(text: str, clause_no: str | None) -> str:
    """显示和外发用的文本：text 不以 clause_no 开头时，在前面加上 clause_no 和一个空格。"""
    if not clause_no or text.startswith(clause_no):
        return text
    return f"{clause_no} {text}"
