"""表格工具（设计 docs/design/M4.md 2.4）：表格单元格位置编号、表头列提示。

从 engine.py 移入，行为不变（M2-08、Q-65）。tables.py 不导入 engine，节点只按属性（loc、text）使用。
R1-03（design/R1.md 第三节，Q-134 ③）：一个表格可以有多个表头行；“合计、小计、总计”列名算金额列；
合计行的纯数字一律按金额处理。
"""
import re
from typing import Protocol

from app.redaction.detect import _NUM, amount_label_kind

_TABLE_LOC = re.compile(r"^(?P<table>.+-T\d+)-R(?P<row>\d+)-C(?P<col>\d+)$")  # DOCX、PDF 表格单元格
_SHEET_LOC = re.compile(r"^(?P<table>F\d+-S\d+)-(?P<col>[A-Z]+)(?P<row>\d+)$")  # XLSX 单元格，整个工作表为一个表格
HEADER_SCAN_ROWS = 20  # 第一个表头行只在每个表格的前 20 行中找（open_questions Q-65）
_PRICE_HEADER = re.compile(r"单价|unit\s*price|price", re.IGNORECASE)
_AMOUNT_HEADER = re.compile(r"金额|总价|总额|合计|小计|总计|amount|total", re.IGNORECASE)
_QTY_HEADER = re.compile(r"数量|quantity|qty", re.IGNORECASE)
_TOTAL_ROW = re.compile(r"合计|小计|总计|(?<![A-Za-z])(?:sub\s*)?total(?![A-Za-z])", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"^\s*(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*$")


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


def _rows(nodes: list[_Node]) -> dict[str, dict[int, list[tuple[int, str]]]]:
    """{表格标识: {行号: [(节点下标, 列)]}}。"""
    out: dict[str, dict[int, list[tuple[int, str]]]] = {}
    for i, n in enumerate(nodes):
        cell = table_cell(n.loc)
        if cell is not None:
            table, col, row = cell
            out.setdefault(table, {}).setdefault(row, []).append((i, col))
    return out


def _is_header(nodes: list[_Node], cells: list[tuple[int, str]]) -> bool:
    """表头行：有单价或金额表头，且没有纯数字单元格（“合计金额 | 222063”这样的合计行不是表头）。"""
    texts = [nodes[i].text for i, _ in cells]
    return (any(header_kind(t) in ("PRICE", "AMOUNT") for t in texts)
            and not any(_BARE_NUMBER.match(t) for t in texts))


def _header_rows_all(nodes: list[_Node], rows: dict[int, list[tuple[int, str]]]) -> list[int]:
    """一个表格的全部表头行（升序）：第一个限前 20 行（Q-65），之后的不限行号。"""
    found = sorted(r for r, cells in rows.items() if _is_header(nodes, cells))
    if not found or found[0] > HEADER_SCAN_ROWS:
        return []
    return found


def header_rows(nodes: list[_Node]) -> dict[str, int]:
    """{表格标识: 第一个表头行号}。报价明细表汇总（9.7）按第一个表头行确定范围（design/R1.md 第三节第 4 条）。"""
    out: dict[str, int] = {}
    for table, rows in _rows(nodes).items():
        found = _header_rows_all(nodes, rows)
        if found:
            out[table] = found[0]
    return out


def _is_total_row(nodes: list[_Node], cells: list[tuple[int, str]]) -> bool:
    """合计行：行内第一个非空单元格含“合计、小计、总计、Total、Subtotal”。"""
    first = next((nodes[i].text for i, _ in sorted(cells, key=lambda c: _col_order(c[1])) if nodes[i].text.strip()), "")
    return bool(_TOTAL_ROW.search(first))


def _col_order(col: str) -> tuple[int, str]:
    return (len(col), col) if col.isalpha() else (int(col), "")


def _col_number(col: str) -> int:
    if col.isdigit():
        return int(col)
    value = 0
    for c in col:
        value = value * 26 + ord(c) - ord("A") + 1
    return value


def table_hints(nodes: list[_Node], *, with_qty: bool = False) -> dict[int, str]:
    """表格列提示（9.3 第 5 步，Q-65、Q-134 ③）：表头含“单价、Unit Price”的列为 PRICE，含“金额、总价、合计、Amount”的列为 AMOUNT；
    with_qty=True 时含“数量、Quantity、Qty”的列为 QTY（数量脱敏开启时用）。

    每个表头行的列提示管到下一个表头行为止；表头行本身和第一个表头行之上的单元格没有提示。
    合计行中的纯数字单元格一律为 AMOUNT（宁可多替换）。返回 {节点下标: 类别}。
    """
    out: dict[int, str] = {}
    for rows in _rows(nodes).values():
        headers = _header_rows_all(nodes, rows)
        header_set = set(headers)
        cols: dict[str, str] = {}
        current = None
        for row in sorted(rows):
            cells = rows[row]
            if row in header_set:
                current = row
                cols = {}
                for i, col in cells:
                    kind = header_kind(nodes[i].text)
                    if kind and (kind != "QTY" or with_qty):
                        cols[col] = kind
                continue
            if current is not None:
                for i, col in cells:
                    if col in cols:
                        out[i] = cols[col]
            if _is_total_row(nodes, cells):
                for i, _col in cells:
                    if _BARE_NUMBER.match(nodes[i].text):
                        out[i] = "AMOUNT"
        # R4：仅认同一行紧邻左格；不跨空列、不覆盖已有表头提示。
        for cells in rows.values():
            by_col = {_col_number(col): i for i, col in cells}
            for col, i in by_col.items():
                left = by_col.get(col - 1)
                if i not in out and left is not None and re.fullmatch(rf"[ \t]*({_NUM})万?[ \t]*", nodes[i].text):
                    kind = amount_label_kind(nodes[left].text)
                    if kind:
                        out[i] = kind
    return out
