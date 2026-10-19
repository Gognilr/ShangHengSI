"""PDF 解析（M3-03 文字页部分，SPEC 8.5，设计 docs/design/M3.md 第四节，open_questions Q-16、Q-61）。

文字页用 pdfplumber：表格单元格出 table_cell 节点，表格区域内的文字行不再重复出段落节点（Q-16），
其余文字行按纵向间距合并为文本块。bbox 为 [x0, top, x1, bottom]，PDF 点，页面显示方向，原点在左上角
（pdfplumber 的字符坐标已按 /Rotate 换算）。扫描页（M3-04）用 pypdfium2 以 200 DPI 渲染（渲染结果已含 /Rotate），
交给 ocr.recognize，行坐标除以渲染比例换算回 PDF 点，再按相同规则合并为块。
解析失败只抛出 ParseError，消息是固定的中文原因，不含原文和异常消息。
"""
import io
import logging
import re
import time
from dataclasses import dataclass

import pdfplumber

from app.parsing import ocr
from app.parsing.docx import manual_clause_no
from app.parsing.model import LOW_OCR_CONF, MAX_PDF_PAGES, ParsedFile, ParsedNode, ParseError

PARSER_VERSION = "pdf-1"
TEXT_PAGE_MIN_CHARS = 20  # SPEC 8.5，去掉空白后的字符数

MSG_CORRUPT = "PDF 文件无法读取，可能已损坏或已加密"
MSG_TOO_MANY_PAGES = "PDF 超过 200 页"
MSG_NO_TEXT = "未能从文件中识别出文字"

OCR_DPI = 200
MAX_OCR_SIDE = 4000  # 渲染后长边上限（像素），超大页面降低渲染比例
MAX_LOW_CONF_PAGES = 10  # 低置信警告最多逐页列出的页数

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")


@dataclass
class PdfInfo:
    page_count: int
    scanned_pages: list[int]  # 需要 OCR 的页码（从 1 开始）


def _open(data: bytes):
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
        n = len(pdf.pages)
    except Exception:  # noqa: BLE001 —— 任何读取失败（含需要密码）都只给固定提示，不带异常消息
        raise ParseError(MSG_CORRUPT) from None
    if n > MAX_PDF_PAGES:
        pdf.close()
        raise ParseError(MSG_TOO_MANY_PAGES)
    if n == 0:
        pdf.close()
        raise ParseError(MSG_CORRUPT)
    return pdf


def _dedupe(page):
    try:
        return page.dedupe_chars()
    except Exception:  # noqa: BLE001
        raise ParseError(MSG_CORRUPT) from None


def _is_scanned(page) -> bool:
    return len(_WS.sub("", page.extract_text() or "")) < TEXT_PAGE_MIN_CHARS


def inspect_pdf(data: bytes) -> PdfInfo:
    """页数与需要 OCR 的页码。"""
    with _open(data) as pdf:
        try:
            scanned = [i for i, p in enumerate(pdf.pages, start=1) if _is_scanned(_dedupe(p))]
        except ParseError:
            raise
        except Exception:  # noqa: BLE001
            raise ParseError(MSG_CORRUPT) from None
        return PdfInfo(page_count=len(pdf.pages), scanned_pages=scanned)


_join = ocr.join_text


def _in_bbox(line: dict, bbox) -> bool:
    cx, cy = (line["x0"] + line["x1"]) / 2, (line["top"] + line["bottom"]) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def _merge_blocks(lines: list[dict]) -> list[tuple[str, list[float]]]:
    """文本行合并为块：行间距 ≤ max(3, 0.6 × 上一行高度)，且下一行不以条款编号开头。"""
    blocks: list[list[dict]] = []
    for ln in sorted(lines, key=lambda d: (d["top"], d["x0"])):
        if blocks:
            prev = blocks[-1][-1]
            gap = ln["top"] - prev["bottom"]
            if gap <= max(3.0, 0.6 * (prev["bottom"] - prev["top"])) and manual_clause_no(ln["text"].strip()) is None:
                blocks[-1].append(ln)
                continue
        blocks.append([ln])
    out = []
    for blk in blocks:
        text = _join([b["text"].strip() for b in blk])
        bbox = [
            round(min(b["x0"] for b in blk), 2), round(min(b["top"] for b in blk), 2),
            round(max(b["x1"] for b in blk), 2), round(max(b["bottom"] for b in blk), 2),
        ]
        out.append((text, bbox))
    return out


