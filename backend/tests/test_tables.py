"""表格工具（M4-03，设计 docs/design/M4.md 2.4）：从 engine.py 移入，行为不变；新增 QTY 表头。"""
from pathlib import Path

import pytest

from app.redaction import tables
from app.redaction.engine import NodeIn
from app.redaction.tables import header_kind, header_rows, table_cell, table_hints


@pytest.mark.parametrize("loc,expected", [
    ("F1-T1-R2-C3", ("F1-T1", "3", 2)),  # DOCX、PDF 表格的列是数字
    ("F1-T2-R10-C1", ("F1-T2", "1", 10)),
    ("F3-PG2-T1-R4-C2", ("F3-PG2-T1", "2", 4)),  # PDF
    ("F2-S1-AB12", ("F2-S1", "AB", 12)),  # XLSX 的列是字母
    ("F1-T2-R3-C1-T1-R1-C2", ("F1-T2-R3-C1-T1", "2", 1)),  # 嵌套表格
    ("F1-P23", None),
    ("F1-S1", None),
    ("F1-PG1-B3", None),
])
def test_table_cell(loc, expected):
    assert table_cell(loc) == expected


@pytest.mark.parametrize("text,expected", [
    ("单价", "PRICE"), ("Unit Price", "PRICE"), ("unit  price (USD)", "PRICE"), ("Price", "PRICE"),
    ("金额", "AMOUNT"), ("总价", "AMOUNT"), ("总额", "AMOUNT"), ("Amount (USD)", "AMOUNT"), ("TOTAL", "AMOUNT"),
    ("数量", "QTY"), ("数量(MT)", "QTY"), ("Quantity", "QTY"), ("QTY", "QTY"), ("qty (pcs)", "QTY"),
    ("品名", None), ("序号", None), ("", None),
    ("单价 数量", "PRICE"),  # 单价、金额优先于数量
    ("Total Quantity", "AMOUNT"),
])
def test_header_kind(text, expected):
    assert header_kind(text) == expected


def test_header_row_ignores_qty_header():
    """QTY 表头不参与表头行判定：表头行仍是前 20 行中第一个含单价或金额表头的行。"""
    nodes = [
        NodeIn("F1-T1-R1-C1", "数量"), NodeIn("F1-T1-R1-C2", "品名"),  # 只有数量表头的行不是表头行
        NodeIn("F1-T1-R2-C1", "数量"), NodeIn("F1-T1-R2-C2", "单价"),
        NodeIn("F1-T1-R3-C1", "320"), NodeIn("F1-T1-R3-C2", "1250"),
    ]
    assert header_rows(nodes) == {"F1-T1": 2}
    nodes_qty_only = [NodeIn("F1-T1-R1-C1", "数量"), NodeIn("F1-T1-R2-C1", "320")]
    assert header_rows(nodes_qty_only) == {}
    assert table_hints(nodes_qty_only, with_qty=True) == {}


def test_table_hints_qty_only_with_flag():
    nodes = [
        NodeIn("F1-T1-R1-C1", "数量"), NodeIn("F1-T1-R1-C2", "单价"), NodeIn("F1-T1-R1-C3", "金额"),
        NodeIn("F1-T1-R2-C1", "320"), NodeIn("F1-T1-R2-C2", "1250"), NodeIn("F1-T1-R2-C3", "400000"),
    ]
    assert table_hints(nodes) == {4: "PRICE", 5: "AMOUNT"}
    assert table_hints(nodes, with_qty=True) == {3: "QTY", 4: "PRICE", 5: "AMOUNT"}


def test_table_hints_header_row_below_title_rows_and_first_20_rows():
    nodes = [
        NodeIn("F1-S1-A1", "报价单"), NodeIn("F1-S1-C7", "数量(MT)"), NodeIn("F1-S1-D7", "Unit Price"),
        NodeIn("F1-S1-C8", "320"), NodeIn("F1-S1-D8", "1250"),
        NodeIn("F1-S2-B21", "单价"), NodeIn("F1-S2-B22", "1250"),  # 第 21 行不再找表头
        NodeIn("F1-S3-B20", "单价"), NodeIn("F1-S3-B22", "1250"),
    ]
    assert table_hints(nodes, with_qty=True) == {3: "QTY", 4: "PRICE", 8: "PRICE"}


def test_tables_module_has_no_engine_dependency():
    source = Path(tables.__file__).read_text(encoding="utf-8")
    assert "engine" not in source.replace("从 engine.py 移入", "").replace("不导入 engine", "")
    assert tables.HEADER_SCAN_ROWS == 20


# ---------------------------------------------------------------- R1-03（design/R1.md 第三节，Q-134 ③）


def _cells(rows: dict[int, list[str]], table: str = "F1-T1") -> list[NodeIn]:
    return [NodeIn(f"{table}-R{r}-C{c}", text) for r, texts in rows.items() for c, text in enumerate(texts, start=1) if text]


def test_multiple_header_rows_and_total_rows():
    nodes = _cells({
        1: ["清单"],
        2: ["序号", "名称", "单价(元)", "总数量", "金额(元)"],
        3: ["1", "道闸", "7,500.00", "12", "90,000.00"],
        4: ["小计", "", "", "440000", ""],  # 小计落在数量列
        5: ["结算价合计"],
        6: ["序号", "项目", "金额", "备注"],  # 第二个表头：没有纯数字
        7: ["1", "硬件金额", "710,000.00", "含运费"],
        8: ["2", "报价合计", "1,150,000.00", "含税"],
    })
    hints = {nodes[i].loc: kind for i, kind in table_hints(nodes).items()}
    assert hints["F1-T1-R3-C3"] == "PRICE" and hints["F1-T1-R3-C5"] == "AMOUNT"
    assert "F1-T1-R3-C4" not in hints  # 数量列不脱敏时没有提示
    assert hints["F1-T1-R4-C4"] == "AMOUNT"  # 合计行的纯数字一律按金额
    assert hints["F1-T1-R7-C3"] == "AMOUNT" and hints["F1-T1-R8-C3"] == "AMOUNT"  # 第二个表头管到表尾
    assert "F1-T1-R7-C1" not in hints and "F1-T1-R6-C3" not in hints
    assert header_rows(nodes) == {"F1-T1": 2}  # 汇总仍按第一个表头


def test_total_column_names_and_total_row_not_header():
    nodes = _cells({
        2: ["序号", "设备名称", "数量", "单价", "合计"],
        3: ["1", "半球摄像机", "25", "520", "13000"],
        4: ["合计金额", "", "", "", "222063"],  # 含“金额”但有纯数字：合计行，不是表头
    })
    hints = {nodes[i].loc: kind for i, kind in table_hints(nodes).items()}
    assert hints == {"F1-T1-R3-C4": "PRICE", "F1-T1-R3-C5": "AMOUNT", "F1-T1-R4-C5": "AMOUNT"}
    assert header_kind("合计（元）") == "AMOUNT" and header_kind("小计") == "AMOUNT" and header_kind("Subtotal") == "AMOUNT"


def test_total_row_in_table_without_header_and_text_cells_untouched():
    nodes = _cells({1: ["项目", "说明"], 2: ["总计", "300000", "约 30 万"]})
    hints = {nodes[i].loc: kind for i, kind in table_hints(nodes).items()}
    assert hints == {"F1-T1-R2-C2": "AMOUNT"}
