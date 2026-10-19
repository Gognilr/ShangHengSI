"""图片解析（M3-04，SPEC 8.5，设计 docs/design/M3.md 第五节）。

Pillow 打开 → EXIF 方向校正 → 转 RGB → 长边超过 MAX_OCR_SIDE 时等比缩小后识别，坐标换算回原图像素。
bbox 以 EXIF 校正后的原图像素为准。解析失败只抛出 ParseError，消息是固定的中文原因。
"""
import io
import logging
import time
import warnings

from PIL import Image, ImageOps

from app.parsing import ocr
from app.parsing.docx import manual_clause_no
from app.parsing.model import LOW_OCR_CONF, ParsedFile, ParsedNode, ParseError

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


def parse_image(data: bytes, file_seq: int) -> ParsedFile:
    img = _open(data)
    width, height = img.size
    ratio = 1.0
    work = img
    if max(width, height) > MAX_OCR_SIDE:
        ratio = MAX_OCR_SIDE / max(width, height)
        work = img.resize((max(1, round(width * ratio)), max(1, round(height * ratio))), Image.LANCZOS)

    started = time.monotonic()
    try:
        lines = ocr.recognize(work)
    except Exception:  # noqa: BLE001
        raise ParseError(MSG_UNREADABLE) from None
    if ratio != 1.0:
        for ln in lines:
            ln.bbox = [round(v / ratio, 2) for v in ln.bbox]

    nodes: list[ParsedNode] = []
    low = False
    for no, block in enumerate(ocr.merge_lines(lines), start=1):
        text, bbox, conf = ocr.block_of(block)
        low = low or conf < LOW_OCR_CONF
        nodes.append(
            ParsedNode(
                loc=f"F{file_seq}-PG1-B{no}", ord=no, kind="ocr_block", part="body", text=text,
                clause_no=manual_clause_no(text), page=1, bbox=bbox, ocr_conf=conf,
            )
        )
    log.info("image ocr: blocks=%d elapsed=%.1fs", len(nodes), time.monotonic() - started)
    if not nodes:
        raise ParseError(MSG_NO_TEXT)
    return ParsedFile(nodes=nodes, page_count=1, has_revisions=False, warnings=[MSG_LOW_CONF] if low else [], ocr_pages=1)
