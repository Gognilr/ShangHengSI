"""V3-C-02：原件定位与拆分；样本全部虚构。"""
from pathlib import Path

import pytest

from app.export.docx_anchor import AnchorMismatch, load_anchors, split_runs
from app.parsing.docx import parse_docx
from app.parsing.model import ParseError

FIXTURES = Path(__file__).parent / "fixtures"
DATA = FIXTURES / "annotate" / "anchors.docx"


def anchors():
    data = DATA.read_bytes()
    stored = {n.loc: n.text for n in parse_docx(data, 1).nodes}
    return load_anchors(data, 1, stored)


def test_cross_run_quote_preserves_text_and_format():
    a = anchors().locate("F1-P1", "按时支付")
    assert a.precise
    before = a.paragraph.text
    runs = split_runs(a)
    assert "".join(r.text for r in runs) == "按时支付"
    assert a.paragraph.text == before
    assert runs[0].bold and runs[1].italic
    # 拆分后的 run 可直接用于后续批注 API；原件字节仍保持不变。
    items = anchors()
    original = DATA.read_bytes()
    items.document.add_comment(split_runs(items.locate("F1-P1", "按时支付")), "虚构批注", author="商衡SI")
    assert DATA.read_bytes() == original
    # 一个 run 内的两个切点也能准确覆盖。
    a = anchors().locate("F1-P1", "应当")
    assert "".join(r.text for r in split_runs(a)) == "应当"


def test_cell_second_paragraph():
    a = anchors().locate("F1-T1-R1-C1", "验收期限")
    assert a.precise and a.paragraph.text == "验收期限为十日。"
    assert "".join(r.text for r in split_runs(a)) == "验收期限"


def test_duplicate_quote_falls_back():
    a = anchors().locate("F1-P2", "及时通知")
    assert not a.precise and a.paragraph.text == "及时通知，及时通知。"


def test_unsupported_structure_and_boundaries_fall_back():
    items = anchors()
    for loc, quote in (("F1-P3", "虚构附件"), ("F1-P6", "域结果"),
                       ("F1-P7", "修订文字"), ("F1-P7", "直接文字"),
                       ("F1-P8", "边界前"), ("F1-P8", "前后")):
        assert not items.locate(loc, quote).precise


def test_style_numbering_body_text():
    a = anchors().locate("F1-P4", "协商解决")
    assert a.precise
    assert "".join(r.text for r in split_runs(a)) == "协商解决"


def test_nonbody_missing_loc_and_empty_quote():
    items = anchors()
    for loc in ("F1-H1", "F1-P999"):
        a = items.locate(loc, "虚构合同页眉")
        assert not a.precise and a.paragraph.text == "甲方应当按时支付合同价款。"
    a = items.locate("F1-T1-R1-C1", None)
    assert not a.precise and a.paragraph.text == "交付地点另行约定。"
    # 段落标记已删除的两段都能追踪；不跨原 XML 段落匹配。
    assert items.locate("F1-P5", "合并前段").precise
    assert items.locate("F1-P5", "合并后段").precise
    assert not items.locate("F1-P5", "前段合并后").precise


def test_stored_text_mismatch():
    data = DATA.read_bytes()
    stored = {n.loc: n.text for n in parse_docx(data, 1).nodes}
    stored["F1-P1"] = stored["F1-P1"].replace("甲", "乙")
    with pytest.raises(AnchorMismatch, match="原件解析结果与任务记录不一致，无法生成批注版"):
        load_anchors(data, 1, stored)
    stored.pop("F1-P1")
    with pytest.raises(AnchorMismatch):
        load_anchors(data, 1, stored)
    stored = {n.loc: n.text for n in parse_docx(data, 1).nodes}
    stored["F1-P999"] = "多余记录"
    with pytest.raises(AnchorMismatch):
        load_anchors(data, 1, stored)


@pytest.mark.parametrize("path", sorted(FIXTURES.rglob("*.docx")), ids=lambda p: str(p.relative_to(FIXTURES)))
def test_tracking_preserves_existing_parse(path):
    data = path.read_bytes()
    try:
        normal = parse_docx(data, 3)
    except ParseError as error:
        # 既有加密/损坏样本保持原错误，不因追踪改变解析行为。
        with pytest.raises(ParseError, match=str(error)):
            parse_docx(data, 3, track=True)
        return
    tracked = parse_docx(data, 3, track=True)
    assert tracked.nodes == normal.nodes
    assert (tracked.warnings, tracked.has_revisions, tracked.page_count, tracked.ocr_pages) == (
        normal.warnings, normal.has_revisions, normal.page_count, normal.ocr_pages)
    assert set(tracked.node_paragraphs) == {n.loc for n in normal.nodes}
    assert all(bool(tracked.node_paragraphs[n.loc]) == (n.part == "body") for n in normal.nodes)
