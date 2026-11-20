"""图像凭证识字（V35-C-01）：PDF 文字页上的独立大图局部 OCR，DOCX 内嵌图片 OCR（当场 / 延后）与导出锚定。

图片用虚构的 quotation_clear.png（内容见 fixtures/images/manifest.yaml），真实 OCR。
"""
import io
import re
from pathlib import Path

import pytest
from PIL import Image

from app.parsing import docx_images
from app.parsing.docx import parse_docx
from app.parsing.pdf import inspect_pdf, parse_pdf

IMAGE = Path(__file__).resolve().parent / "fixtures" / "images" / "quotation_clear.png"
BODY = "本页为投标文件的说明文字，以下附保证金回单截图及营业执照复印件，内容均为虚构，仅用于测试。" * 4


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _seal_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), "red").save(buf, "PNG")
    return buf.getvalue()


def _pdf() -> bytes:
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("WQY", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", subfontIndex=0))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595.27, 841.89), invariant=1)
    c.setFont("WQY", 10)
    for i in range(0, len(BODY), 45):  # 页上部 ≥ 200 字的矢量文字
        c.drawString(50, 800 - i // 45 * 14, BODY[i:i + 45])
    c.drawImage(ImageReader(str(IMAGE)), 60, 260, width=420, height=420 * 0.6)  # 独立大图
    c.drawImage(ImageReader(io.BytesIO(_seal_png())), 450, 80, width=100, height=100)  # 印章大小，不识别
    c.showPage()
    c.save()
    return buf.getvalue()


def test_pdf_text_page_large_image_is_cropped_and_ocred():
    data = _pdf()
    assert inspect_pdf(data).scanned_pages == [1]  # 计入 OCR 页数
    parsed = parse_pdf(data, 1)
    assert parsed.ocr_pages == 1 and "第 1 页为图文混排，已对图片部分做 OCR" in parsed.warnings
    ocr = [n for n in parsed.nodes if n.ocr_conf is not None]
    text = norm("".join(n.text for n in ocr))
    assert "QT-2026-0508" in text and "报价函" in text
    assert all(n.bbox[1] >= 841.89 - 260 - 252 - 2 and n.bbox[3] <= 841.89 - 260 + 2 for n in ocr)  # 只在图片区域内
    assert sum(norm(n.text).count("仅用于测试") for n in parsed.nodes) == 4  # 矢量文字不重复
    assert len({n.loc for n in parsed.nodes}) == len(parsed.nodes)
    # 名额用完：图片区域不识别、文件不失败
    over = parse_pdf(data, 1, region_budget=0)
    assert over.ocr_pages == 0 and "另有 1 页中的图片超出 OCR 页数上限，未识别文字" in over.warnings


def _docx() -> bytes:
    from docx import Document
    from docx.shared import Cm

    doc = Document()
    doc.add_paragraph("一、营业执照")
    doc.add_paragraph().add_run().add_picture(str(IMAGE), width=Cm(14))
    doc.add_paragraph("二、印章").add_run().add_picture(io.BytesIO(_seal_png()), width=Cm(3))  # 小图不识别
    doc.add_paragraph("三、其他说明")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_inline_image_ocr_and_export_anchor():
    from app.export.docx_anchor import load_anchors
    from app.tasks.pipeline import _parse_bytes

    data = _docx()
    parsed, _ = _parse_bytes("docx", data, 1, 0)
    assert parsed.ocr_pages == 1 and docx_images.WARN_DONE.format(count=1) in parsed.warnings
    assert not any("嵌入对象" in w for w in parsed.warnings)
    locs = [n.loc for n in parsed.nodes]
    images = [n for n in parsed.nodes if n.loc.startswith("F1-IM1-")]
    assert images and all(n.part == "body" and n.page is None and n.bbox is None for n in images)
    assert "QT-2026-0508" in norm("".join(n.text for n in images))
    # 图片节点紧跟在图片前的段落之后、下一段之前
    assert locs.index(images[0].loc) == locs.index("F1-P1") + 1 and locs[-1] == "F1-P3"
    assert [n.ord for n in parsed.nodes] == list(range(1, len(locs) + 1))

    anchors = load_anchors(data, 1, {n.loc: n.text for n in parsed.nodes})
    paragraph = anchors.locate(images[0].loc, "QT-2026-0508").paragraph
    assert paragraph._p.xpath(".//a:blip")  # 落在图片所在段落，不报“与任务记录不一致”

    over, _ = _parse_bytes("docx", data, 1, 100)  # 名额用完：不识别、不失败
    assert over.ocr_pages == 0 and docx_images.WARN_OVER.format(count=1) in over.warnings


def test_docx_deferred_ord_falls_between_neighbours():
    data = _docx()
    parsed = docx_images.defer(data, parse_docx(data, 1), 1000)
    assert parsed.ocr_pending == [1] and docx_images.WARN_PENDING.format(count=1) in parsed.warnings
    ords = {n.loc: n.ord for n in parsed.nodes}
    [(no, nodes, confs)] = list(docx_images.ocr_pending(data, 1, [1]))
    assert no == 1 and nodes and confs
    assert all(ords["F1-P1"] < n.ord < ords["F1-P2"] for n in nodes)
