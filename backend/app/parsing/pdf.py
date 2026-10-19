"""PDF 解析（M3-03 文字页部分，SPEC 8.5，设计 docs/design/M3.md 第四节，open_questions Q-16、Q-61）。

文字页用 pdfplumber：表格单元格出 table_cell 节点，表格区域内的文字行不再重复出段落节点（Q-16），
其余文字行按纵向间距合并为文本块。bbox 为 [x0, top, x1, bottom]，PDF 点，页面显示方向，原点在左上角
（pdfplumber 的字符坐标已按 /Rotate 换算）。扫描页的 OCR 在 M3-04 实现，这里先抛出 ParseError 占位。
解析失败只抛出 ParseError，消息是固定的中文原因，不含原文和异常消息。
"""
import io
import re
from dataclasses import dataclass

import pdfplumber

from app.parsing.docx import manual_clause_no
from app.parsing.model import MAX_PDF_PAGES, ParsedFile, ParsedNode, ParseError

PARSER_VERSION = "pdf-1"
TEXT_PAGE_MIN_CHARS = 20  # SPEC 8.5，去掉空白后的字符数

MSG_CORRUPT = "PDF 文件无法读取，可能已损坏或已加密"
MSG_TOO_MANY_PAGES = "PDF 超过 200 页"
MSG_NO_TEXT = "未能从文件中识别出文字"
MSG_SCAN_PENDING = "扫描页的文字识别尚未启用"  # M3-04 实现 OCR 后删除

_CJK_END = re.compile(r"[一-鿿，。；：、？！（）“”‘’《》]")
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


def _join(lines: list[str]) -> str:
    """两侧都是汉字或中文标点时直接相连，否则以一个空格相连。"""
    out = lines[0]
    for nxt in lines[1:]:
        out += ("" if _CJK_END.match(out[-1]) and _CJK_END.match(nxt[0]) else " ") + nxt
    return out


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


def parse_pdf(data: bytes, file_seq: int) -> ParsedFile:
    nodes: list[ParsedNode] = []
    with _open(data) as pdf:
        page_count = len(pdf.pages)
        try:
            for page_no, raw in enumerate(pdf.pages, start=1):
                page = _dedupe(raw)
                if _is_scanned(page):
                    raise ParseError(MSG_SCAN_PENDING)
                _parse_text_page(page, page_no, file_seq, nodes)
        except ParseError:
            raise
        except Exception:  # noqa: BLE001
            raise ParseError(MSG_CORRUPT) from None
    if not nodes:
        raise ParseError(MSG_NO_TEXT)
    return ParsedFile(nodes=nodes, page_count=page_count, has_revisions=False, warnings=[])
