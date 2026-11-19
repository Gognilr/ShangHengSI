"""扫描页线框表格（V34-D-02，docs/v34/PLAN.md 第三节，V34Q-55）。

不引入模型：OCR 之后用 opencv 形态学检测横竖线得到表格网格，OCR 行按中心点落入单元格。
* 取 RGB 三通道中的最大值再二值化：红色印章在最大通道里接近白色，压在表格上不会被当成线。
* 按长横线估计倾角，把线条图旋正后再取网格；单元格框换算回原图坐标。
* 每一行内，两列之间的竖线缺了（合并单元格）就把两列并成一格，单元格记在左起第一列（与 pdfplumber 一致）。
* 网格不成形（横线或竖线不足 3 条）或落入率低（OCR 行跨过竖线、落不进单元格）时整张表不用，
  这些行照旧合并为 ocr_block。
"""
import logging
import math
from dataclasses import dataclass

from app.parsing import ocr
from app.parsing.ocr import OcrLine

log = logging.getLogger(__name__)

MIN_FIT = 0.8  # 落入率：表格区域内能完整落进一个单元格的 OCR 行比例
LINE_COVER = 0.5  # 横线（竖线）须覆盖表格区域宽（高）的比例
SEP_COVER = 0.6  # 一行内两列之间的竖线须覆盖该行高度的比例，否则视为合并单元格


@dataclass
class Cell:
    row: int  # 网格行号，从 1 开始
    col: int  # 左起第一列，从 1 开始
    lines: list[OcrLine]
    bbox: list[float]  # 原图坐标（已除以 scale）

    @property
    def text(self) -> str:
        return ocr.join_text([ln.text for ln in _visual_order(self.lines)])

    @property
    def conf(self) -> float:
        return round(min(ln.conf for ln in self.lines), 4)


@dataclass
class Table:
    top: float
    cells: list[Cell]  # 非空单元格，按行、列排序


def _visual_order(lines: list[OcrLine]) -> list[OcrLine]:
    """同一格内的多行：先按行（纵向中心相差小于半个行高为同一行），行内从左到右。"""
    rows: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda d: (d.bbox[1] + d.bbox[3]) / 2):
        cy, h = (ln.bbox[1] + ln.bbox[3]) / 2, ln.bbox[3] - ln.bbox[1]
        if rows:
            last = rows[-1][-1]
            if abs(cy - (last.bbox[1] + last.bbox[3]) / 2) < 0.5 * max(h, last.bbox[3] - last.bbox[1]):
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return [ln for row in rows for ln in sorted(row, key=lambda d: d.bbox[0])]


def _runs(on) -> list[float]:
    """布尔序列中连续为真的段 → 各段中心。"""
    out, start = [], None
    for i, v in enumerate(list(on) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start + i - 1) / 2)
            start = None
    return out


def _masks(binary, w: int, h: int):
    import cv2

    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 25), 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 50))))
    return horiz, vert


