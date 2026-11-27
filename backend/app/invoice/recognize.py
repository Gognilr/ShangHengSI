"""逐个文件识别（设计第三节）：XML、OFD 读结构化数据；PDF 文字层按坐标套版式；扫描件与照片走现有 RapidOCR。

二维码只用于核对。调用方（job）对每个文件持 worker.ocr_slot()：pdfium 与 OCR 引擎都不是线程安全的。
识别出的文字、号码、名称与文件名都不写日志。
"""
import io
import math
from dataclasses import dataclass, field

from app.invoice import layout, qr, structured
from app.invoice.layout import Seg
from app.invoice.model import Invoice
from app.parsing.model import LOW_OCR_CONF, MAX_PDF_PAGES

QR_DPI = (200, 300)  # 先试 200 DPI，解不出再试 300 DPI（设计第三节）
OCR_DPI = 200
MAX_PHOTO_SIDE = 3200
TEXT_PAGE_MIN_CHARS = 20

MSG_PDF_PASSWORD = "PDF 设有打开密码，请取消密码后再传"
MSG_PDF_BAD = "PDF 无法读取，可能已损坏"
MSG_TOO_MANY_PAGES = f"PDF 超过 {MAX_PDF_PAGES} 页"
MSG_IMAGE_BAD = "图片无法读取"
MSG_NOT_INVOICE = "认不出是发票"
MSG_NO_NUMBER = "未读到发票号码"
MSG_MULTI = "这一页像有多张发票，请分开后再传"
MSG_OCR_FULL = "本批扫描件与照片已满 100 页，其余未识别"


@dataclass
class FileResult:
    invoices: list[Invoice] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (位置后缀, 原因)
    ocr_pages: int = 0


def recognize(name: str, ext: str, data: bytes, *, ocr_budget: int) -> FileResult:
    if ext == "xml":
        got = structured.read_xml(data)
        return _single(name, got)
    if ext == "ofd":
        return _single(name, structured.read_ofd(data))
    if ext == "pdf":
        return _read_pdf(name, data, ocr_budget)
    return _read_photo(name, data, ocr_budget)


def _single(name: str, got: Invoice | str) -> FileResult:
    if isinstance(got, str):
        return FileResult(failures=[("", got)])
    got.files = [name]
    return FileResult(invoices=[got])


# ---------------------------------------------------------------- 图像处理

def remove_red(img):
    """去红章：只把鲜红的印章色换成白色，压在名称、备注上的印章不再干扰识别。

    数电票的版式标签本身是暗红色（实测约 128,0,0），不能按“偏红”一刀切，否则锚点全被抹掉；
    这里只去亮度高、饱和的红（印章油墨、浅色水印），暗红的标签与黑字保留。"""
    import numpy as np
    from PIL import Image

    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mask = (r >= 200) & (r - np.maximum(g, b) > 45)
    if not mask.any():
        return img
    out = arr.copy()
    out[mask] = 255
    return Image.fromarray(out.astype(np.uint8))


def straighten(img):
    """照片做透视矫正：找面积最大的四边形（占画面 30%～97%）拉成矩形；找不到时原样返回。"""
    import cv2
    import numpy as np
    from PIL import Image

    arr = np.asarray(img.convert("RGB"))
    h, w = arr.shape[:2]
    # 四周一圈已是白纸（扫描件、裁好的图）：纸占满画面，不再找框，免得把票面里的表格框当成纸边
    m = max(2, min(h, w) // 40)
    border = np.concatenate([arr[:m].reshape(-1, 3), arr[-m:].reshape(-1, 3), arr[:, :m].reshape(-1, 3), arr[:, -m:].reshape(-1, 3)])
    if border.mean() > 200:
        return img
    k = 1000 / max(h, w) if max(h, w) > 1000 else 1.0
    small = cv2.resize(arr, (max(1, int(w * k)), max(1, int(h * k)))) if k != 1.0 else arr
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_all = small.shape[0] * small.shape[1]
    best = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4 and 0.3 * area_all <= cv2.contourArea(approx) <= 0.97 * area_all:
            best = approx.reshape(4, 2).astype(np.float32) / k
            break
    if best is None:
        return img
    s, d = best.sum(axis=1), np.diff(best, axis=1).ravel()
    tl, br, tr, bl = best[np.argmin(s)], best[np.argmax(s)], best[np.argmin(d)], best[np.argmax(d)]
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if width < 200 or height < 100:
        return img
    m = cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]),
                                    np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]]))
    return Image.fromarray(cv2.warpPerspective(arr, m, (width, height), flags=cv2.INTER_CUBIC))


