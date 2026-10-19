"""OCR（M3-04，SPEC 8.5，设计 docs/design/M3.md 第五节，open_questions Q-64）。

RapidOCR 引擎在进程内首次使用时创建一次并复用；调用方负责用 worker.ocr_slot() 互斥，这里不加锁。
创建引擎前先关闭 onnxruntime 的遥测；模型文件随 rapidocr-onnxruntime 包进镜像，运行时不下载任何东西。
日志只写行数和耗时，不写识别出的文字。
"""
import re
from dataclasses import dataclass

from app.parsing.docx import manual_clause_no

_CJK_END = re.compile(r"[一-鿿，。；：、？！（）“”‘’《》]")

_engine = None


@dataclass
class OcrLine:
    text: str
    bbox: list[float]  # [x0, top, x1, bottom]，输入图片的像素坐标
    conf: float


def _get_engine():
    global _engine
    if _engine is None:
        import onnxruntime

        onnxruntime.disable_telemetry_events()
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def recognize(image) -> list[OcrLine]:
    """识别 PIL 图片，返回文字行（空文字的行丢弃），bbox 为四点框的外接矩形。"""
    import numpy as np

    arr = np.array(image.convert("RGB"))[:, :, ::-1]  # RGB → BGR
    result, _ = _get_engine()(arr)
    lines: list[OcrLine] = []
    for box, text, score in result or []:
        text = (text or "").strip()
        if not text:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        lines.append(
            OcrLine(text=text, bbox=[float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))], conf=float(score))
        )
    return lines


def join_text(parts: list[str]) -> str:
    """两侧都是汉字或中文标点时直接相连，否则以一个空格相连。"""
    out = parts[0]
    for nxt in parts[1:]:
        out += ("" if _CJK_END.match(out[-1]) and _CJK_END.match(nxt[0]) else " ") + nxt
    return out


def merge_lines(lines: list[OcrLine], min_gap: float = 3.0) -> list[list[OcrLine]]:
    """文本行合并为块（PDF 扫描页与图片共用）。

    规则与 PDF 文字页相同：行间距 ≤ max(min_gap, 0.6 × 上一行高度)，且下一行不以条款编号开头；
    与上一行纵向重叠过半（同一行上的左右两段）时另起一块。min_gap 与 bbox 同单位。
    """
    blocks: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda d: (d.bbox[1], d.bbox[0])):
        if blocks:
            prev = blocks[-1][-1]
            prev_h = prev.bbox[3] - prev.bbox[1]
            gap = ln.bbox[1] - prev.bbox[3]
            if -0.5 * prev_h <= gap <= max(min_gap, 0.6 * prev_h) and manual_clause_no(ln.text) is None:
                blocks[-1].append(ln)
                continue
        blocks.append([ln])
    return blocks


def block_of(block: list[OcrLine]) -> tuple[str, list[float], float]:
    """块 → (文字, 外接 bbox, 平均置信度)。"""
    text = join_text([ln.text for ln in block])
    bbox = [
        round(min(b.bbox[0] for b in block), 2), round(min(b.bbox[1] for b in block), 2),
        round(max(b.bbox[2] for b in block), 2), round(max(b.bbox[3] for b in block), 2),
    ]
    return text, bbox, round(sum(b.conf for b in block) / len(block), 4)
