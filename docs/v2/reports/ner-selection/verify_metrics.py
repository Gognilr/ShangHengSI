"""独立核对严格实体边界、覆盖率、保留项误替换与残余分母。"""
from score import metrics, score

record = {"id": "one", "text": "甲乙丙丁", "expected": [[0, 2, "PERSON"]], "keep": [[2, 4]],
          "split": "calibration", "origin": "narrative"}
exact = {"one": [{"start": 0, "end": 2, "label": "PERSON", "score": 0.95}]}
assert metrics([record], exact, 0.9)["exact_recall"] == 1
assert metrics([record], exact, 0.99)["exact_recall"] == 0
wide = {"one": [{"start": 0, "end": 4, "label": "PERSON", "score": 0.95}]}
assert metrics([record], wide, 0.9)["exact_recall"] == 0
assert metrics([record], wide, 0.9)["coverage_recall"] == 1
assert metrics([record], wide, 0.9)["keep_overlap_count"] == 1
wrong = {"one": [{"start": 0, "end": 2, "label": "ORG", "score": 0.95}]}
assert metrics([record], wrong, 0.9)["exact_recall"] == 0
raw = {"candidate": "fake", "predictions": exact, "offline": True, "runtime_artifact_bytes": 100,
       "performance": {"median_seconds": 1, "peak_increase_bytes": 100}}
report = score({"records": [record]}, raw)
assert report["medium"] == 0.95
assert report["gates"]["holdout_recall_90"] is False
assert report["gates"]["image_increase_1_5gb"] is None
assert report["all_gates"] is False
raw["runtime_image_increase_bytes"] = 1_500_000_001
assert score({"records": [record]}, raw)["gates"]["image_increase_1_5gb"] is False
raw["runtime_image_increase_bytes"] = 1_500_000_000
assert score({"records": [record]}, raw)["gates"]["image_increase_1_5gb"] is True
print("strict boundary, coverage, keep overlap, label, calibration and missing-holdout checks passed")
