"""图片解析（M3-04）：manifest.yaml 中的预期事实、EXIF 方向、缩放、像素上限与损坏文件。"""
import io
import re
from pathlib import Path

import pytest
import yaml
from PIL import Image

from app.parsing import image as image_mod
from app.parsing import ocr as ocr_mod
from app.parsing.image import MSG_LOW_CONF, MSG_NO_TEXT, MSG_UNREADABLE, parse_image
from app.parsing.model import ParseError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "images"
MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))["samples"]


def data_of(name: str) -> bytes:
    return (FIXTURES / MANIFEST[name]["file"]).read_bytes()


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_clear_png():
    sample = MANIFEST["quotation_clear"]
    parsed = parse_image(data_of("quotation_clear"), 2)
    assert parsed.page_count == 1 and parsed.ocr_pages == 1 and parsed.warnings == sample["warnings"]
    assert [n.ord for n in parsed.nodes] == list(range(1, len(parsed.nodes) + 1))
    assert all(n.kind == "ocr_block" and n.part == "body" and n.page == 1 for n in parsed.nodes)
    assert all(re.fullmatch(r"F2-PG1-B\d+", n.loc) for n in parsed.nodes)
    assert all(n.ocr_conf >= 0.80 for n in parsed.nodes)
    full = norm("".join(n.text for n in parsed.nodes))
    for kw in sample["ocr_keywords"]:
        assert norm(kw) in full, kw
    w, h = sample["display_size"]
    assert all(0 <= n.bbox[0] < n.bbox[2] <= w and 0 <= n.bbox[1] < n.bbox[3] <= h for n in parsed.nodes)


def test_lowq_jpg_exif_rotated_and_low_conf_warning():
    sample = MANIFEST["quotation_lowq"]
    parsed = parse_image(data_of("quotation_lowq"), 1)
    assert parsed.warnings == [MSG_LOW_CONF] == sample["warnings"]
    assert any(n.ocr_conf < 0.80 for n in parsed.nodes)
    # bbox 以 EXIF 校正后的显示尺寸（500 x 700）为准，而不是存储尺寸（700 x 500）
    w, h = sample["display_size"]
    assert all(n.bbox[2] <= w + 1 and n.bbox[3] <= h + 1 for n in parsed.nodes)


def test_exif_orientation_applied_before_ocr(monkeypatch):
    sizes = []
    monkeypatch.setattr(ocr_mod, "recognize", lambda img: sizes.append(img.size) or [ocr_mod.OcrLine("文字", [1, 1, 20, 20], 0.9)])
    parse_image(data_of("quotation_lowq"), 1)
    assert sizes == [(500, 700)]


def test_large_image_scaled_down_and_bbox_restored(monkeypatch):
    seen = []

    def fake(img):
        seen.append(img.size)
        return [ocr_mod.OcrLine("缩放测试", [100, 200, 2000, 400], 0.9)]

    monkeypatch.setattr(ocr_mod, "recognize", fake)
    buf = io.BytesIO()
    Image.new("RGB", (8000, 4000), "white").save(buf, "PNG")
    parsed = parse_image(buf.getvalue(), 1)
    assert seen == [(4000, 2000)]  # 长边缩到 MAX_OCR_SIDE
    assert parsed.nodes[0].bbox == [200.0, 400.0, 4000.0, 800.0]  # 换算回原图像素


def test_no_text_and_unreadable(monkeypatch):
    monkeypatch.setattr(ocr_mod, "recognize", lambda img: [])
    with pytest.raises(ParseError) as e:
        parse_image(data_of("quotation_clear"), 1)
    assert str(e.value) == MSG_NO_TEXT
    for data in (b"", b"not an image secret-text", data_of("quotation_clear")[:200]):
        with pytest.raises(ParseError) as e:
            parse_image(data, 1)
        assert str(e.value) == MSG_UNREADABLE and "secret" not in str(e.value)


def test_pixel_limit_rejected(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, "PNG")  # 10000 像素，超过 1000 的上限（会先发警告）
    with pytest.raises(ParseError) as e:
        parse_image(buf.getvalue(), 1)
    assert str(e.value) == MSG_UNREADABLE


def test_log_has_no_text(monkeypatch, caplog):
    monkeypatch.setattr(ocr_mod, "recognize", lambda img: [ocr_mod.OcrLine("机密报价文字", [1, 1, 20, 20], 0.9)])
    with caplog.at_level("DEBUG"):
        parse_image(data_of("quotation_clear"), 1)
    assert "机密报价" not in caplog.text
    assert image_mod.PARSER_VERSION == "image-1"
