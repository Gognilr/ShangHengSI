"""V311-B-01 一次性离线剖析与批量对拍，不作为常规 pytest 用例。

在后端测试容器中运行，例如
python tests/ner_batch_probe.py --threads 8 --batch-size 4 --output /tmp/ner-probe.json
"""
import argparse
import json
import os
import resource
import statistics
import time
from pathlib import Path

INITIAL_BYTES = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024

import numpy as np
import yaml
from docx import Document
from openpyxl import Workbook

from app.parsing.docx import parse_docx
from app.parsing.xlsx import parse_xlsx
from app.redaction import ner


def samples(directory: Path, include_golden: bool) -> dict[str, list[str]]:
    fixtures = Path(__file__).parent / "fixtures"
    records = json.loads((fixtures / "ner/reference_corpus.json").read_text())["records"]
    source = "\n".join(r["input"] for r in records if r["origin"] == "narrative")
    text = (source * (10000 // len(source) + 1))[:10000]
    rows = [["企业", "联系人", "地址", "事项"]]
    for index in range(100):
        rows.append([
            "青弧贸易有限公司" if index % 2 else "Blue Arc Trading Limited",
            "联系人王海" if index % 2 else "Contact Alice Morgan",
            "上海市虹口区青弧路88号" if index % 2 else "18 Fictional Avenue, London",
            f"样品批次{index + 1}，按约交付",
        ])
    document, workbook = Document(), Workbook()
    table = document.add_table(rows=len(rows), cols=4)
    for row_index, row in enumerate(rows):
        workbook.active.append(row)
        for column, value in enumerate(row):
            table.cell(row_index, column).text = value
    docx_path, xlsx_path = directory / "ner-table.docx", directory / "ner-table.xlsx"
    document.save(docx_path)
    workbook.save(xlsx_path)
    result = {
        "benchmark": [text],
        "xlsx": [n.text for n in parse_xlsx(xlsx_path.read_bytes(), 1).nodes if n.text],
        "docx": [n.text for n in parse_docx(docx_path.read_bytes(), 1).nodes if n.text],
    }
    if include_golden:
        golden = []
        for path in sorted((fixtures / "redaction_golden").glob("*.txt")):
            annotation = yaml.safe_load(path.with_suffix(".yaml").read_text(encoding="utf-8"))
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if annotation.get("loc_prefixed"):
                lines = [line.split(" ", 1)[1] for line in lines]
            if annotation.get("parsed_text"):
                from app.parsing.model import strip_invisible
                lines = [strip_invisible(line) for line in lines]
            golden.extend(lines)
        result["golden"] = golden
    return result


def prepare(model, text: str, stages: dict):
    """沿用原始 window 的分词、张量与掩码构造。"""
    stamp = time.perf_counter()
    words = list(ner._SPLIT.finditer(text))
    if not words:
        return None
    prompt = ["<<ENT>>", "person", "<<ENT>>", "organization", "<<ENT>>", "street address", "<<SEP>>"]
    encoding = model.tokenizer.encode(prompt + [word.group() for word in words], is_pretokenized=True)
    mask, previous, seen = [], None, 0
    for word in encoding.word_ids:
        if word is None:
            mask.append(0)
        elif word != previous:
            seen += 1
            mask.append(max(0, seen - len(prompt)))
        else:
            mask.append(0)
        previous = word
    stages["tokenize"] += time.perf_counter() - stamp
    stamp = time.perf_counter()
    count = len(words)
    spans = np.array([(i, i + j) for i in range(count) for j in range(model.width)], dtype=np.int64)
    feed = {
        "input_ids": np.array([encoding.ids], dtype=np.int64),
        "attention_mask": np.array([encoding.attention_mask], dtype=np.int64),
        "words_mask": np.array([mask], dtype=np.int64),
        "text_lengths": np.array([[count]], dtype=np.int64),
        "span_idx": spans[None, :, :],
        "span_mask": (spans[:, 1] < count)[None, :],
    }
    stages["tensors"] += time.perf_counter() - stamp
    return words, feed


def decode(words, logits):
    """与原始 window 一致，包含 0.01 阈值与贪心去重。"""
    count = len(words)
    probs = 1 / (1 + np.exp(-np.clip(logits[:count], -80, 80)))
    candidates = []
    for start, width, label in zip(*np.where(probs > 0.01)):
        end = start + width
        if end < count:
            candidates.append(ner.Entity(words[start].start(), words[end].end(), ner.LABELS[label],
                                         float(probs[start, width, label])))
    accepted = []
    for entity in sorted(candidates, key=lambda e: -e.score):
        if not any(e.start < entity.end and entity.start < e.end for e in accepted):
            accepted.append(entity)
    return sorted(accepted, key=lambda e: e.start)


def batch_raw(model, texts: list[str], batch_size: int):
    stages = dict(tokenize=0.0, tensors=0.0, inference=0.0, decode=0.0)
    stamp = time.perf_counter()
    windows = []
    for node, text in enumerate(texts):
        for offset in range(0, len(text), 288):
            item = prepare(model, text[offset:offset + 320], stages)
            if item is not None:
                words, feed = item
                windows.append((node, offset, words, feed))
    windows.sort(key=lambda item: (len(item[2]), item[3]["input_ids"].shape[1]))
    outputs = [{} for _ in texts]
    calls = 0
    for start in range(0, len(windows), batch_size):
        batch = windows[start:start + batch_size]
        tensor_stamp = time.perf_counter()
        sequence = max(item[3]["input_ids"].shape[1] for item in batch)
        spans = max(item[3]["span_mask"].shape[1] for item in batch)
        padded = {}
        for key, value in batch[0][3].items():
            shape = list(value.shape)
            shape[0] = len(batch)
            if key in ("input_ids", "attention_mask", "words_mask"):
                shape[1] = sequence
            if key in ("span_idx", "span_mask"):
                shape[1] = spans
            pad = (model.tokenizer.token_to_id("[PAD]") or 0) if key == "input_ids" else 0
            padded[key] = np.full(shape, pad, dtype=value.dtype)
        for index, (_, _, _, feed) in enumerate(batch):
            for key, value in feed.items():
                padded[key][index, :value.shape[1]] = value[0]
        stages["tensors"] += time.perf_counter() - tensor_stamp
        infer_stamp = time.perf_counter()
        logits = model.session.run(None, padded)[0]
        stages["inference"] += time.perf_counter() - infer_stamp
        calls += 1
        decode_stamp = time.perf_counter()
        for index, (node, offset, words, _) in enumerate(batch):
            for hit in decode(words, logits[index]):
                hit = ner.Entity(hit.start + offset, hit.end + offset, hit.label, hit.score)
                key = (hit.start, hit.end, hit.label)
                if key not in outputs[node] or outputs[node][key].score < hit.score:
                    outputs[node][key] = hit
        stages["decode"] += time.perf_counter() - decode_stamp
    metrics = {
        "seconds": time.perf_counter() - stamp, "stages": stages, "calls": calls,
        "nodes": len(texts), "windows": len(windows),
        "words": [len(item[2]) for item in windows],
        "spans": [item[3]["span_mask"].shape[1] for item in windows],
    }
    return outputs, metrics


def compare(texts: list[str], actual: list[dict], repeats: int) -> dict:
    mismatches, largest, timings = set(), 0.0, []
    for _ in range(repeats):
        stamp = time.perf_counter()
        for index, (text, predictions) in enumerate(zip(texts, actual)):
            expected = {(e.start, e.end, e.label): e.score for e in ner.raw_recognize(text)}
            if expected.keys() != predictions.keys():
                mismatches.add(index)
            for key in expected.keys() & predictions.keys():
                largest = max(largest, abs(expected[key] - predictions[key].score))
        timings.append(time.perf_counter() - stamp)
    return {"serial_seconds": timings, "serial_median_seconds": statistics.median(timings),
            "mismatched_nodes": sorted(mismatches), "max_score_delta": largest,
            "passed": not mismatches and largest <= 1e-4}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, choices=(8, 12, 16), default=8)
    parser.add_argument("--batch-size", type=int, choices=(1, 4, 8), default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--golden", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats 须为正数")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["NER_THREADS"] = str(args.threads)
    ner.check(load=True)
    model = ner._get_model()
    report = {
        "threads": args.threads, "batch_size": args.batch_size,
        "inputs": [(item.name, item.shape) for item in model.session.get_inputs()],
        "outputs": [(item.name, item.shape) for item in model.session.get_outputs()],
        "samples": {},
    }
    for name, texts in samples(args.output.parent, args.golden).items():
        timings = []
        repetitions = args.repeats if name == "benchmark" else 1
        for _ in range(repetitions):
            actual, metrics = batch_raw(model, texts, args.batch_size)
            timings.append(metrics["seconds"])
        metrics.update(seconds=timings, median_seconds=statistics.median(timings),
                       parity=compare(texts, actual, repetitions))
        report["samples"][name] = metrics
        print(json.dumps({name: {key: value for key, value in metrics.items()
                                 if key not in ("words", "spans")}}), flush=True)
    peak_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    report.update(initial_bytes=INITIAL_BYTES, peak_bytes=peak_bytes,
                  increase_bytes=peak_bytes - INITIAL_BYTES,
                  memory_passed=peak_bytes - INITIAL_BYTES <= 2_500_000_000)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = report["memory_passed"] and all(sample["parity"]["passed"] for sample in report["samples"].values())
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
