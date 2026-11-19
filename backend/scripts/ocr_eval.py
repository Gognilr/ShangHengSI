"""OCR 选型评测（V34-D-01，D-02 沿用）。只在评测镜像中运行，产品代码不引用。

    python scripts/ocr_eval.py fixtures <候选>            # tests/fixtures/ocr_eval 的虚构样本：字错率、字段命中、耗时
    python scripts/ocr_eval.py pdfs <候选> <目录>          # 目录下全部 PDF 按产品路径解析：OCR 页数、低置信页数、耗时

候选：v4（现用 rapidocr-onnxruntime 1.4.4）、v6s（rapidocr 3.x 自带 PP-OCRv6 small）、
v5m（PP-OCRv5 mobile）、v6m（PP-OCRv6 medium）；后两者的模型文件放在 OCR_EVAL_MODELS 目录。
pdfs 模式只输出计数与耗时，不输出任何识别文字、文件名与路径（真实标书只在本机评测）。
"""
import json
import os
import resource
import sys
import time
import unicodedata
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.parsing import ocr  # noqa: E402
from app.parsing.ocr import OcrLine  # noqa: E402

MODELS = Path(os.environ.get("OCR_EVAL_MODELS", "/models"))


def make_engine(name: str):
    if name == "v4":
        from rapidocr_onnxruntime import RapidOCR

        eng = RapidOCR()
        return lambda arr: [(b, t, s) for b, t, s in (eng(arr)[0] or [])]
    from rapidocr import ModelType, OCRVersion, RapidOCR

    params = {"Global.log_level": "warning"}
    if name == "v5m":
        params.update({
            "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE, "Det.model_path": str(MODELS / "ch_PP-OCRv5_det_mobile.onnx"),
            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE, "Rec.model_path": str(MODELS / "ch_PP-OCRv5_rec_mobile.onnx"),
        })
    elif name == "v6m":
        params.update({
            "Det.model_type": ModelType.MEDIUM, "Det.model_path": str(MODELS / "PP-OCRv6_det_medium.onnx"),
            "Rec.model_type": ModelType.MEDIUM, "Rec.model_path": str(MODELS / "PP-OCRv6_rec_medium.onnx"),
        })
    elif name != "v6s":
        raise SystemExit(f"未知候选 {name}")
    eng = RapidOCR(params=params)

    def run(arr):
        r = eng(arr)
        if r.boxes is None:
            return []
        return list(zip(r.boxes.tolist(), r.txts, r.scores))

    return run


def install(name: str):
    """替换 ocr.recognize 的引擎部分，其余（bbox、空行丢弃）与产品一致。"""
    import numpy as np

    if name == "app":  # 产品现用的 ocr.recognize（含块置信度规则），不替换
        return ocr.recognize
    run = make_engine(name)

    def recognize(image):
        arr = np.array(image.convert("RGB"))[:, :, ::-1]
        lines = []
        for box, text, score in run(arr):
            text = (text or "").strip()
            if not text:
                continue
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            lines.append(OcrLine(text=text, bbox=[float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))], conf=float(score)))
        return lines

    ocr.recognize = recognize
    return recognize


def norm(s: str) -> str:
    return "".join(unicodedata.normalize("NFKC", s).split())


def reading_order(lines: list[OcrLine]) -> str:
    """按行聚类（纵向中心相差小于半个行高视为同一行），行内从左到右。"""
    rows: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda d: (d.bbox[1] + d.bbox[3]) / 2):
        c, h = (ln.bbox[1] + ln.bbox[3]) / 2, ln.bbox[3] - ln.bbox[1]
        if rows:
            last = rows[-1][-1]
            if abs(c - (last.bbox[1] + last.bbox[3]) / 2) < 0.5 * max(h, last.bbox[3] - last.bbox[1]):
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return "".join(ln.text for row in rows for ln in sorted(row, key=lambda d: d.bbox[0]))


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def run_fixtures(name: str):
    import yaml
    from PIL import Image

    from app.parsing.model import LOW_OCR_CONF

    fx = BACKEND / "tests" / "fixtures" / "ocr_eval"
    manifest = yaml.safe_load((fx / "manifest.yaml").read_text(encoding="utf-8"))["samples"]
    t0 = time.perf_counter()
    recognize = install(name)
    recognize(Image.new("RGB", (200, 60), "white"))  # 加载模型
    load_s = time.perf_counter() - t0
    tot_err = tot_len = hit = nfield = 0
    times, out = [], {}
    for key, s in manifest.items():
        img = Image.open(fx / s["file"])
        t = time.perf_counter()
        lines = recognize(img)
        times.append(time.perf_counter() - t)
        got, truth = norm(reading_order(lines)), norm("".join(s["lines"]))
        err = edit_distance(truth, got)
        tot_err, tot_len = tot_err + err, tot_len + len(truth)
        miss = [f for f in s["fields"] if norm(f) not in got]
        hit, nfield = hit + len(s["fields"]) - len(miss), nfield + len(s["fields"])
        blocks = [ocr.block_of(b)[2] for b in ocr.merge_lines(lines)]
        out[key] = {
            "cer": round(err / len(truth), 4), "fields_missed": miss, "sec": round(times[-1], 2),
            "low_conf_blocks": sum(c < LOW_OCR_CONF for c in blocks), "blocks": len(blocks),
        }
    print(json.dumps({
        "candidate": name, "load_sec": round(load_s, 2), "cer": round(tot_err / tot_len, 4),
        "fields": f"{hit}/{nfield}", "sec_per_page": round(sum(times) / len(times), 2),
        "peak_rss_mb": round(peak_mb()), "pages": out,
    }, ensure_ascii=False, indent=1))


def run_pdfs(name: str, root: str):
    from app.parsing import pdf

    install(name)
    files = sorted(Path(root).rglob("*.pdf")) + sorted(Path(root).rglob("*.PDF"))
    ocr_pages = low_pages = text_pages = failed = 0
    t0 = time.perf_counter()
    for f in files:
        data = f.read_bytes()
        parsed = None
        for pw in (None, ""):
            try:
                parsed = pdf.parse_pdf(data, 1, password=pw, max_pages=1000)
                break
            except Exception:  # noqa: BLE001 —— 只计数，不输出异常内容
                continue
        if parsed is None:
            failed += 1
            continue
        pages = {n.page for n in parsed.nodes if n.kind == "ocr_block"}
        low = {n.page for n in parsed.nodes if n.kind == "ocr_block" and n.ocr_conf is not None and n.ocr_conf < pdf.LOW_OCR_CONF}
        ocr_pages, low_pages = ocr_pages + len(pages), low_pages + len(low)
        text_pages += len({n.page for n in parsed.nodes} - pages)
    total = time.perf_counter() - t0
    print(json.dumps({
        "candidate": name, "files": len(files), "failed": failed, "ocr_pages": ocr_pages, "low_conf_pages": low_pages,
        "text_pages": text_pages, "total_sec": round(total, 1), "sec_per_ocr_page": round(total / max(ocr_pages, 1), 2),
        "peak_rss_mb": round(peak_mb()),
    }, ensure_ascii=False))


if __name__ == "__main__":
    mode, cand = sys.argv[1], sys.argv[2]
    run_fixtures(cand) if mode == "fixtures" else run_pdfs(cand, sys.argv[3])
