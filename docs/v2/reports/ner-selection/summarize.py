"""汇总原始预测与实建镜像大小；无实建证据时镜像门禁标为未验证。"""
import argparse
import hashlib
import json
from pathlib import Path

from score import score

parser = argparse.ArgumentParser()
parser.add_argument("evidence", type=Path)
args = parser.parse_args()
root = args.evidence
corpus = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
summary = []
for name in ("bert-int8", "bert-fp32", "gliner-default", "gliner-characters", "gliner-fp32-characters"):
    path = root / f"{name}.predictions.json"
    if not path.exists():
        continue
    raw = json.loads(path.read_text(encoding="utf-8"))
    if name == "bert-int8":
        raw["runtime_image_increase_bytes"] = json.loads((root / "bert-runtime-image-size.json").read_text(encoding="utf-8-sig"))["increase"]
    elif name in ("gliner-default", "gliner-characters"):
        raw["runtime_image_increase_bytes"] = json.loads((root / "gliner-runtime-image-size.json").read_text(encoding="utf-8-sig"))["increase"]
    report = score(corpus, raw)
    (root / f"{name}.score.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary.append({"name": name, "candidate": report["candidate"], "medium": report["medium"], "high": report["high"],
                    "narrative": report["narrative_medium"]["exact_recall"],
                    "holdout": report["splits"]["holdout"]["medium"]["exact_recall"],
                    "high_keep": report["all_high"]["keep_overlap_count"],
                    "high_count": report["high_automatic_count"], "performance": report["performance"],
                    "image_increase": report["runtime_image_increase_bytes"], "gates": report["gates"],
                    "predictions_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
