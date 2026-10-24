"""断网核对本轮原始文件、退出码、容器约束及计分快照，不重跑推理。"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

from score import score

parser = argparse.ArgumentParser()
parser.add_argument("evidence", type=Path)
args = parser.parse_args()
root = args.evidence
scripts = Path(__file__).parent
audit = json.loads((scripts / "audit.json").read_text(encoding="utf-8"))
corpus = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
assert hashlib.sha256((root / "corpus.json").read_bytes()).hexdigest() == audit["corpus_sha256"]
assert corpus["samples_sha256"] == hashlib.sha256((scripts / "samples.json").read_bytes()).hexdigest()
assert len(corpus["records"]) == 391
assert sum(e[2] in ("PERSON", "ORG") for r in corpus["records"] for e in r["expected"]) == 45
for receipt in audit["downloads"]:
    path = root / "models" / receipt["model"].replace("/", "--") / receipt["file"]
    assert path.stat().st_size == receipt["bytes"], str(path)
    with path.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == receipt["sha256"], str(path)
assert sum(r["bytes"] for r in audit["downloads"]) == 1_567_026_222
for receipt in audit["exported_hashes"]:
    relative = receipt["path"].replace("\\", "/").split("/exported/", 1)[1]
    path = root / "exported" / relative
    assert path.stat().st_size == receipt["bytes"]
    with path.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == receipt["sha256"]
for entry in audit["summary"]:
    name = entry["name"]
    path = root / f"{name}.predictions.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["predictions_sha256"]
    raw = json.loads(path.read_text(encoding="utf-8"))
    if entry["image_increase"] is not None:
        raw["runtime_image_increase_bytes"] = entry["image_increase"]
    result = score(corpus, raw)
    assert result == audit["scores"][name], name
    assert result["all_gates"] is False, name
    host = audit["hostconfigs"][name]
    assert host["NetworkMode"] == "none" and host["NanoCpus"] == 2_000_000_000
    assert int((root / f"{name}.exit").read_text(encoding="utf-8-sig")) == 0
for name, code in audit["exit_codes"].items():
    assert code == (1 if name == "export-bert" else 0), name
for entry in audit.get("scaling", []):
    threads = entry["threads"]
    raw = json.loads((root / f"scaling-{threads}.json").read_text())
    assert raw == {k: v for k, v in entry.items() if k != "hostconfig"}
    assert entry["hostconfig"]["NetworkMode"] == "none"
    assert entry["hostconfig"]["NanoCpus"] == threads * 1_000_000_000
    assert entry["chars"] == 10000 and len(entry["runs_seconds"]) == 3
    assert entry["torch_threads_explicit"] is True
for path in scripts.glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("download hashes, frozen corpus, raw prediction hashes, score snapshots, offline/CPU quotas, exit codes and Python syntax verified")
