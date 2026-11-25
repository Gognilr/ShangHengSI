"""PDF 解析（M3-03 文字页部分，SPEC 8.5，设计 docs/design/M3.md 第四节，open_questions Q-16、Q-61）。

文字页用 pdfplumber：表格单元格出 table_cell 节点，表格区域内的文字行不再重复出段落节点（Q-16），
其余文字行按纵向间距合并为文本块。bbox 为 [x0, top, x1, bottom]，PDF 点，页面显示方向，原点在左上角
（pdfplumber 的字符坐标已按 /Rotate 换算）。扫描页（M3-04）用 pypdfium2 以 200 DPI 渲染（渲染结果已含 /Rotate），
交给 ocr.recognize，行坐标除以渲染比例换算回 PDF 点，再按相同规则合并为块；线框表格转为 table_cell（ocr_table，V34-D-02）。
文字页上没有矢量文字覆盖的独立大图（回单截图、执照、证书扫描，V35-C-01）只裁该区域 OCR，同样计 1 页 OCR。
解析失败只抛出 ParseError，消息是固定的中文原因，不含原文和异常消息。
"""
import io
import logging
import math
import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from app.parsing import ocr, ocr_table
from app.parsing.docx import manual_clause_no
from app.parsing.model import LOW_OCR_CONF, MAX_PDF_PAGES, ParsedFile, ParsedNode, ParseError
from app.redaction import tables as table_tools

PARSER_VERSION = "pdf-1"
TEXT_PAGE_MIN_CHARS = 20  # SPEC 8.5，去掉空白后的字符数

MSG_CORRUPT = "PDF 文件无法读取，可能已损坏或已加密"
MSG_TOO_MANY_PAGES = "PDF 超过 200 页"
MSG_NO_TEXT = "未能从文件中识别出文字"
MSG_PASSWORD = "此 PDF 需要打开密码，请输入投标方提供的密码"


class PdfPasswordError(ParseError):
    """PDF 需要密码或密码不正确，不带出第三方异常内容。"""

OCR_DPI = 200
MAX_OCR_SIDE = 4000  # 渲染后长边上限（像素），超大页面降低渲染比例
MAX_LOW_CONF_PAGES = 10  # 低置信警告最多逐页列出的页数

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")


@dataclass
class PdfInfo:
    page_count: int
    scanned_pages: list[int]  # 需要 OCR 的页码（从 1 开始），含只识别图片区域的页
    region_pages: list[int] = field(default_factory=list)  # 其中只识别图片区域的页（V35-C-01）：名额不足时不识别、不失败


def _open(data: bytes, password: str | None = None, max_pages: int | None = None):
    pdf = None
    try:
        pdf = pdfplumber.open(io.BytesIO(data), password=password)
        n = len(pdf.pages)
    except Exception as exc:  # noqa: BLE001 —— 任何读取失败都只给固定提示，不带异常消息
        if pdf is not None:
            pdf.close()
        # pdfplumber 把 pdfminer 的异常包成 PdfminerException，原异常在 args[0]
        wrong = isinstance(exc, PDFPasswordIncorrect) or any(isinstance(a, PDFPasswordIncorrect) for a in exc.args)
        if wrong and password is not None:
            # 既有审核 / 会话不传密码，仍沿用原提示；标书显式传空串检测加密。
            raise PdfPasswordError(MSG_PASSWORD) from None
        raise ParseError(MSG_CORRUPT) from None
    limit = MAX_PDF_PAGES if max_pages is None else max_pages
    if n > limit:
        pdf.close()
        raise ParseError(MSG_TOO_MANY_PAGES if max_pages is None else f"PDF 超过 {limit} 页")
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


# 独立大图（V35-C-01，按两组公开标书定）：合并相互重叠的图片后，面积 ≥ 页面 4%、短边 ≥ 90 点、长边 ≥ 140 点
# （约 3.2 × 4.9 厘米：证书缩略图以上；印章、签名、窄条页眉图不到），且框内矢量文字不超过 5 个字
REGION_MIN_AREA = 0.04
REGION_MIN_SIDE = 90.0
REGION_MIN_LONG = 140.0
REGION_MAX_CHARS = 5


