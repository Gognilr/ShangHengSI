"""GLiNER ONNX 本地实测。保留官方分词基线，并单列中文逐字预切分变体。"""
import argparse
import json
import os
import re
import resource
import statistics
import time
from pathlib import Path

BASE_RSS = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
import onnxruntime as ort
from transformers import AutoTokenizer
from gliner.config import GLiNERConfig
from gliner.model import UniEncoderSpanGLiNER
ort.disable_telemetry_events()


class ChineseCharacterSplitter:
    def __call__(self, text):
        pattern = r"[一-鿿]|[A-Za-zÀ-ÖØ-öø-ÿĀ-ſ]+(?:['’\-][A-Za-zÀ-ÖØ-öø-ÿĀ-ſ]+)*|\d+(?:\.\d+)?|[^\s]"
        for match in re.finditer(pattern, text):
            yield match.group(), match.start(), match.end()


class GlinerRecognizer:
    def __init__(self, folder, filename, splitter, threads=2):
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(folder / filename), sess_options=options, providers=["CPUExecutionProvider"])
        tokenizer = AutoTokenizer.from_pretrained(str(folder), local_files_only=True, trust_remote_code=False)
        config = GLiNERConfig(**json.loads((folder / "gliner_config.json").read_text()))
        self.model = UniEncoderSpanGLiNER(config, tokenizer=tokenizer, model=UniEncoderSpanGLiNER.ort_model_class(session))
        if splitter == "characters":
            self.model.data_processor.words_splitter = ChineseCharacterSplitter()

    def recognize(self, text):
        output = {}
        labels = {"person": "PERSON", "organization": "ORG", "street address": "ADDR"}
        for offset in range(0, len(text), 288):
            chunk = text[offset:offset + 320]
            if not chunk.strip():
                continue
            found = self.model.predict_entities(chunk, list(labels), threshold=0.01, flat_ner=True)
            for entity in found:
                a, b = entity["start"] + offset, entity["end"] + offset
                label = labels[entity["label"]]
                key = (a, b, label)
                value = {"start": a, "end": b, "label": label, "score": float(entity["score"])}
                if key not in output or value["score"] > output[key]["score"]:
                    output[key] = value
        return sorted(output.values(), key=lambda p: (p["start"], p["end"], p["label"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--filename", default="model_int8.onnx")
    parser.add_argument("--splitter", choices=["default", "characters"], default="default")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    start = time.perf_counter()
    model = GlinerRecognizer(args.model, args.filename, args.splitter)
    load_seconds = time.perf_counter() - start
    predictions = {}
    for number, record in enumerate(corpus["records"], 1):
        predictions[record["id"]] = model.recognize(record["input"]) if record["input"].strip() else []
        if number % 100 == 0:
            print("records", number, flush=True)
    source = "\n".join(r["input"] for r in corpus["records"] if r["origin"] == "narrative")
    full = (source * (10000 // len(source) + 1))[:10000]
    model.recognize(full)
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        model.recognize(full)
        timings.append(time.perf_counter() - start)
    runtime_files = [args.filename, "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "gliner_config.json"]
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    result = {"candidate": "gliner-" + args.filename.removesuffix(".onnx") + "-" + args.splitter,
              "predictions": predictions, "offline": os.environ.get("HF_HUB_OFFLINE") == "1",
              "runtime_files": runtime_files,
              "runtime_artifact_bytes": sum((args.model / name).stat().st_size for name in runtime_files),
              "performance": {"chars": len(full), "runs_seconds": timings, "median_seconds": statistics.median(timings),
                              "load_seconds": load_seconds, "baseline_rss_bytes": BASE_RSS,
                              "peak_rss_bytes": peak, "peak_increase_bytes": peak - BASE_RSS}}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("candidate", "runtime_artifact_bytes", "performance")}))
