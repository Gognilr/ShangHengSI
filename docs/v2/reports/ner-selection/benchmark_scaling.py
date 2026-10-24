"""目标硬件讨论用的开发机 CPU 扩展补测；不替换原 2 vCPU 门禁证据。"""
import argparse
import json
import resource
import statistics
import time
from pathlib import Path

from run_gliner_onnx import BASE_RSS, GlinerRecognizer
import torch

parser = argparse.ArgumentParser()
parser.add_argument("evidence", type=Path)
parser.add_argument("--threads", type=int, required=True)
args = parser.parse_args()
torch.set_num_threads(args.threads)
torch.set_num_interop_threads(1)
corpus = json.loads((args.evidence / "corpus.json").read_text())
source = "\n".join(r["input"] for r in corpus["records"] if r["origin"] == "narrative")
text = (source * (10000 // len(source) + 1))[:10000]
start = time.perf_counter()
model = GlinerRecognizer(args.evidence / "exported/gliner", "model.onnx", "characters", args.threads)
load = time.perf_counter() - start
model.recognize(text)
times = []
for _ in range(3):
    start = time.perf_counter()
    predictions = model.recognize(text)
    times.append(time.perf_counter() - start)
    print("run", len(times), times[-1], flush=True)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
result = {"threads": args.threads, "torch_threads_explicit": True,
          "chars": len(text), "load_seconds": load, "runs_seconds": times,
          "median_seconds": statistics.median(times), "baseline_rss_bytes": BASE_RSS,
          "peak_rss_bytes": peak, "peak_increase_bytes": peak - BASE_RSS,
          "prediction_count": len(predictions), "scope": "开发机补测，非目标 28 vCPU 主机验收，非原门槛替代"}
(args.evidence / f"scaling-{args.threads}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False), flush=True)
