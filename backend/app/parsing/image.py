"""图片解析（M3-04，SPEC 8.5，设计 docs/design/M3.md 第五节）。

Pillow 打开 → EXIF 方向校正 → 转 RGB → 长边超过 MAX_OCR_SIDE 时等比缩小后识别，坐标换算回原图像素。
bbox 以 EXIF 校正后的原图像素为准。解析失败只抛出 ParseError，消息是固定的中文原因。
"""
import io
import logging
import time
import warnings

from PIL import Image, ImageOps

from app.parsing import ocr, ocr_table
from app.parsing.docx import manual_clause_no
from app.parsing.model import LOW_OCR_CONF, ParsedFile, ParsedNode, ParseError, is_app_watermark

PARSER_VERSION = "image-1"
MAX_OCR_SIDE = 4000  # 长边超过时先等比缩小再识别

MSG_UNREADABLE = "图片无法读取"
MSG_NO_TEXT = "未能从图片中识别出文字"
MSG_LOW_CONF = "图片 OCR 识别置信度低，请核对原件"

log = logging.getLogger(__name__)


def _open(data: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)  # 超过像素上限一律按失败处理
            img = Image.open(io.BytesIO(data))
            img.load()
            img = ImageOps.exif_transpose(img)
            return img.convert("RGB")
    except Exception:  # noqa: BLE001 —— 含 DecompressionBombError，不带异常消息
        raise ParseError(MSG_UNREADABLE) from None


def ocr_nodes(img: Image.Image, prefix: str, page: int | None) -> tuple[list[ParsedNode], list[float]]:
    """识别一张图片，返回 (节点, 各块与单元格的置信度)。loc 为 {prefix}-B{块} / {prefix}-T{表}-R{行}-C{列}，
    bbox 为原图像素；ord 按块顺序从 1 编号，由调用方重排。图片文件与 DOCX 内嵌图片（V35-C-01）共用。"""
    width, height = img.size
    ratio = 1.0
    work = img
    if max(width, height) > MAX_OCR_SIDE:
        ratio = MAX_OCR_SIDE / max(width, height)
        work = img.resize((max(1, round(width * ratio)), max(1, round(height * ratio))), Image.LANCZOS)
    lines = ocr.recognize(work)
    # 线框表格（V34-D-02）：在识别用的图上找，单元格框按原图像素
    tables, used = ocr_table.find_tables(work, lines, ratio)
    if ratio != 1.0:
        for ln in lines:
            ln.bbox = [round(v / ratio, 2) for v in ln.bbox]
    rest = ocr.clean_lines([ln for i, ln in enumerate(lines) if i not in used], LOW_OCR_CONF)
    items = [(t.top, "table", t) for t in tables]
    items += [(b[1][1], "block", b) for b in map(ocr.block_of, ocr.merge_lines(rest)) if not is_app_watermark(b[0])]
    items.sort(key=lambda it: it[0])

    nodes: list[ParsedNode] = []
    confs: list[float] = []
    block_no = table_no = 0
    for _, kind, content in items:
        if kind == "block":
            block_no += 1
            text, bbox, conf = content
            confs.append(conf)
            nodes.append(
                ParsedNode(
                    loc=f"{prefix}-B{block_no}", ord=len(nodes) + 1, kind="ocr_block", part="body", text=text,
                    clause_no=manual_clause_no(text), page=page, bbox=bbox, ocr_conf=conf,
                )
            )
            continue
        table_no += 1
        for cell in content.cells:
            confs.append(cell.conf)
            nodes.append(
                ParsedNode(
                    loc=f"{prefix}-T{table_no}-R{cell.row}-C{cell.col}", ord=len(nodes) + 1, kind="table_cell",
                    part="body", text=cell.text, page=page, bbox=cell.bbox, ocr_conf=cell.conf,
                )
            )
    return nodes, confs


def parse_image(data: bytes, file_seq: int) -> ParsedFile:
    img = _open(data)
    started = time.monotonic()
    try:
        nodes, confs = ocr_nodes(img, f"F{file_seq}-PG1", 1)
    except Exception:  # noqa: BLE001
        raise ParseError(MSG_UNREADABLE) from None
    log.info("image ocr: blocks=%d elapsed=%.1fs", len(nodes), time.monotonic() - started)
    if not nodes:
        raise ParseError(MSG_NO_TEXT)
    low = any(c < LOW_OCR_CONF for c in confs)
    return ParsedFile(nodes=nodes, page_count=1, has_revisions=False, warnings=[MSG_LOW_CONF] if low else [], ocr_pages=1)