def _parse_text_page(page, page_no: int, file_seq: int, nodes: list[ParsedNode]):
    tables = page.find_tables()
    items: list[tuple[float, str, object]] = []  # (top, 类型, 内容)

    for t in tables:
        items.append((t.bbox[1], "table", t))
    table_boxes = [t.bbox for t in tables]
    lines = [ln for ln in page.extract_text_lines() if ln["text"].strip() and not any(_in_bbox(ln, b) for b in table_boxes)]
    for text, bbox in _merge_blocks(lines):
        items.append((bbox[1], "block", (text, bbox)))

    items.sort(key=lambda it: it[0])
    block_no = table_no = 0
    for _, kind, content in items:
        if kind == "block":
            block_no += 1
            text, bbox = content
            nodes.append(
                ParsedNode(
                    loc=f"F{file_seq}-PG{page_no}-B{block_no}", ord=len(nodes) + 1, kind="paragraph", part="body",
                    text=text, clause_no=manual_clause_no(text), page=page_no, bbox=bbox,
                )
            )
        else:
            table_no += 1
            rows = content.extract()
            for i, row in enumerate(content.rows):
                for j, cell_bbox in enumerate(row.cells):
                    if cell_bbox is None:
                        continue
                    text = _WS.sub(" ", (rows[i][j] or "")).strip()
                    if not text:
                        continue
                    nodes.append(
                        ParsedNode(
                            loc=f"F{file_seq}-PG{page_no}-T{table_no}-R{i + 1}-C{j + 1}", ord=len(nodes) + 1,
                            kind="table_cell", part="body", text=text, page=page_no,
                            bbox=[round(cell_bbox[0], 2), round(cell_bbox[1], 2), round(cell_bbox[2], 2), round(cell_bbox[3], 2)],
                        )
                    )


def _render_page(pdfium_doc, page_no: int):
    """渲染第 page_no 页，返回 (PIL 图片, 渲染比例)。pdfium 渲染结果已按 /Rotate 旋转到显示方向。"""
    page = pdfium_doc[page_no - 1]
    try:
        w, h = page.get_size()  # 显示方向的 PDF 点
        scale = min(OCR_DPI / 72, MAX_OCR_SIDE / max(w, h, 1.0))
        return page.render(scale=scale).to_pil().convert("RGB"), scale
    finally:
        page.close()


def _parse_scanned_page(pdfium_doc, page_no: int, file_seq: int, nodes: list[ParsedNode]) -> list[float]:
    """OCR 一页，追加 ocr_block 节点，返回各块的置信度。"""
    img, scale = _render_page(pdfium_doc, page_no)
    lines = ocr.recognize(img)
    for ln in lines:  # 像素 → PDF 点
        ln.bbox = [v / scale for v in ln.bbox]
    confs: list[float] = []
    for no, block in enumerate(ocr.merge_lines(lines), start=1):
        text, bbox, conf = ocr.block_of(block)
        confs.append(conf)
        nodes.append(
            ParsedNode(
                loc=f"F{file_seq}-PG{page_no}-B{no}", ord=len(nodes) + 1, kind="ocr_block", part="body", text=text,
                clause_no=manual_clause_no(text), page=page_no, bbox=bbox, ocr_conf=conf,
            )
        )
    return confs


def parse_pdf(data: bytes, file_seq: int) -> ParsedFile:
    nodes: list[ParsedNode] = []
    scanned: list[int] = []
    low_conf: list[int] = []
    empty: list[int] = []
    started = time.monotonic()
    with _open(data) as pdf:
        page_count = len(pdf.pages)
        pdfium_doc = None
        try:
            for page_no, raw in enumerate(pdf.pages, start=1):
                page = _dedupe(raw)
                if not _is_scanned(page):
                    _parse_text_page(page, page_no, file_seq, nodes)
                    continue
                if pdfium_doc is None:
                    import pypdfium2

                    pdfium_doc = pypdfium2.PdfDocument(data)
                scanned.append(page_no)
                confs = _parse_scanned_page(pdfium_doc, page_no, file_seq, nodes)
                if not confs:
                    empty.append(page_no)
                elif any(c < LOW_OCR_CONF for c in confs):
                    low_conf.append(page_no)
        except ParseError:
            raise
        except Exception:  # noqa: BLE001
            raise ParseError(MSG_CORRUPT) from None
        finally:
            if pdfium_doc is not None:
                pdfium_doc.close()
    if scanned:
        log.info("pdf ocr: pages=%d blocks=%d elapsed=%.1fs", len(scanned), len(nodes), time.monotonic() - started)
    if not nodes:
        raise ParseError(MSG_NO_TEXT)

    warnings: list[str] = []
    if scanned:
        warnings.append(f"第 {'、'.join(str(p) for p in scanned)} 页为扫描页，已用 OCR 识别")
    for p in low_conf[:MAX_LOW_CONF_PAGES]:
        warnings.append(f"第 {p} 页 OCR 识别置信度低，请核对原件")
    if len(low_conf) > MAX_LOW_CONF_PAGES:
        warnings.append(f"等 {len(low_conf) - MAX_LOW_CONF_PAGES} 页 OCR 识别置信度低，请核对原件")
    for p in empty:
        warnings.append(f"第 {p} 页未识别到文字")
    return ParsedFile(nodes=nodes, page_count=page_count, has_revisions=False, warnings=warnings, ocr_pages=len(scanned))
