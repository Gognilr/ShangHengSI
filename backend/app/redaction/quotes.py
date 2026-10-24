"""报价明细表汇总（SPEC 9.7 后半，设计 docs/design/M4.md 2.6，Q-18、Q-72）。

纯函数，不碰数据库：从正文节点中找出报价明细表，给出汇总行的文字。外发文本中整张表（表头行至最后一个数据行）
换成一行汇总，由 outbound.build 使用（M4-04 接入）。汇总行中的“本地校验：…”部分在 M6 补上。
"""
import re
from dataclasses import dataclass
from typing import Protocol

from app.redaction import detect, tables
from app.redaction.detect import _CODES, _CURRENCY

MIN_DATA_ROWS = 3  # 单价列中有数字的行不足 3 行时不算报价明细表
DETAIL_MAX_ROWS = 200
_PRODUCT_HEADER = re.compile(r"品名|产品|货物|名称|规格型号|description|product|commodity|item", re.IGNORECASE)
_DIGIT = re.compile(r"\d")
_HEADER_CURRENCY = re.compile(rf"(?<![A-Za-z])(?:US\$|HK\$|{_CODES})(?![A-Za-z])|人民币|美元|欧元|英镑|日元|港元|港币|[¥￥$€£]")
_SPACES = re.compile(r"\s+")


class _Node(Protocol):
    loc: str
    text: str


@dataclass(frozen=True)
class QuoteTable:
    loc: str  # 汇总行的位置编号（Q-18）：DOCX F1-T2，PDF F3-PG2-T1，XLSX F2-S1
    first_loc: str  # 汇总行放在这个节点的位置（表头行的第一个单元格）
    row_locs: frozenset[str]  # 被汇总替换掉的全部单元格（表头行至最后一个数据行）
    rows: int  # 数据行数
    currencies: list[str]  # 按出现顺序去重；都识别不到时为空
    products: list[str]  # 产品列的已脱敏文字，按出现顺序去重；没有产品列时为空
    detailed: bool = False


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _header_currencies(text: str) -> list[str]:
    out = []
    for m in _HEADER_CURRENCY.finditer(text):
        code = _CURRENCY.get(m.group().upper()) or _CURRENCY.get(m.group())
        if code:
            out.append(code)
    return out


def find_quote_tables(nodes: list[_Node], redacted: dict[str, str]) -> list[QuoteTable]:
    """报价明细表：表头行（Q-65）中有 PRICE 列，且表头行以下“单价列中有数字”的行不少于 3 行。

    nodes 为正文节点（原文），redacted 为 {loc: 脱敏后文本}，产品名称取脱敏后的文字。
    """
    header_row = tables.header_rows(nodes)
    cells: dict[str, list[tuple[int, str, int]]] = {}  # 表格标识 → [(节点下标, 列, 行号)]，文档顺序
    for i, n in enumerate(nodes):
        cell = tables.table_cell(n.loc)
        if cell is not None:
            cells.setdefault(cell[0], []).append((i, cell[1], cell[2]))
    result: list[QuoteTable] = []
    for table, members in cells.items():
        hrow = header_row.get(table)
        if hrow is None:
            continue
        header = [(i, col) for i, col, row in members if row == hrow]
        price_cols = {col for i, col in header if tables.header_kind(nodes[i].text) == "PRICE"}
        amount_cols = {col for i, col in header if tables.header_kind(nodes[i].text) == "AMOUNT"}
        if not price_cols:
            continue
        data_rows = sorted({row for i, col, row in members
                            if row > hrow and col in price_cols and _DIGIT.search(nodes[i].text)})
        if len(data_rows) < MIN_DATA_ROWS:
            continue
        last = data_rows[-1]
        covered = [(i, col, row) for i, col, row in members if hrow <= row <= last]
        row_set = set(data_rows)

        currencies: list[str] = []
        for i, col, row in covered:
            if row in row_set and (col in price_cols or col in amount_cols):
                kind = "PRICE" if col in price_cols else "AMOUNT"
                currencies += [h.currency for h in detect.find_amounts(nodes[i].text, kind) if h.currency]
        currencies = _dedupe(currencies)
        if not currencies:
            currencies = _dedupe([c for i, _col in header for c in _header_currencies(nodes[i].text)])

        product_col = next((col for i, col in header if _PRODUCT_HEADER.search(nodes[i].text)), None)
        products: list[str] = []
        if product_col is not None:
            for i, col, row in covered:
                if col == product_col and row in row_set:
                    text = _SPACES.sub(" ", redacted.get(nodes[i].loc, nodes[i].text)).strip()
                    if text:
                        products.append(text)
        result.append(QuoteTable(
            loc=table,
            first_loc=nodes[min(i for i, _col in header)].loc,
            row_locs=frozenset(nodes[i].loc for i, _col, _row in covered),
            rows=len(data_rows),
            currencies=currencies,
            products=_dedupe(products),
            detailed=len(data_rows) <= DETAIL_MAX_ROWS,
        ))
    return result


def summary_text(t: QuoteTable, note: str | None = None) -> str:
    """【报价明细表：共 12 行，币种 USD，产品：油系针状焦等 3 种】；M6 在末尾补“，本地校验：…”。"""
    parts = [f"共 {t.rows} 行", "币种 " + "、".join(t.currencies) if t.currencies else "币种未知"]
    if t.products and not t.detailed:
        parts.append(f"产品：{t.products[0]}" if len(t.products) == 1 else f"产品：{t.products[0]}等 {len(t.products)} 种")
    if note:
        parts.append(f"本地校验：{note}")
    return "【报价明细表：" + "，".join(parts) + ("；明细逐格列在下方" if t.detailed else "") + "】"
