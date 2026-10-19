"""PDF 解析（M3-03 文字页部分）：manifest.yaml 中的预期事实、inspect_pdf、上限与损坏文件。"""
import io
import re
from pathlib import Path

import pytest
import yaml

from app.parsing.model import ParseError
from app.parsing.pdf import MSG_CORRUPT, MSG_SCAN_PENDING, MSG_TOO_MANY_PAGES, inspect_pdf, parse_pdf

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pdf"
MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))["samples"]
TEXT_SAMPLES = ["text_contract", "rotated"]


def data_of(name: str) -> bytes:
    return (FIXTURES / MANIFEST[name]["file"]).read_bytes()


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


@pytest.mark.parametrize("name", TEXT_SAMPLES)
def test_manifest_blocks_and_tables(name):
    sample = MANIFEST[name]
    parsed = parse_pdf(data_of(name), 1)
    tol = sample["bbox_tolerance"]
    assert parsed.page_count == sample["page_count"]
    assert parsed.ocr_pages == 0
    assert parsed.warnings == sample["warnings"]
    assert [n.ord for n in parsed.nodes] == list(range(1, len(parsed.nodes) + 1))

    paragraphs = [n for n in parsed.nodes if n.kind == "paragraph"]
    assert len(paragraphs) == len(sample["blocks"])
    for node, exp in zip(paragraphs, sample["blocks"]):
        assert node.page == exp["page"]
        assert norm(node.text) == norm(exp["text"]), exp["text"]
        assert node.clause_no == exp["clause_no"], exp["text"]
        assert node.part == "body"
        for got, want in zip(node.bbox, exp["bbox"]):
            assert abs(got - want) <= tol, (exp["text"], node.bbox, exp["bbox"])

    cells = [n for n in parsed.nodes if n.kind == "table_cell"]
    expected_cells = [(t["page"], t["table"], c) for t in sample["tables"] for c in t["cells"]]
    assert len(cells) == len(expected_cells)
    for node, (page, tno, c) in zip(cells, expected_cells):
        assert node.loc == f"F1-PG{page}-T{tno}-R{c['row']}-C{c['col']}"
        assert node.text == c["text"]
        assert node.page == page


def test_locs_unique_and_format():
    parsed = parse_pdf(data_of("text_contract"), 2)
    locs = [n.loc for n in parsed.nodes]
    assert len(set(locs)) == len(locs)
    assert all(re.fullmatch(r"F2-PG\d+-(B\d+|T\d+-R\d+-C\d+)", loc) for loc in locs)
    # 每页内块序号从 1 开始、连续
    for page in (1, 2):
        nos = [int(n.loc.rsplit("-B", 1)[1]) for n in parsed.nodes if n.page == page and n.kind == "paragraph"]
        assert nos == list(range(1, len(nos) + 1))


def test_table_text_not_duplicated_in_paragraphs():
    """Q-16：表格区域内的文字只出现在单元格节点中。"""
    parsed = parse_pdf(data_of("text_contract"), 1)
    paragraphs = " ".join(n.text for n in parsed.nodes if n.kind == "paragraph")
    for word in ("USD 1,250.00", "超低硫", "合计 Total"):
        assert word not in paragraphs
        assert any(word in n.text for n in parsed.nodes if n.kind == "table_cell")
    assert [n.text for n in parsed.nodes if n.loc.endswith("T1-R4-C1")] == ["合计 Total"]
    assert not any(n.loc.endswith(("T1-R4-C2", "T1-R4-C3", "T1-R4-C4")) for n in parsed.nodes)


def test_nodes_in_reading_order():
    parsed = parse_pdf(data_of("text_contract"), 1)
    page1 = [n for n in parsed.nodes if n.page == 1]
    tops = [n.bbox[1] for n in page1 if n.kind == "paragraph" or n.loc.endswith("T1-R1-C1")]
    assert tops == sorted(tops)
    # 表格排在它上方的“货物明细见下表。”之后、页码行之前
    kinds = [n.kind for n in page1]
    assert kinds.index("table_cell") > max(i for i, n in enumerate(page1) if n.text == "货物明细见下表。")
    assert page1[-1].text.startswith("第 1 页")


def test_wrapped_paragraph_merged_into_one_block():
    parsed = parse_pdf(data_of("text_contract"), 1)
    node = next(n for n in parsed.nodes if n.text.startswith("合同总价为"))
    assert norm(node.text).endswith("余款在见提单副本后5个工作日内支付。")


def test_rotated_page_bbox_in_display_orientation():
    parsed = parse_pdf(data_of("rotated"), 1)
    page2 = [n for n in parsed.nodes if n.page == 2]
    # 显示方向为横向（宽 841.89）：文字从左向右排，bbox 的宽远大于高，且不超出显示范围
    for n in page2:
        x0, top, x1, bottom = n.bbox
        assert x1 - x0 > bottom - top
        assert 0 <= x0 < x1 <= 841.89 + 1 and 0 <= top < bottom <= 595.28 + 1
    assert page2[0].text == "旋转页：货物验收记录"
    assert [n.clause_no for n in page2] == [None, "第6条", None, None]


@pytest.mark.parametrize(
    "name,scanned",
    [("text_contract", []), ("scanned_contract", [1, 2]), ("mixed", [2]), ("rotated", [])],
)
def test_inspect_pdf(name, scanned):
    info = inspect_pdf(data_of(name))
    assert info.page_count == MANIFEST[name]["page_count"]
    assert info.scanned_pages == scanned


@pytest.mark.parametrize("name", ["scanned_contract", "mixed"])
def test_scanned_pages_placeholder(name):
    """M3-04 实现 OCR 前，含扫描页的文件抛出占位 ParseError。"""
    with pytest.raises(ParseError) as e:
        parse_pdf(data_of(name), 1)
    assert str(e.value) == MSG_SCAN_PENDING


@pytest.mark.parametrize("fn", [inspect_pdf, lambda d: parse_pdf(d, 1)])
def test_encrypted_and_corrupt(fn):
    for data in (data_of("encrypted"), b"", b"%PDF-1.4 secret-text garbage", data_of("text_contract")[:300]):
        with pytest.raises(ParseError) as e:
            fn(data)
        assert str(e.value) == MANIFEST["encrypted"]["expect_parse_error"] == MSG_CORRUPT
        assert "secret" not in str(e.value)


def _pdf_with_pages(n: int) -> bytes:
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    buf = io.BytesIO()
    c = reportlab.Canvas(buf, invariant=1)
    for i in range(n):
        c.drawString(72, 700, f"page {i + 1} some text that is long enough to be a text page")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_page_limit():
    assert inspect_pdf(_pdf_with_pages(200)).page_count == 200
    over = _pdf_with_pages(201)
    for fn in (inspect_pdf, lambda d: parse_pdf(d, 1)):
        with pytest.raises(ParseError) as e:
            fn(over)
        assert str(e.value) == MSG_TOO_MANY_PAGES
