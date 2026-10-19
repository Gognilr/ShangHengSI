"""报价明细表汇总（M4-03，SPEC 9.7，设计 docs/design/M4.md 2.6，Q-18）。DOCX、PDF、XLSX 三种表格位置编号各有测试。"""
from app.redaction.engine import NodeIn
from app.redaction.quotes import QuoteTable, find_quote_tables, summary_text


def docx_table(prefix: str, rows: list[list[str]]) -> list[NodeIn]:
    """prefix 如 F1-T2（DOCX）或 F3-PG2-T1（PDF）；rows[0] 为第 1 行。"""
    return [NodeIn(f"{prefix}-R{r}-C{c}", text) for r, row in enumerate(rows, start=1)
            for c, text in enumerate(row, start=1) if text != ""]


def xlsx_table(prefix: str, rows: list[list[str]], first_row: int = 1) -> list[NodeIn]:
    letters = "ABCDEFGH"
    return [NodeIn(f"{prefix}-{letters[c]}{r}", text) for r, row in enumerate(rows, start=first_row)
            for c, text in enumerate(row) if text != ""]


QUOTE_ROWS = [
    ["序号", "品名", "数量", "单价(USD/MT)", "金额(USD)"],
    ["1", "煅后石油焦", "320", "USD 1250", "USD 400000"],
    ["2", "针状焦", "120", "2150", "258000"],
    ["3", "煅后石油焦", "80", "1250", "100000"],
    ["", "合计 Total", "", "", "USD 758000"],
]


def redacted_of(nodes: list[NodeIn]) -> dict[str, str]:
    return {n.loc: n.text for n in nodes}


def test_docx_quote_table():
    nodes = docx_table("F1-T2", QUOTE_ROWS)
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.loc == "F1-T2"  # Q-18：DOCX F1-T{表}
    assert t.first_loc == "F1-T2-R1-C1"
    assert t.rows == 3
    assert t.currencies == ["USD"]
    assert t.products == ["煅后石油焦", "针状焦"]
    # 表头行至最后一个数据行整表替换；合计行照常逐格发送
    assert "F1-T2-R4-C5" in t.row_locs and "F1-T2-R1-C2" in t.row_locs
    assert not any(loc.startswith("F1-T2-R5") for loc in t.row_locs)
    assert len(t.row_locs) == 4 * 5
    assert summary_text(t) == "【报价明细表：共 3 行，币种 USD，产品：煅后石油焦等 2 种】"


def test_pdf_quote_table():
    nodes = docx_table("F3-PG2-T1", QUOTE_ROWS)
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.loc == "F3-PG2-T1"  # Q-18：PDF F3-PG{页}-T{表}
    assert t.first_loc == "F3-PG2-T1-R1-C1" and t.rows == 3


def test_xlsx_quote_table_with_title_rows_above():
    title = [NodeIn("F2-S1-A1", "报价单 Quotation"), NodeIn("F2-S1-A2", "卖方：星河虚构进出口有限公司")]
    body = xlsx_table("F2-S1", QUOTE_ROWS, first_row=7)
    nodes = title + body
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.loc == "F2-S1"  # Q-18：XLSX F2-S1
    assert t.first_loc == "F2-S1-A7"
    assert t.rows == 3 and t.currencies == ["USD"]
    # 表头上方的标题、签约方信息，表格下方的合计行照常发送
    assert "F2-S1-A1" not in t.row_locs and "F2-S1-A2" not in t.row_locs
    assert not any(loc.endswith("11") for loc in t.row_locs)
    assert "F2-S1-E10" in t.row_locs and "F2-S1-A7" in t.row_locs


def test_fewer_than_three_data_rows_not_summarized():
    rows = QUOTE_ROWS[:3]  # 表头 + 2 个数据行
    for nodes in (docx_table("F1-T1", rows), xlsx_table("F1-S1", rows)):
        assert find_quote_tables(nodes, redacted_of(nodes)) == []
    # 刚好 3 个数据行
    nodes = docx_table("F1-T1", QUOTE_ROWS[:4])
    assert len(find_quote_tables(nodes, redacted_of(nodes))) == 1


def test_data_rows_are_rows_with_digits_in_price_column():
    rows = [
        ["品名", "单价"],
        ["油系针状焦", "面议"],  # 单价列无数字：不是数据行
        ["煅后焦", "1250"],
        ["石墨电极", "2150"],
        ["沥青焦", "980"],
        ["备注：以上价格含税", ""],  # 最后一个数据行之后的行不汇总
    ]
    nodes = docx_table("F1-T1", rows)
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.rows == 3
    assert "F1-T1-R2-C1" in t.row_locs  # 首个数据行之前的非数据行在“表头行至最后一个数据行”之间，一并汇总
    assert "F1-T1-R6-C1" not in t.row_locs


def test_no_price_column_not_a_quote_table():
    rows = [["品名", "金额"], ["A", "100"], ["B", "200"], ["C", "300"]]
    nodes = docx_table("F1-T1", rows)
    assert find_quote_tables(nodes, redacted_of(nodes)) == []
    # 没有表头行
    nodes = docx_table("F1-T1", [["A", "100"], ["B", "200"], ["C", "300"]])
    assert find_quote_tables(nodes, redacted_of(nodes)) == []