def _skew(horiz, w: int) -> float:
    """长横线的中位斜率对应的角度（度）；没有长横线时 0。"""
    import cv2
    import numpy as np

    n, labels, stats, _ = cv2.connectedComponentsWithStats(horiz, connectivity=8)
    slopes = []
    for i in range(1, n):
        x, y, bw, bh = stats[i][:4]
        if bw < w / 5:
            continue
        ys, xs = np.nonzero(labels[y:y + bh, x:x + bw] == i)
        if len(xs) > 10:
            slopes.append(float(np.polyfit(xs, ys, 1)[0]))
    return math.degrees(math.atan(sorted(slopes)[len(slopes) // 2])) if slopes else 0.0


def find_tables(image, lines: list[OcrLine], scale: float = 1.0) -> tuple[list[Table], set[int]]:
    """线框表格；检测出错时不阻断解析，整页照旧走 ocr_block。"""
    try:
        return _find_tables(image, lines, scale)
    except Exception:  # noqa: BLE001
        log.warning("scan table detection failed")
        return [], set()


def _find_tables(image, lines: list[OcrLine], scale: float) -> tuple[list[Table], set[int]]:
    """在 image（PIL，像素）上找线框表格；lines 为同一图片上的 OCR 行（像素坐标）。

    返回 (表格, 被表格用掉的行下标)。单元格 bbox 除以 scale（PDF 页换算为 PDF 点）。
    """
    import cv2
    import numpy as np

    if not lines:
        return [], set()
    ink = np.asarray(image.convert("RGB")).max(axis=2)
    h, w = ink.shape
    binary = cv2.adaptiveThreshold(ink, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    horiz, vert = _masks(binary, w, h)
    angle = _skew(horiz, w)
    matrix = np.float32([[1, 0, 0], [0, 1, 0]])
    if abs(angle) > 0.05:
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        binary = cv2.warpAffine(binary, matrix, (w, h), flags=cv2.INTER_NEAREST)
        horiz, vert = _masks(binary, w, h)
    inverse = cv2.invertAffineTransform(matrix)

    def fwd(x, y):
        return (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2], matrix[1][0] * x + matrix[1][1] * y + matrix[1][2])

    def back(x, y):
        return (inverse[0][0] * x + inverse[0][1] * y + inverse[0][2], inverse[1][0] * x + inverse[1][1] * y + inverse[1][2])

    # 旋正后各行的中心与左右端
    placed = []
    for ln in lines:
        x0, y0, x1, y1 = ln.bbox
        cy = (y0 + y1) / 2
        (ax, ay), (bx, by) = fwd(x0, cy), fwd(x1, cy)
        placed.append(((ax + bx) / 2, (ay + by) / 2, min(ax, bx), max(ax, bx)))

    grid = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tables: list[Table] = []
    used: set[int] = set()
    for contour in contours:
        rx, ry, rw, rh = cv2.boundingRect(contour)
        if rw < w * 0.15 or rh < 30:
            continue
        ys = [ry + v for v in _runs(horiz[ry:ry + rh, rx:rx + rw].sum(axis=1) >= LINE_COVER * rw * 255)]
        xs = [rx + v for v in _runs(vert[ry:ry + rh, rx:rx + rw].sum(axis=0) >= LINE_COVER * rh * 255)]
        if len(ys) < 3 or len(xs) < 3:
            continue
        # 每行的列分段：两列之间的竖线缺了就合并
        spans: list[list[tuple[int, int]]] = []
        for r in range(len(ys) - 1):
            top, bottom = int(ys[r]) + 4, int(ys[r + 1]) - 3
            row, start = [], 0
            for c in range(1, len(xs) - 1):
                x = int(xs[c])
                seg = vert[top:bottom, max(0, x - 4):x + 5].max(axis=1) if bottom > top else np.zeros(0)
                if seg.size and (seg > 0).mean() >= SEP_COVER:
                    row.append((start, c - 1))
                    start = c
            row.append((start, len(xs) - 2))
            spans.append(row)
        inside, fit, cells = [], 0, {}
        for i, (cx, cy, left, right) in enumerate(placed):
            if not (xs[0] < cx < xs[-1] and ys[0] < cy < ys[-1]):
                continue
            inside.append(i)
            r = next(k for k in range(len(ys) - 1) if cy < ys[k + 1])
            c = next(k for k in range(len(xs) - 1) if cx < xs[k + 1])
            c0, c1 = next(s for s in spans[r] if s[0] <= c <= s[1])
            width = max(right - left, 1.0)
            if (min(right, xs[c1 + 1]) - max(left, xs[c0])) / width >= 0.9:
                fit += 1
            cells.setdefault((r, c0, c1), []).append(i)
        if not inside or fit < MIN_FIT * len(inside):
            continue
        out = []
        for (r, c0, c1), idx in sorted(cells.items()):
            corners = [back(x, y) for x in (xs[c0], xs[c1 + 1]) for y in (ys[r], ys[r + 1])]
            bbox = [round(float(min(p[0] for p in corners)) / scale, 2), round(float(min(p[1] for p in corners)) / scale, 2),
                    round(float(max(p[0] for p in corners)) / scale, 2), round(float(max(p[1] for p in corners)) / scale, 2)]
            out.append(Cell(row=r + 1, col=c0 + 1, lines=[lines[i] for i in idx], bbox=bbox))
        used.update(inside)
        tables.append(Table(top=min(c.bbox[1] for c in out), cells=out))
    tables.sort(key=lambda t: t.top)
    return tables, used
