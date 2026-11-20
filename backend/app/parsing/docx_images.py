"""DOCX 内嵌图片识字（V35-C-01，SPEC 8.3，V35Q-02）。

正文（含表格、文本框）中达到证照尺寸的图片逐张 OCR，节点 kind=ocr_block / table_cell、part=body，
loc F{n}-IM{张}-B{块} / F{n}-IM{张}-T{表}-R{行}-C{列}，parent_loc 为图片所在的段落或单元格，紧跟在该节点之后；
page、bbox 为空（DOCX 没有页面坐标）。每张图片占 1 页 OCR 名额；名额不足时只识别前面的图片并提示，不让整份文件失败。
文件审核与标书招标文件当场识别（调用方持 ocr_slot）；标书投标文件排入延后识别队列（bid/pipeline）。
图片只在内存中读取，不另存；日志只写张数与耗时。
"""
import logging
import time
import zipfile
from io import BytesIO

from app.parsing import docx, image
from app.parsing.model import LOW_OCR_CONF, DocxImage, ParsedFile, ParsedNode

# 证照尺寸：短边、长边像素下限（印章、签名、徽标、项目符号等小图不识别）与文件大小下限
MIN_SHORT_PX = 240
MIN_LONG_PX = 400
MIN_BYTES = 8 * 1024
ORD_GAP = 10000  # 延后识别时原有节点 ord × ORD_GAP，图片节点插在所在节点之后（与 bid/pipeline 的页间隔相同）

WARN_DONE = "已识别 {count} 张内嵌图片中的文字"
WARN_LOW = "内嵌图片 OCR 识别置信度低，请核对原件"
WARN_PENDING = "{count} 张内嵌图片正在后台识别"
WARN_OVER = "另有 {count} 张内嵌图片超出 OCR 页数上限，未识别文字"

log = logging.getLogger(__name__)


def _size(blob: bytes) -> tuple[int, int]:
    from PIL import Image

    try:
        with Image.open(BytesIO(blob)) as im:  # 只读文件头；EMF / WMF 等矢量图读不出尺寸，不识别
            return im.size
    except Exception:  # noqa: BLE001
        return 0, 0


def select(data: bytes, parsed: ParsedFile) -> list[DocxImage]:
    """筛出证照尺寸的图片，按出现顺序；序号即在此列表中的位置（从 1 起），重新解析时不变。"""
    if not parsed.images:
        return []
    out = []
    with zipfile.ZipFile(BytesIO(data)) as zf:
        members = {i.filename.lower(): i for i in zf.infolist()}
        for img in parsed.images:
            info = members.get(img.target.lower())
            if info is None or info.file_size < MIN_BYTES:
                continue
            w, h = _size(zf.read(info))
            if min(w, h) >= MIN_SHORT_PX and max(w, h) >= MIN_LONG_PX:
                out.append(img)
    return out


def _read(data: bytes, target: str) -> bytes:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        info = next(i for i in zf.infolist() if i.filename.lower() == target.lower())
        return zf.read(info)


def recognize(data: bytes, file_seq: int, no: int, img: DocxImage) -> tuple[list[ParsedNode], list[float]]:
    """识别第 no 张图片（select 中的序号）。图片读不出或识别失败只返回空，不影响整份文件。"""
    try:
        pic = image._open(_read(data, img.target))
        nodes, confs = image.ocr_nodes(pic, f"F{file_seq}-IM{no}", None)
    except Exception:  # noqa: BLE001 —— 单张图片失败不阻断，不带异常内容
        return [], []
    for n in nodes:
        n.bbox = None
        n.parent_loc = img.parent_loc
    return nodes, confs


def pending_ord(picks: list[DocxImage], no: int, i: int) -> int:
    """延后识别的第 no 张图片第 i 个节点的 ord：所在节点 ord（已 × ORD_GAP）之后，同一处的多张图片依次排开。"""
    img = picks[no - 1]
    slot = sum(1 for p in picks[:no - 1] if p.after == img.after)
    return img.after * ORD_GAP + 1 + slot * 1000 + i


def ocr_inline(data: bytes, parsed: ParsedFile, file_seq: int, budget: int, on_ocr=None) -> ParsedFile:
    """当场识别（调用方持 ocr_slot）：最多 budget 张，节点插回所在位置并重排 ord，追加提示。"""
    picks = select(data, parsed)
    if not picks:
        return parsed
    todo = picks[:max(0, budget)]
    started = time.monotonic()
    inserts: dict[int, list[ParsedNode]] = {}
    low = False
    for no, img in enumerate(todo, start=1):
        nodes, confs = recognize(data, file_seq, no, img)
        low = low or any(c < LOW_OCR_CONF for c in confs)
        inserts.setdefault(img.after, []).extend(nodes)
        if on_ocr is not None:
            on_ocr(no)
    merged: list[ParsedNode] = list(inserts.get(0, []))
    for i, node in enumerate(parsed.nodes, start=1):
        merged.append(node)
        merged.extend(inserts.get(i, []))
    for i, node in enumerate(merged, start=1):
        node.ord = i
    parsed.nodes = merged
    parsed.ocr_pages += len(todo)
    if todo:
        log.info("docx images ocr: images=%d elapsed=%.1fs", len(todo), time.monotonic() - started)
        parsed.warnings.append(WARN_DONE.format(count=len(todo)))
    if low:
        parsed.warnings.append(WARN_LOW)
    if len(picks) > len(todo):
        parsed.warnings.append(WARN_OVER.format(count=len(picks) - len(todo)))
    return parsed


def defer(data: bytes, parsed: ParsedFile, budget: int) -> ParsedFile:
    """延后识别（标书投标文件）：原有节点 ord 拉开间隔，待识别的图片序号放进 ocr_pending。"""
    picks = select(data, parsed)
    if not picks:
        return parsed
    for node in parsed.nodes:
        node.ord *= ORD_GAP
    todo = min(len(picks), max(0, budget))
    parsed.ocr_pending = list(range(1, todo + 1))
    if todo:
        parsed.warnings.append(WARN_PENDING.format(count=todo))
    if len(picks) > todo:
        parsed.warnings.append(WARN_OVER.format(count=len(picks) - todo))
    return parsed


def ocr_pending(data: bytes, file_seq: int, nos: list[int]):
    """延后识别：逐张产出 (序号, 新节点, 置信度)，节点 ord 已按 pending_ord 排好。"""
    picks = select(data, docx.parse_docx(data, file_seq))
    for no in nos:
        if no > len(picks):
            yield no, [], []
            continue
        nodes, confs = recognize(data, file_seq, no, picks[no - 1])
        for i, n in enumerate(nodes, start=1):
            n.ord = pending_ord(picks, no, i)
        yield no, nodes, confs
