"""OCR 封装（M3-04）：merge_lines / block_of 的规则，以及真实 RapidOCR 引擎的复用与遥测关闭。"""
from pathlib import Path

import pytest
from PIL import Image

from app.parsing import ocr
from app.parsing.ocr import OcrLine, block_of, merge_lines

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "images"


def line(text, x0, top, x1, bottom, conf=0.9):
    return OcrLine(text, [x0, top, x1, bottom], conf)


def test_merge_adjacent_lines_into_one_block():
    blocks = merge_lines([line("合同总价为", 10, 10, 200, 30), line("六十万元整。", 10, 34, 200, 54)])
    assert len(blocks) == 1
    text, bbox, conf = block_of(blocks[0])
    assert text == "合同总价为六十万元整。"  # 两侧都是汉字，直接相连
    assert bbox == [10, 10, 200, 54]


def test_merge_latin_lines_joined_with_space():
    text, _, _ = block_of(merge_lines([line("Total price", 0, 0, 100, 20), line("USD 610,000", 0, 22, 100, 42)])[0])
    assert text == "Total price USD 610,000"


def test_big_gap_and_clause_number_start_new_block():
    blocks = merge_lines([line("甲", 0, 0, 100, 20), line("乙", 0, 100, 100, 120)])
    assert len(blocks) == 2
    blocks = merge_lines([line("货物明细见下表", 0, 0, 100, 20), line("第2条 价格与付款", 0, 22, 100, 42)])
    assert len(blocks) == 2


def test_same_row_segments_not_merged():
    blocks = merge_lines([line("卖方：甲", 0, 0, 100, 20), line("买方：乙", 300, 2, 400, 22)])
    assert len(blocks) == 2


def test_block_conf_is_mean_of_lines():
    _, _, conf = block_of([line("a", 0, 0, 1, 1, 0.9), line("b", 0, 2, 1, 3, 0.5)])
    assert conf == 0.7


def test_recognize_real_engine_reused_and_telemetry_disabled(monkeypatch):
    import onnxruntime

    calls = []
    monkeypatch.setattr(onnxruntime, "disable_telemetry_events", lambda: calls.append(1))
    monkeypatch.setattr(ocr, "_engine", None)
    img = Image.open(FIXTURES / "quotation_clear.png")
    lines = ocr.recognize(img)
    engine = ocr._engine
    assert engine is not None and calls == [1]  # 创建引擎前调用过一次
    assert lines and all(ln.text.strip() and ln.bbox[0] < ln.bbox[2] and ln.bbox[1] < ln.bbox[3] for ln in lines)
    assert all(0 <= ln.conf <= 1 for ln in lines)
    ocr.recognize(img)
    assert ocr._engine is engine and calls == [1]  # 复用，不重复创建