def test_header_only_in_first_20_rows():
    rows = [["x", "y"]] * 20 + [["品名", "单价"], ["A", "1"], ["B", "2"], ["C", "3"]]
    nodes = docx_table("F1-T1", rows)
    assert find_quote_tables(nodes, redacted_of(nodes)) == []


def test_two_tables_independent():
    nodes = docx_table("F1-T1", QUOTE_ROWS) + docx_table("F1-T2", QUOTE_ROWS[:2]) + docx_table("F1-T3", QUOTE_ROWS)
    tables = find_quote_tables(nodes, redacted_of(nodes))
    assert [t.loc for t in tables] == ["F1-T1", "F1-T3"]


def test_products_use_redacted_text_and_dedupe():
    nodes = docx_table("F1-T1", QUOTE_ROWS)
    red = redacted_of(nodes)
    red["F1-T1-R2-C2"] = "  煅后石油焦\n（低硫）  "
    red["F1-T1-R4-C2"] = "煅后石油焦"
    (t,) = find_quote_tables(nodes, red)
    assert t.products == ["煅后石油焦 （低硫）", "针状焦", "煅后石油焦"]
    assert summary_text(t).endswith("产品：煅后石油焦 （低硫）等 3 种】")


def test_product_column_headers():
    for header in ["品名", "产品", "货物", "名称", "规格型号", "Description", "Product", "Commodity", "Item"]:
        rows = [["序号", header, "单价"], ["1", "A", "1"], ["2", "B", "2"], ["3", "A", "3"]]
        nodes = docx_table("F1-T1", rows)
        (t,) = find_quote_tables(nodes, redacted_of(nodes))
        assert t.products == ["A", "B"], header


def test_first_matching_product_column_wins():
    rows = [["货物名称", "规格型号", "单价"], ["焦炭", "A1", "1"], ["沥青", "B2", "2"], ["石墨", "C3", "3"]]
    nodes = docx_table("F1-T1", rows)
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.products == ["焦炭", "沥青", "石墨"]


def test_no_product_column():
    rows = [["序号", "单价"], ["1", "USD 1"], ["2", "USD 2"], ["3", "USD 3"]]
    nodes = docx_table("F1-T1", rows)
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.products == []
    assert summary_text(t) == "【报价明细表：共 3 行，币种 USD】"


def test_currencies_order_and_dedupe():
    rows = [["品名", "单价", "金额"], ["A", "USD 1", "¥ 7"], ["B", "USD 2", "¥ 14"], ["C", "EUR 3", "¥ 21"]]
    nodes = docx_table("F1-T1", rows)
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.currencies == ["USD", "CNY", "EUR"]
    assert "币种 USD、CNY、EUR" in summary_text(t)


def test_currency_from_header_when_cells_have_none():
    rows = [["品名", "单价(USD/MT)"], ["A", "1250"], ["B", "2150"], ["C", "980"]]
    nodes = docx_table("F1-T1", rows)
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.currencies == ["USD"]
    rows = [["品名", "单价（¥）"], ["A", "1250"], ["B", "2150"], ["C", "980"]]
    nodes = docx_table("F1-T1", rows)
    assert find_quote_tables(nodes, redacted_of(nodes))[0].currencies == ["CNY"]


def test_currency_unknown():
    rows = [["品名", "单价"], ["A", "1250"], ["B", "2150"], ["C", "980"]]
    nodes = docx_table("F1-T1", rows)
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.currencies == []
    assert summary_text(t) == "【报价明细表：共 3 行，币种未知，产品：A等 3 种】"


def test_summary_text_formats():
    base = dict(loc="F1-T1", first_loc="F1-T1-R1-C1", row_locs=frozenset(), rows=12)
    assert summary_text(QuoteTable(**base, currencies=["USD"], products=["油系针状焦", "针状焦", "沥青焦"])) == \
        "【报价明细表：共 12 行，币种 USD，产品：油系针状焦等 3 种】"
    assert summary_text(QuoteTable(**base, currencies=["USD"], products=["油系针状焦"])) == \
        "【报价明细表：共 12 行，币种 USD，产品：油系针状焦】"
    assert summary_text(QuoteTable(**base, currencies=[], products=[])) == "【报价明细表：共 12 行，币种未知】"
    assert summary_text(QuoteTable(**base, currencies=["USD", "EUR"], products=[])) == "【报价明细表：共 12 行，币种 USD、EUR】"


def test_non_table_nodes_ignored():
    nodes = [NodeIn("F1-P1", "报价单")] + docx_table("F1-T1", QUOTE_ROWS) + [NodeIn("F1-P9", "备注")]
    (t,) = find_quote_tables(nodes, redacted_of(nodes))
    assert t.first_loc == "F1-T1-R1-C1" and "F1-P1" not in t.row_locs and "F1-P9" not in t.row_locs
