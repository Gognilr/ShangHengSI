"""ONNX CPU BERT 实测：滑窗覆盖全文；包含分词 / 解码耗时，不加载 PyTorch。"""
import argparse
import json
import os
import resource
import statistics
import time
from pathlib import Path

BASE_RSS = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
ort.disable_telemetry_events()


class BertRecognizer:
    def __init__(self, folder, filename):
        self.tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=384, stride=48)
        self.labels = json.loads((folder / "config.json").read_text())["id2label"]
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(folder / filename), sess_options=options,
                                            providers=["CPUExecutionProvider"])

    def recognize(self, text):
        encoded = self.tokenizer.encode(text)
        output = {}
        for chunk in [encoded, *encoded.overflowing]:
            values = {"input_ids": chunk.ids, "attention_mask": chunk.attention_mask,
                      "token_type_ids": chunk.type_ids}
            inputs = {v.name: np.array([values[v.name]], dtype=np.int64) for v in self.session.get_inputs()}
            logits = self.session.run(None, inputs)[0][0]
            exp = np.exp(logits - logits.max(axis=-1, keepdims=True))
            probabilities = exp / exp.sum(axis=-1, keepdims=True)
            active = None

            def finish():
                nonlocal active
                if active:
                    key = (active["start"], active["end"], active["label"])
                    if key not in output or output[key]["score"] < active["score"]:
                        output[key] = active
                active = None

            for i, (start, end) in enumerate(chunk.offsets):
                label_id = int(np.argmax(probabilities[i]))
                tag = self.labels[str(label_id)]
                if start == end or tag == "O" or tag.endswith("TIME"):
                    finish()
                    continue
                prefix, label = tag.split("-", 1)
                label = {"PER": "PERSON", "ORG": "ORG", "LOC": "ADDR"}.get(label)
                if label is None:
                    finish()
                    continue
                score = float(probabilities[i, label_id])
                if active and prefix == "I" and active["label"] == label:
                    active["end"] = end
                    active["score"] = min(active["score"], score)
                else:
                    finish()
                    active = {"start": start, "end": end, "label": label, "score": score}
            finish()
        return sorted(output.values(), key=lambda p: (p["start"], p["end"], p["label"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--filename", default="model_int8.onnx")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    started = time.perf_counter()
    model = BertRecognizer(args.model, args.filename)
    load_seconds = time.perf_counter() - started
    predictions = {r["id"]: model.recognize(r["input"]) if r["input"].strip() else [] for r in corpus["records"]}
    source = "\n".join(r["input"] for r in corpus["records"] if r["origin"] == "narrative")
    full = (source * (10000 // len(source) + 1))[:10000]
    model.recognize(full)
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        model.recognize(full)
        timings.append(time.perf_counter() - start)
    runtime_files = [args.filename, "tokenizer.json", "config.json"]
    result = {"candidate": "bert4ner-" + args.filename.removesuffix(".onnx"), "predictions": predictions,
              "offline": os.environ.get("HF_HUB_OFFLINE") == "1", "runtime_files": runtime_files,
              "runtime_artifact_bytes": sum((args.model / name).stat().st_size for name in runtime_files),
              "performance": {"chars": len(full), "runs_seconds": timings, "median_seconds": statistics.median(timings),
                              "load_seconds": load_seconds, "baseline_rss_bytes": BASE_RSS,
                              "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                              "peak_increase_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 - BASE_RSS}}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("candidate", "runtime_artifact_bytes", "performance")}))
