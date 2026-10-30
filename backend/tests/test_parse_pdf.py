"""PDF 解析（M3-03 文字页部分）：manifest.yaml 中的预期事实、inspect_pdf、上限与损坏文件。"""
import io
import re
from pathlib import Path

import pytest
import yaml

from app.parsing.model import ParseError
from app.parsing.pdf import MSG_CORRUPT, MSG_TOO_MANY_PAGES, inspect_pdf, parse_pdf

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


def _page_text(parsed, page):
    return norm("".join(n.text for n in parsed.nodes if n.page == page))


def test_scanned_contract_ocr():
    sample = MANIFEST["scanned_contract"]
    parsed = parse_pdf(data_of("scanned_contract"), 1)
    assert parsed.page_count == 2 and parsed.ocr_pages == 2
    assert parsed.warnings == sample["warnings"]
    assert all(n.kind == "ocr_block" and n.part == "body" and n.ocr_conf is not None for n in parsed.nodes)
    for kw in sample["pages"][0]["ocr_keywords"]:
        assert norm(kw) in _page_text(parsed, 1), kw
    assert all(n.ocr_conf >= 0.80 for n in parsed.nodes if n.page == 1)  # 第 1 页清晰
    assert any(n.ocr_conf < 0.80 for n in parsed.nodes if n.page == 2)  # 第 2 页故意模糊
    assert [n.ord for n in parsed.nodes] == list(range(1, len(parsed.nodes) + 1))
    assert all(re.fullmatch(r"F1-PG[12]-B\d+", n.loc) for n in parsed.nodes)


def test_scanned_bbox_converted_to_pdf_points():
    """200 DPI 渲染的像素坐标除以 200/72 换算回 PDF 点：bbox 落在 A4 页面（595 x 842）之内。"""
    parsed = parse_pdf(data_of("scanned_contract"), 1)
    for n in parsed.nodes:
        x0, top, x1, bottom = n.bbox
        assert 0 <= x0 < x1 <= 595.3 and 0 <= top < bottom <= 841.9
    assert max(n.bbox[3] for n in parsed.nodes) > 100  # 不是缩得过小


def test_mixed_pdf_text_and_scanned():
    sample = MANIFEST["mixed"]
    parsed = parse_pdf(data_of("mixed"), 1)
    assert parsed.page_count == 3 and parsed.ocr_pages == 1
    assert parsed.warnings == sample["warnings"]
    pages = [n.page for n in parsed.nodes]
    assert pages == sorted(pages)
    assert {n.kind for n in parsed.nodes if n.page == 2} == {"ocr_block"}
    assert {n.kind for n in parsed.nodes if n.page != 2} == {"paragraph"}
    assert all(n.ocr_conf is None for n in parsed.nodes if n.page != 2)
    for kw in sample["pages"][1]["ocr_keywords"]:
        assert norm(kw) in _page_text(parsed, 2), kw


def test_f04_same_page_mixed_image_body():
    data = (FIXTURES / "mixed_same_page.pdf").read_bytes()
    assert inspect_pdf(data).scanned_pages == [1]  # 混排页也占任务 OCR 预算
    parsed = parse_pdf(data, 1)
    assert parsed.page_count == 2 and parsed.ocr_pages == 1
    assert "第 1 页为图文混排，已对图片部分做 OCR" in parsed.warnings
    page1 = [n for n in parsed.nodes if n.page == 1]
    assert any(n.kind == 'ocr_block' and 'FICTIONALIMAGEBODY' in norm(n.text) for n in page1)
    assert sum('FICTIVEMIXEDPAGEHEADER' in norm(n.text) for n in page1) == 1
    assert all(n.kind == 'paragraph' for n in parsed.nodes if n.page == 2)
    assert len({n.loc for n in parsed.nodes}) == len(parsed.nodes)
    assert [n.ord for n in parsed.nodes] == list(range(1, len(parsed.nodes) + 1))


def test_scanned_coordinates_and_empty_page_warning(monkeypatch):
    from app.parsing import ocr as ocr_mod

    sizes = []

    def fake(img):
        sizes.append(img.size)
        return [] if len(sizes) == 1 else [ocr_mod.OcrLine("销售合同 HT-1", [100, 100, 600, 160], 0.95)]

    monkeypatch.setattr(ocr_mod, "recognize", fake)
    parsed = parse_pdf(data_of("scanned_contract"), 1)
    assert "第 1 页未识别到文字" in parsed.warnings
    assert [n.page for n in parsed.nodes] == [2]
    assert abs(sizes[0][0] - 1654) <= 2 and abs(sizes[0][1] - 2339) <= 2  # A4 @ 200 DPI
    bbox = parsed.nodes[0].bbox
    assert abs(bbox[0] - 100 * 72 / 200) < 0.1 and abs(bbox[3] - 160 * 72 / 200) < 0.1


def test_low_conf_pages_listed_up_to_ten(monkeypatch):
    from app.parsing import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "recognize", lambda img: [ocr_mod.OcrLine("测试文字行内容", [10, 10, 300, 40], 0.5)])
    parsed = parse_pdf(_scan_like_pdf(12), 1)
    low = [w for w in parsed.warnings if "置信度低" in w]
    assert low[:10] == [f"第 {p} 页 OCR 识别置信度低，请核对原件" for p in range(1, 11)]
    assert low[10] == "等 2 页 OCR 识别置信度低，请核对原件" and len(low) == 11


def test_rotated_scanned_page_rendered_in_display_orientation(monkeypatch):
    """MediaBox 纵向（595 x 842）、/Rotate 90 的扫描页：显示方向为横向，渲染图与换算后的 bbox 都按显示方向。"""
    from app.parsing import ocr as ocr_mod

    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    buf = io.BytesIO()
    c = reportlab.Canvas(buf, pagesize=(841.89, 595.28), invariant=1)  # 设置旋转后 reportlab 把 MediaBox 写成纵向
    c.rect(50, 50, 100, 100)
    c.setPageRotation(90)
    c.showPage()
    c.save()

    sizes = []

    def fake(img):
        sizes.append(img.size)
        return [ocr_mod.OcrLine("旋转页文字", [100, 1000, 2200, 1100], 0.95)]

    monkeypatch.setattr(ocr_mod, "recognize", fake)
    parsed = parse_pdf(buf.getvalue(), 1)
    assert b"/MediaBox [ 0 0 595.28 841.89 ]" in buf.getvalue()
    assert sizes[0][0] > sizes[0][1]  # 显示方向为横向（不是 MediaBox 的纵向）
    bbox = parsed.nodes[0].bbox
    assert abs(bbox[2] - 2200 * 72 / 200) < 0.1 and bbox[2] <= 841.9 and bbox[3] <= 595.3


def test_scanned_pdf_without_any_text_fails(monkeypatch):
    from app.parsing import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "recognize", lambda img: [])
    with pytest.raises(ParseError) as e:
        parse_pdf(data_of("scanned_contract"), 1)
    assert str(e.value) == "未能从文件中识别出文字"


def _scan_like_pdf(n: int) -> bytes:
    """n 页空白页（没有文字对象，按扫描页处理）。"""
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    buf = io.BytesIO()
    c = reportlab.Canvas(buf, invariant=1)
    for _ in range(n):
        c.rect(50, 50, 100, 100)
        c.showPage()
    c.save()
    return buf.getvalue()


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