def _ocr_segs(img) -> list[Seg]:
    from app.parsing import ocr

    return [Seg(ln.text, *ln.bbox, conf=ln.conf) for ln in ocr.recognize(remove_red(img))]


# ---------------------------------------------------------------- 一页 → 票

def _page_invoices(name: str, segs: list[Seg], qrs, width: float, source: str, where: str, res: FileResult) -> None:
    groups = layout.split_page(segs, width)
    if groups is None:
        res.failures.append((where, MSG_MULTI))
        return
    for group, code in zip(groups, layout.assign_qr(groups, qrs)):
        rows = layout.build_rows(group)
        inv = layout.read_invoice(rows, source=source, low_conf=LOW_OCR_CONF)
        if not inv.number:
            if layout.looks_like_invoice(rows):
                res.failures.append((where, MSG_NO_NUMBER))
            elif len(groups) == 1 and not res.invoices:
                res.failures.append((where, MSG_NOT_INVOICE))
            continue
        if not inv.kind and code is not None:
            inv.kind = layout.QR_KINDS.get(code.kind_code, "")
        inv.qr = code
        inv.files = [name]
        res.invoices.append(inv)


def _render(pdfium_doc, i: int, dpi: int):
    page = pdfium_doc[i]
    try:
        w, h = page.get_size()
        scale = min(dpi / 72, 6000 / max(w, h, 1.0))
        return page.render(scale=scale).to_pil().convert("RGB"), scale
    finally:
        page.close()


def _read_pdf(name: str, data: bytes, budget: int) -> FileResult:
    import pdfplumber
    import pypdfium2
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    res = FileResult()
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
        n = len(pdf.pages)
    except Exception as exc:  # noqa: BLE001 —— 只给固定原因，不带异常消息
        wrong = isinstance(exc, PDFPasswordIncorrect) or any(isinstance(a, PDFPasswordIncorrect) for a in exc.args)
        return FileResult(failures=[("", MSG_PDF_PASSWORD if wrong else MSG_PDF_BAD)])
    try:
        if n == 0:
            return FileResult(failures=[("", MSG_PDF_BAD)])
        if n > MAX_PDF_PAGES:
            return FileResult(failures=[("", MSG_TOO_MANY_PAGES)])
        doc = pypdfium2.PdfDocument(data)
        try:
            for i in range(n):
                where = f"第 {i + 1} 页" if n > 1 else ""
                try:
                    page = pdf.pages[i].dedupe_chars()
                    text = "".join(c["text"] for c in page.chars if not c["text"].isspace())
                except Exception:  # noqa: BLE001
                    res.failures.append((where, MSG_PDF_BAD))
                    continue
                if len(text) >= TEXT_PAGE_MIN_CHARS:
                    words = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=2)
                    segs = [Seg(w["text"], w["x0"], w["top"], w["x1"], w["bottom"]) for w in words]
                    qrs = []
                    for dpi in QR_DPI:
                        img, scale = _render(doc, i, dpi)
                        qrs = qr.decode(img, scale)
                        if qrs:
                            break
                    _page_invoices(name, segs, qrs, float(page.width), "text", where, res)
                    continue
                if res.ocr_pages >= budget:
                    res.failures.append((where, MSG_OCR_FULL))
                    continue
                res.ocr_pages += 1
                img, scale = _render(doc, i, OCR_DPI)
                qrs = qr.decode(img, 1.0)
                if not qrs:
                    big, k = _render(doc, i, QR_DPI[1])
                    qrs = qr.decode(big, k / scale)
                _page_invoices(name, _ocr_segs(img), qrs, float(img.width), "scan", where, res)
        finally:
            doc.close()
    finally:
        pdf.close()
    if not res.invoices and not res.failures:
        res.failures.append(("", MSG_NOT_INVOICE))
    return res


def _read_photo(name: str, data: bytes, budget: int) -> FileResult:
    from PIL import Image

    from app.parsing.image import _open
    from app.parsing.model import ParseError

    if budget <= 0:
        return FileResult(failures=[("", MSG_OCR_FULL)])
    try:
        img = _open(data)
    except ParseError:
        return FileResult(failures=[("", MSG_IMAGE_BAD)])
    if max(img.size) > MAX_PHOTO_SIDE:
        k = MAX_PHOTO_SIDE / max(img.size)
        img = img.resize((max(1, math.floor(img.width * k)), max(1, math.floor(img.height * k))), Image.LANCZOS)
    img = straighten(img)
    res = FileResult(ocr_pages=1)
    _page_invoices(name, _ocr_segs(img), qr.decode(img), float(img.width), "photo", "", res)
    if not res.invoices and not res.failures:
        res.failures.append(("", MSG_NOT_INVOICE))
    return res
