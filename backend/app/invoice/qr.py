"""票面二维码（设计第三节、第八节）：只取号码、日期、金额用于核对，不覆盖其他来源。

用 opencv 自带的解码器（运行镜像已有 opencv-python-headless），不新增依赖。二维码内容不写日志。
"""
import re

from app.invoice.model import Qr, norm_date, parse_money


def parse(content: str) -> Qr | None:
    """“01,32,,24112000000012345678,100.00,20241101,,ABCD”：8 段逗号分隔，第一段 01。"""
    parts = [p.strip() for p in (content or "").split(",")]
    if len(parts) < 6 or parts[0] != "01" or not re.fullmatch(r"\d{8,20}", parts[3]):
        return None
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", parts[5])
    return Qr(kind_code=parts[1], code=parts[2], number=parts[3], amount=parse_money(parts[4]),
              date=norm_date(*m.groups()) if m else None)


def decode(image, scale: float = 1.0) -> list[Qr]:
    """在 PIL 图片上找发票二维码；中心点坐标除以 scale（PDF 页换算回 PDF 点）。"""
    import cv2
    import numpy as np

    arr = np.asarray(image.convert("L"))
    found: list[Qr] = []
    detector = cv2.QRCodeDetector()
    try:
        ok, texts, points, _ = detector.detectAndDecodeMulti(arr)
    except cv2.error:
        ok, texts, points = False, (), None
    if ok and points is not None:
        for text, pts in zip(texts, points):
            q = parse(text)
            if q is not None:
                q.cx, q.cy = float(pts[:, 0].mean()) / scale, float(pts[:, 1].mean()) / scale
                found.append(q)
    if not found and hasattr(cv2, "QRCodeDetectorAruco"):
        try:
            ok, texts, points, _ = cv2.QRCodeDetectorAruco().detectAndDecodeMulti(arr)
        except cv2.error:
            ok = False
        if ok and points is not None:
            for text, pts in zip(texts, points):
                q = parse(text)
                if q is not None:
                    q.cx, q.cy = float(pts[:, 0].mean()) / scale, float(pts[:, 1].mean()) / scale
                    found.append(q)
    seen, out = set(), []
    for q in found:
        if q.number not in seen:
            seen.add(q.number)
            out.append(q)
    return out
