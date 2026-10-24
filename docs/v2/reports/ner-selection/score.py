"""V2-B-06a：独立于模型实现的严格实体计分与校准集阈值选择。"""
import argparse
import json
from pathlib import Path

GRID = [0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999, 1.0]


def metrics(records, predictions, threshold):
    expected = hit = covered = keep_hits = high_entities = 0
    labels = {label: {"expected": 0, "exact": 0, "covered": 0} for label in ("PERSON", "ORG", "ADDR")}
    misses = []
    violations = []
    for record in records:
        accepted = [p for p in predictions.get(record["id"], []) if p["score"] >= threshold]
        high_entities += len(accepted)
        for a, b, label in record["expected"]:
            exact = any((p["start"], p["end"], p["label"]) == (a, b, label) for p in accepted)
            cover = any(p["start"] <= a and b <= p["end"] and p["label"] == label for p in accepted)
            labels[label]["expected"] += 1
            labels[label]["exact"] += int(exact)
            labels[label]["covered"] += int(cover)
            if label in ("PERSON", "ORG"):
                expected += 1
                hit += int(exact)
                covered += int(cover)
                if not exact:
                    misses.append({"id": record["id"], "text": record["text"][a:b], "label": label})
        for a, b in record["keep"]:
            overlap = [p for p in accepted if p["start"] < b and a < p["end"]]
            if overlap:
                keep_hits += 1
                violations.append({"id": record["id"], "keep": record["text"][a:b], "predictions": overlap})
    return {"expected_person_org": expected, "exact_person_org": hit,
            "exact_recall": hit / expected if expected else None,
            "coverage_recall": covered / expected if expected else None,
            "keep_overlap_count": keep_hits, "prediction_count": high_entities,
            "by_label": labels, "misses": misses, "keep_violations": violations}


def score(corpus, raw):
    records = corpus["records"]
    predictions = raw["predictions"]
    calibration = [r for r in records if r["split"] == "calibration"]
    safe = [r for r in records if r["split"] in ("calibration", "regression")]
    medium_choices = [t for t in GRID if (metrics(calibration, predictions, t)["exact_recall"] or 0) >= 0.9]
    medium = max(medium_choices) if medium_choices else GRID[0]
    high_choices = [t for t in GRID if t >= max(0.9, medium) and metrics(safe, predictions, t)["keep_overlap_count"] == 0]
    high = min(high_choices) if high_choices else 1.0
    splits = {}
    for split in ("calibration", "holdout", "regression"):
        selected = [r for r in records if r["split"] == split]
        splits[split] = {"medium": metrics(selected, predictions, medium), "high": metrics(selected, predictions, high)}
    narrative = [r for r in records if r["origin"] == "narrative"]
    all_high = metrics(records, predictions, high)
    gates = {"narrative_recall_90": (metrics(narrative, predictions, medium)["exact_recall"] or 0) >= 0.9,
             "holdout_recall_90": (splits["holdout"]["medium"]["exact_recall"] or 0) >= 0.9,
             "high_keep_zero": all_high["keep_overlap_count"] == 0,
             "ten_thousand_chars_3s": raw["performance"]["median_seconds"] <= 3,
             "memory_increase_1gb": raw["performance"]["peak_increase_bytes"] <= 1_000_000_000,
             "image_increase_1_5gb": (raw["runtime_image_increase_bytes"] <= 1_500_000_000
                                      if raw.get("runtime_image_increase_bytes") is not None else None),
             "offline": raw["offline"] is True}
    return {"candidate": raw["candidate"], "medium": medium, "high": high,
            "calibration_medium_satisfied": bool(medium_choices), "calibration_high_satisfied": bool(high_choices),
            "narrative_medium": metrics(narrative, predictions, medium), "all_high": all_high,
            "splits": splits, "gates": gates, "all_gates": all(gates.values()),
            "high_automatic_count": all_high["prediction_count"], "performance": raw["performance"],
            "runtime_artifact_bytes": raw["runtime_artifact_bytes"],
            "runtime_image_increase_bytes": raw.get("runtime_image_increase_bytes")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    report = score(json.loads(args.corpus.read_text(encoding="utf-8")),
                   json.loads(args.predictions.read_text(encoding="utf-8")))
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("candidate", "medium", "high", "gates", "all_gates")}, ensure_ascii=False))
