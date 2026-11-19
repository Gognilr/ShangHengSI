"""OCR（M3-04，SPEC 8.5，设计 docs/design/M3.md 第五节，open_questions Q-64）。

RapidOCR 引擎在进程内首次使用时创建一次并复用；调用方负责用 worker.ocr_slot() 互斥，这里不加锁。
创建引擎前先关闭 onnxruntime 的遥测。模型（PP-OCRv6 small 检测 / 识别 + v2.0 方向分类，V34-D-01）随锁定的
rapidocr 包进镜像；创建引擎时显式传入三个模型文件的路径，rapidocr 给了路径就不进入下载分支，运行时不联网。
文件的 SHA-256 登记在 deploy/models.yaml，由 model-check 核对。
日志只写行数和耗时，不写识别出的文字。
"""
import re
from dataclasses import dataclass

from app.parsing.docx import manual_clause_no

_CJK_END = re.compile(r"[一-鿿，。；：、？！（）“”‘’《》]")

_engine = None

# 包内模型文件（rapidocr/models/ 下），键为 rapidocr 的配置段。
MODEL_FILES = {
    "Det": "PP-OCRv6_det_small.onnx",
    "Rec": "PP-OCRv6_rec_small.onnx",
    "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
}


def model_paths() -> dict:
    """{配置段: 包内模型文件路径}。只定位文件，不导入 rapidocr（避免加载其依赖）。"""
    import importlib.util
    from pathlib import Path

    root = Path(importlib.util.find_spec("rapidocr").origin).parent / "models"
    return {k: root / v for k, v in MODEL_FILES.items()}


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
        from rapidocr import RapidOCR

        params = {"Global.log_level": "warning"}
        params.update({f"{k}.model_path": str(v) for k, v in model_paths().items()})
        _engine = RapidOCR(params=params)
    return _engine


def recognize(image) -> list[OcrLine]:
    """识别 PIL 图片，返回文字行（空文字的行丢弃），bbox 为四点框的外接矩形。"""
    import numpy as np

    arr = np.array(image.convert("RGB"))[:, :, ::-1]  # RGB → BGR
    result = _get_engine()(arr)
    if result.boxes is None:  # 未检测到文字
        return []
    lines: list[OcrLine] = []
    for box, text, score in zip(result.boxes.tolist(), result.txts, result.scores):
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
    """块 → (文字, 外接 bbox, 置信度)。

    块置信度取最低一行（V34-D-01）：PP-OCRv6 的分数整体偏高，平均会把一行明显错读的数字摊平到阈值以上。
    """
    text = join_text([ln.text for ln in block])
    bbox = [
        round(min(b.bbox[0] for b in block), 2), round(min(b.bbox[1] for b in block), 2),
        round(max(b.bbox[2] for b in block), 2), round(max(b.bbox[3] for b in block), 2),
    ]
    return text, bbox, round(min(b.conf for b in block), 4)