def _image_regions(page) -> list[tuple[float, float, float, float]]:
    """文字页上需要局部 OCR 的图片区域（PDF 点，页面显示方向）。"""
    boxes = [[max(0, im['x0']), max(0, im['top']), min(page.width, im['x1']), min(page.height, im['bottom'])]
             for im in page.images]
    boxes = [b for b in boxes if b[0] < b[2] and b[1] < b[3]]
    merged: list[list[float]] = []
    for b in sorted(boxes):  # 切成多条的图片、叠放的图片合为一个区域
        for m in merged:
            if b[0] <= m[2] + 2 and b[2] >= m[0] - 2 and b[1] <= m[3] + 2 and b[3] >= m[1] - 2:
                m[:] = [min(m[0], b[0]), min(m[1], b[1]), max(m[2], b[2]), max(m[3], b[3])]
                break
        else:
            merged.append(list(b))
    page_area = float(page.width * page.height)
    out = []
    for x0, top, x1, bottom in merged:
        w, h = x1 - x0, bottom - top
        if w * h < page_area * REGION_MIN_AREA or min(w, h) < REGION_MIN_SIDE or max(w, h) < REGION_MIN_LONG:
            continue
        inside = sum(1 for c in page.chars if c["text"].strip()
                     and x0 <= (c["x0"] + c["x1"]) / 2 <= x1 and top <= (c["top"] + c["bottom"]) / 2 <= bottom)
        if inside <= REGION_MAX_CHARS:
            out.append((x0, top, x1, bottom))
    return out


