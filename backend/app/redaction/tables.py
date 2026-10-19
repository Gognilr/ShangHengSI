"""表格工具（设计 docs/design/M4.md 2.4）：表格单元格位置编号、表头列提示。

从 engine.py 移入，行为不变（M2-08、Q-65）。tables.py 不导入 engine，节点只按属性（loc、text）使用。
"""
import re
from typing import Protocol

_TABLE_LOC = re.compile(r"^(?P<table>.+-T\d+)-R(?P<row>\d+)-C(?P<col>\d+)$")  # DOCX、PDF 表格单元格
_SHEET_LOC = re.compile(r"^(?P<table>F\d+-S\d+)-(?P<col>[A-Z]+)(?P<row>\d+)$")  # XLSX 单元格，整个工作表为一个表格
HEADER_SCAN_ROWS = 20  # 表头行只在每个表格的前 20 行中找（open_questions Q-65）
_PRICE_HEADER = re.compile(r"单价|unit\s*price|price", re.IGNORECASE)
_AMOUNT_HEADER = re.compile(r"金额|总价|总额|amount|total", re.IGNORECASE)
_QTY_HEADER = re.compile(r"数量|quantity|qty", re.IGNORECASE)


class _Node(Protocol):
    loc: str
    text: str


def table_cell(loc: str) -> tuple[str, str, int] | None:
    """表格单元格的 (表格标识, 列, 行号)；不是表格单元格时 None。"""
    m = _TABLE_LOC.match(loc) or _SHEET_LOC.match(loc)
    return (m["table"], m["col"], int(m["row"])) if m else None


def header_kind(text: str) -> str | None:
    """表头文字的类别：PRICE（单价）、AMOUNT（金额）、QTY（数量）；都不是时 None。单价、金额优先于数量。"""
    if _PRICE_HEADER.search(text):
        return "PRICE"
    if _AMOUNT_HEADER.search(text):
        return "AMOUNT"
    if _QTY_HEADER.search(text):
        return "QTY"
    return None


def header_rows(nodes: list[_Node]) -> dict[str, int]:
    """{表格标识: 表头行号}。表头行是每个表格前 20 行中第一个含单价或金额表头的行（Q-65）；数量表头不参与判定。"""
    header_row: dict[str, int] = {}
    for n in nodes:
        cell = table_cell(n.loc)
        if cell is None:
            continue
        table, _col, row = cell
        if (row <= HEADER_SCAN_ROWS and header_kind(n.text) in ("PRICE", "AMOUNT")
                and row < header_row.get(table, row + 1)):
            header_row[table] = row
    return header_row


def table_hints(nodes: list[_Node], *, with_qty: bool = False) -> dict[int, str]:
    """表格列提示（9.3 第 5 步，Q-65）：表头含“单价、Unit Price”的列为 PRICE，含“金额、总价、Amount”的列为 AMOUNT；
    with_qty=True 时含“数量、Quantity、Qty”的列为 QTY（数量脱敏开启时用）。

    只有表头行之下的单元格得到提示。没有表头行的表格没有列提示。返回 {节点下标: 类别}。
    """
    header_row = header_rows(nodes)
    cells: list[tuple[int, str, str, int]] = []
    for i, n in enumerate(nodes):
        cell = table_cell(n.loc)
        if cell is not None:
            cells.append((i, *cell))
    headers: dict[tuple[str, str], str] = {}
    for i, table, col, row in cells:
        if header_row.get(table) == row:
            kind = header_kind(nodes[i].text)
            if kind and (kind != "QTY" or with_qty):
                headers[(table, col)] = kind
    return {i: headers[(t, c)] for i, t, c, r in cells if r > header_row.get(t, r) and (t, c) in headers}
