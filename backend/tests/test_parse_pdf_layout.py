"""PDF 版面（V34-D-03）：页眉页脚分离、无线框报价表的受限 text 兜底、跨页续表在读表时拼接。样本临时生成，内容虚构。"""
import io

import pytest

from app.checks.amounts import check_tables
from app.checks.model import CheckNode
from app.parsing.pdf import parse_pdf

canvas = pytest.importorskip("reportlab.pdfgen.canvas")
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402

FONT = "WQY"
pdfmetrics.registerFont(TTFont(FONT, "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", subfontIndex=0))
W, H = 595.27, 841.89
COLS = [60, 100, 250, 320, 410, 530]  # 序号、名称、数量、单价、金额的列边界
HEAD = ["序号", "名称", "数量", "单价", "金额"]


def _pdf(pages) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H), invariant=1)
    for draw in pages:
        c.setFont(FONT, 10)
        draw(c)
        c.showPage()
    c.save()
    return buf.getvalue()


def _text(c, x, top, s, size=10):
    c.setFont(FONT, size)
    c.drawString(x, H - top, s)


def _rows(c, top, rows, ruled=True, height=22):
    """从 top 起画表格（PDF 坐标原点在左下），返回表格底边的 top。"""
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            _text(c, COLS[j] + 4, top + i * height + 15, cell)
    bottom = top + len(rows) * height
    if ruled:
        for i in range(len(rows) + 1):
            c.line(COLS[0], H - (top + i * height), COLS[-1], H - (top + i * height))
        for x in COLS:
            c.line(x, H - top, x, H - bottom)
    return bottom


def _checks(parsed):
    nodes = [CheckNode(n.loc, n.text, 1, n.clause_no, ocr_conf=n.ocr_conf, bbox=n.bbox) for n in parsed.nodes if n.part == "body"]
    return check_tables(nodes, [])


def test_running_header_and_page_numbers_leave_body():
    def page(i):
        def draw(c):
            _text(c, 72, 40, "示例科技有限公司 设备采购合同")
            for k in range(6):
                _text(c, 72, 160 + k * 60, f"第 {i * 6 + k + 1} 条 甲方应按约定期限支付本合同项下的各期款项，乙方按约交付货物并提供服务。")
            _text(c, 270, 810, f"第 {i + 1} 页 共 4 页")
        return draw

    parsed = parse_pdf(_pdf([page(i) for i in range(4)]), 1)
    header = [n for n in parsed.nodes if n.part == "header"]
    footer = [n for n in parsed.nodes if n.part == "footer"]
    assert len(header) == 4 and all("采购合同" in n.text for n in header)
    assert len(footer) == 4 and [n.page for n in footer] == [1, 2, 3, 4]
    body = [n for n in parsed.nodes if n.part == "body"]
    assert len(body) == 24 and not any("采购合同" in n.text or "共 4 页" in n.text for n in body)


def test_unruled_quotation_uses_restricted_text_strategy():
    rows = [HEAD, ["1", "服务器", "2", "15,000.00", "30,000.00"], ["2", "交换机", "3", "4,200.00", "12,000.00"],
            ["3", "机柜", "1", "3,500.00", "3,500.00"], ["合计", "", "", "", "45,500.00"]]

    def draw(c):
        _text(c, 72, 80, "报价单")
        _text(c, 72, 110, "本报价有效期为三十日，以下价格均为含税价，币种人民币，交货地点为买方指定仓库。")
        bottom = _rows(c, 140, rows, ruled=False)
        _text(c, 72, bottom + 40, "说明：以上报价包含运输、安装与一年质保，不含现场土建改造。")

    parsed = parse_pdf(_pdf([draw]), 1)
    cells = [n for n in parsed.nodes if n.kind == "table_cell"]
    assert {n.text for n in cells} >= {"单价", "15,000.00", "45,500.00"}
    assert not any("有效期" in n.text or "质保" in n.text for n in cells)  # 表外正文仍是段落
    tables, results = _checks(parsed)
    assert [(t.recognized, t.rows_checked, t.row_errors) for t in tables] == [(True, 3, 1)]
    assert [r.code for r in results] == ["L-01"]  # 第 2 行 3 × 4,200 ≠ 12,000；合计等于各行金额之和


def test_plain_text_page_gets_no_text_strategy_cells():
    def draw(c):
        for k in range(8):
            _text(c, 72, 100 + k * 30, f"第 {k + 1} 条 数量 单价 金额 由双方另行确认，付款期限 30 日，违约金 5 % 。")

    assert not [n for n in parse_pdf(_pdf([draw]), 1).nodes if n.kind == "table_cell"]


@pytest.mark.parametrize("repeat_header", [False, True])
def test_table_continued_on_next_page_checked_as_one(repeat_header):
    first = [HEAD, ["1", "服务器", "2", "15,000.00", "30,000.00"], ["2", "交换机", "3", "4,000.00", "12,000.00"]]
    second = ([HEAD] if repeat_header else []) + [["3", "机柜", "1", "3,500.00", "3,500.00"],
                                                  ["合计", "", "", "", "45,500.00"]]

    def page1(c):
        _text(c, 72, 80, "分项报价表")
        _rows(c, 700, first)

    def page2(c):
        _rows(c, 60, second)
        _text(c, 72, 300, "投标人：（盖章）")

    parsed = parse_pdf(_pdf([page1, page2]), 1)
    assert "第 1–2 页表格按续表合并核对" in parsed.warnings
    tables, results = _checks(parsed)
    assert [(t.table, t.rows_checked, t.row_errors, t.total_ok) for t in tables] == [("F1-PG1-T1", 3, 0, True)]
    assert results == []