def _is_mixed(page) -> bool:
    if len(_WS.sub("", page.extract_text() or "")) >= 200:
        return False
    # 图片先裁到页面，再按横向条带求并集面积，重叠图片不重复计数。
    boxes = [(max(0, im['x0']), max(0, im['top']), min(page.width, im['x1']),
              min(page.height, im['bottom'])) for im in page.images]
    boxes = [b for b in boxes if b[0] < b[2] and b[1] < b[3]]
    xs = sorted({x for b in boxes for x in (b[0], b[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((b[1], b[3]) for b in boxes if b[0] < right and b[2] > left)
        covered, end = 0.0, 0.0
        for top, bottom in intervals:
            covered += max(0, bottom - max(top, end))
            end = max(end, bottom)
        area += (right - left) * covered
    return area >= page.width * page.height * 0.5


def inspect_pdf(data: bytes, *, password: str | None = None, max_pages: int | None = None) -> PdfInfo:
    """页数与需要 OCR 的页码。"""
    with _open(data, password, max_pages) as pdf:
        try:
            scanned, regions = [], []
            for i, raw in enumerate(pdf.pages, start=1):
                page = _dedupe(raw)
                if _is_scanned(page) or _is_mixed(page):
                    scanned.append(i)
                elif _image_regions(page):
                    scanned.append(i)
                    regions.append(i)
        except ParseError:
            raise
        except Exception:  # noqa: BLE001
            raise ParseError(MSG_CORRUPT) from None
        return PdfInfo(page_count=len(pdf.pages), scanned_pages=scanned, region_pages=regions)


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


_TEXT_STRATEGY = {"vertical_strategy": "text", "horizontal_strategy": "text"}


def _text_tables(page) -> list:
    """受限 text 兜底（V34-D-03）：无线框的报价表。只取表头行（前 5 行内，含单价 / 数量 / 金额 / 合计中的两类）到数据结束的行，
    且至少 3 列各有 2 个以上纯数字单元格；不满足的整张丢弃，避免把普通正文切成单元格。"""
    try:
        found = page.find_tables(_TEXT_STRATEGY)
    except Exception:  # noqa: BLE001 —— 兜底失败不影响原有解析
        return []
    out = []
    for t in found:
        texts = [[_WS.sub(" ", c or "").strip() for c in row] for row in t.extract()]
        cells = [row.cells for row in t.rows]
        header = next((i for i, row in enumerate(texts[:5])
                       if len({k for c in row if c and (k := table_tools.header_kind(c))}) >= 2
                       and not any(table_tools._numeric_cell(c) for c in row if c)), None)
        if header is None:
            continue
        end, sparse = header, 0
        for i in range(header + 1, len(texts)):
            if sum(1 for c in texts[i] if c) >= 2:
                end, sparse = i, 0
            elif (sparse := sparse + 1) >= 2:  # 连续两行只有一格（正文）即表格结束；单行可能是名称换行
                break
        body = texts[header + 1:end + 1]
        width = max((len(r) for r in body), default=0)
        numeric_cols = sum(1 for j in range(width)
                           if sum(1 for r in body if j < len(r) and r[j] and table_tools._numeric_cell(r[j])) >= 2)
        if numeric_cols < 3:
            continue
        keep = range(header, end + 1)
        boxes = [b for i in keep for b in cells[i] if b is not None]
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        rows = [texts[i] for i in keep]
        out.append(SimpleNamespace(bbox=bbox, extract=lambda rows=rows: rows,
                                   rows=[SimpleNamespace(cells=cells[i]) for i in keep]))
    return out


def _parse_text_page(page, page_no: int, file_seq: int, nodes: list[ParsedNode]):
    tables = page.find_tables() or _text_tables(page)
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


RUNNING_BAND = 0.08  # 页眉 / 页脚带：页顶、页底各 8%
_DIGITS = re.compile(r"[0-9０-９]+")


def _mark_running(nodes: list[ParsedNode], heights: dict[int, float]) -> None:
    """页眉页脚（V34-D-03）：完全位于页顶 / 页底带内的文本块（文字页段落与 OCR 块），去空白、数字归一化（兼容页码）后
    在至少 3 页且至少半数页重复出现，标为 part=header / footer（与 DOCX 一致，不参与外发与 AI 审核）。
    表格单元格不参与：文字页表格区域内的行本来就不出块，OCR 表格内的行已归入单元格。"""
    seen: dict[tuple[str, str], dict[int, list[ParsedNode]]] = {}
    for n in nodes:
        h = heights.get(n.page or 0)
        if n.kind not in ("paragraph", "ocr_block") or not n.bbox or not h:
            continue
        band = "header" if n.bbox[3] <= h * RUNNING_BAND else "footer" if n.bbox[1] >= h * (1 - RUNNING_BAND) else None
        key = _DIGITS.sub("#", _WS.sub("", n.text))
        if not band and n.kind == "ocr_block" and len(key) <= 12 and not re.search(r"[：:]", key):
            band = "noise"  # 扫描件里页面中部重复的短行（软件水印、角标，问题 14）
        if band and key:
            seen.setdefault((band, key), {}).setdefault(n.page, []).append(n)
    need = max(3, math.ceil(len(heights) / 2))
    for (band, _key), pages in seen.items():
        if band == "noise":
            if len(pages) < max(3, math.ceil(len(heights) * 0.8)):
                continue
            band = "header"
        if len(pages) >= need:
            for found in pages.values():
                for n in found:
                    n.part = band


def _continued_warnings(nodes: list[ParsedNode]) -> list[str]:
    out = []
    for group in table_tools.continued_tables(nodes):
        pages = [int(re.search(r"-PG(\d+)-", name)[1]) for name, _skip in group]
        out.append(f"第 {pages[0]}–{pages[-1]} 页表格按续表合并核对")
    return out


def _render_page(pdfium_doc, page_no: int):
    """渲染第 page_no 页，返回 (PIL 图片, 渲染比例)。pdfium 渲染结果已按 /Rotate 旋转到显示方向。"""
    page = pdfium_doc[page_no - 1]
    try:
        w, h = page.get_size()  # 显示方向的 PDF 点
        scale = min(OCR_DPI / 72, MAX_OCR_SIDE / max(w, h, 1.0))
        return page.render(scale=scale).to_pil().convert("RGB"), scale
    finally:
        page.close()


def _crop_lines(img, scale: float, regions):
    """只识别图片区域（V35-C-01）：逐块裁剪识别，行坐标移回整页像素；返回 (只留区域的白底整页图, 行)。
    区域外涂白，线框表格检测不会把页面上的矢量表格线当成网格。"""
    from PIL import Image

    canvas = Image.new("RGB", img.size, "white")
    lines = []
    for x0, top, x1, bottom in regions:
        box = (max(0, int(x0 * scale)), max(0, int(top * scale)),
               min(img.width, math.ceil(x1 * scale)), min(img.height, math.ceil(bottom * scale)))
        crop = img.crop(box)
        canvas.paste(crop, box[:2])
        for ln in ocr.recognize(crop):
            ln.bbox = [ln.bbox[0] + box[0], ln.bbox[1] + box[1], ln.bbox[2] + box[0], ln.bbox[3] + box[1]]
            lines.append(ln)
    return canvas, lines


def _parse_scanned_page(pdfium_doc, page_no: int, file_seq: int, nodes: list[ParsedNode], regions=None) -> list[float]:
    """OCR 一页，追加 ocr_block 与线框表格的 table_cell 节点（V34-D-02），返回各块、各单元格的置信度。
    regions 非空时只识别这些图片区域：区域内几乎没有矢量文字，不再按已有节点去重（图片常放在表格单元格里）。"""
    img, scale = _render_page(pdfium_doc, page_no)
    existing = [n for n in nodes if n.page == page_no]
    boxes = [] if regions else [n.bbox for n in existing if n.bbox]

    def overlaps(bbox):
        return any(bbox[0] < b[2] and bbox[2] > b[0] and bbox[1] < b[3] and bbox[3] > b[1] for b in boxes)

    if regions:
        img, lines = _crop_lines(img, scale, regions)
    else:
        lines = ocr.recognize(img)
    # 合并前先去掉已有文字的 OCR 行，避免页眉与紧邻的图片正文合并后整块被丢弃。
    lines = ocr.clean_lines([ln for ln in lines if not overlaps([v / scale for v in ln.bbox])], LOW_OCR_CONF)
    # 线框表格（V34-D-02）在像素图上找，单元格框直接换算为 PDF 点；其余行照旧合并为块。
    tables, used = ocr_table.find_tables(img, lines, scale)
    for ln in lines:  # 像素 → PDF 点
        ln.bbox = [v / scale for v in ln.bbox]
    rest = [ln for i, ln in enumerate(lines) if i not in used]
    first_no = max((int(n.loc.rsplit('-B', 1)[1]) for n in existing if re.search(r'-B\d+$', n.loc)), default=0) + 1
    table_no = max((int(m[1]) for n in existing if (m := re.search(r'-T(\d+)-R\d+-C\d+$', n.loc))), default=0)
    items = [(t.top, "table", t) for t in tables]
    for block in ocr.merge_lines(rest):
        text, bbox, conf = ocr.block_of(block)
        if not overlaps(bbox):
            items.append((bbox[1], "block", (text, bbox, conf)))
    items.sort(key=lambda it: it[0])
    confs: list[float] = []
    block_no = first_no - 1
    for _, kind, content in items:
        if kind == "block":
            block_no += 1
            text, bbox, conf = content
            confs.append(conf)
            nodes.append(
                ParsedNode(
                    loc=f"F{file_seq}-PG{page_no}-B{block_no}", ord=len(nodes) + 1, kind="ocr_block", part="body", text=text,
                    clause_no=manual_clause_no(text), page=page_no, bbox=bbox, ocr_conf=conf,
                )
            )
            continue
        table_no += 1
        for cell in content.cells:
            confs.append(cell.conf)
            nodes.append(
                ParsedNode(
                    loc=f"F{file_seq}-PG{page_no}-T{table_no}-R{cell.row}-C{cell.col}", ord=len(nodes) + 1,
                    kind="table_cell", part="body", text=cell.text, page=page_no, bbox=cell.bbox, ocr_conf=cell.conf,
                )
            )
    return confs


def ocr_pages(data: bytes, file_seq: int, pages: list[int], existing: list[ParsedNode], *, password: str | None = None):
    """延后识别（标书投标文件）：逐页 OCR，产出 (页码, 新节点, 置信度或 None)。existing 为该文件已有的文字节点，用于去掉已有文字的区域。"""
    import pypdfium2

    doc = pypdfium2.PdfDocument(data, password=password)
    try:
        with _open(data, password, max_pages=len(doc)) as plumber:
            for page_no in pages:
                page = _dedupe(plumber.pages[page_no - 1])
                regions = None if _is_scanned(page) or _is_mixed(page) else _image_regions(page)
                nodes = [n for n in existing if n.page == page_no]
                before = len(nodes)
                confs = _parse_scanned_page(doc, page_no, file_seq, nodes, regions)
                # 只识别图片区域的页没识别到文字（照片）不算空白页：置信度给 None
                yield page_no, nodes[before:], None if regions and not confs else confs
    finally:
        doc.close()


def parse_pdf(data: bytes, file_seq: int, on_ocr=None, *, password: str | None = None,
              max_pages: int | None = None, defer_ocr: bool = False, region_budget: int | None = None) -> ParsedFile:
    """defer_ocr：扫描页与图文混排页不在此识别，页码放进 ocr_pending，由调用方随后用 ocr_pages 补上。
    region_budget：只识别图片区域的页最多识别几页（剩余 OCR 名额），超出的只提示；None 不限。"""
    nodes: list[ParsedNode] = []
    pending: list[int] = []
    scanned: list[int] = []
    mixed: list[int] = []
    low_conf: list[int] = []
    empty: list[int] = []
    over: list[int] = []
    heights: dict[int, float] = {}
    started = time.monotonic()
    with _open(data, password, max_pages) as pdf:
        page_count = len(pdf.pages)
        pdfium_doc = None
        try:
            for page_no, raw in enumerate(pdf.pages, start=1):
                page = _dedupe(raw)
                heights[page_no] = float(page.height)
                is_scanned = _is_scanned(page)
                regions = None
                if not is_scanned:
                    _parse_text_page(page, page_no, file_seq, nodes)
                    if not _is_mixed(page) and not (regions := _image_regions(page)):
                        continue
                    if regions and region_budget is not None:
                        if region_budget <= 0:
                            over.append(page_no)
                            continue
                        region_budget -= 1
                if defer_ocr:
                    pending.append(page_no)
                    continue
                if pdfium_doc is None:
                    import pypdfium2

                    pdfium_doc = pypdfium2.PdfDocument(data, password=password)
                (scanned if is_scanned else mixed).append(page_no)
                confs = _parse_scanned_page(pdfium_doc, page_no, file_seq, nodes, regions)
                if on_ocr is not None:
                    on_ocr(len(scanned) + len(mixed))  # 已 OCR 的页数（V321-E-06）
                if not confs:
                    if not regions:  # 只识别图片区域的页没识别到文字（照片）不算空白页
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
    if scanned or mixed:
        log.info("pdf ocr: pages=%d blocks=%d elapsed=%.1fs", len(scanned) + len(mixed), len(nodes), time.monotonic() - started)
    if not nodes and not pending:
        raise ParseError(MSG_NO_TEXT)
    _mark_running(nodes, heights)

    warnings: list[str] = []
    if pending:
        warnings.append(f"{len(pending)} 页为扫描页或图文混排，正在后台识别")
    if scanned:
        warnings.append(f"第 {'、'.join(str(p) for p in scanned)} 页为扫描页，已用 OCR 识别")
    for p in mixed:
        warnings.append(f"第 {p} 页为图文混排，已对图片部分做 OCR")
    for p in low_conf[:MAX_LOW_CONF_PAGES]:
        warnings.append(f"第 {p} 页 OCR 识别置信度低，请核对原件")
    if len(low_conf) > MAX_LOW_CONF_PAGES:
        warnings.append(f"等 {len(low_conf) - MAX_LOW_CONF_PAGES} 页 OCR 识别置信度低，请核对原件")
    for p in empty:
        warnings.append(f"第 {p} 页未识别到文字")
    if over:
        warnings.append(f"另有 {len(over)} 页中的图片超出 OCR 页数上限，未识别文字")
    warnings += _continued_warnings(nodes)
    return ParsedFile(nodes=nodes, page_count=page_count, has_revisions=False, warnings=warnings, ocr_pages=len(scanned) + len(mixed),
                      ocr_pending=pending)
