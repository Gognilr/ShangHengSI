"""V2-B-06a：生成规则之后的 NER 评测输入；仅在开发机 Docker 中运行。"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from app.redaction.engine import NodeIn, compute


def positions(text, value):
    return [m.span() for m in re.finditer(re.escape(value), text)]


def make_record(identifier, text, expected, keep, result, loc, split, origin):
    occupied = [(i.char_start, i.char_end) for i in result.items if i.loc == loc]
    residual = []
    partial = []
    for start, end, label in sorted(set(expected)):
        if any(a <= start and end <= b for a, b in occupied):
            continue
        if any(a < end and start < b for a, b in occupied):
            partial.append([start, end, label])
        else:
            residual.append([start, end, label])
    chars = list(text)
    for start, end in occupied:
        chars[start:end] = " " * (end - start)
    return {"id": identifier, "origin": origin, "split": split, "text": text,
            "input": "".join(chars), "expected": residual, "partial_rule_overlap": partial,
            "keep": sorted(set(keep)), "rule_occupied": occupied}


def build(root):
    samples_file = Path(__file__).with_name("samples.json")
    samples = json.loads(samples_file.read_text(encoding="utf-8"))["samples"]
    records = []
    for sample in samples:
        text = sample["text"]
        assert all(text.count(v) == 1 for v, _ in sample["entities"]), sample["id"]
        result = compute([NodeIn("F1-P1", text)], "other")
        expected = [(text.index(v), text.index(v) + len(v), label) for v, label in sample["entities"]]
        keep = [span for v in sample["must_keep"] for span in positions(text, v)]
        records.append(make_record(sample["id"], text, expected, keep, result, "F1-P1", sample["split"], "narrative"))
    golden = root / "backend/tests/fixtures/redaction_golden"
    for path in sorted(golden.glob("*.txt")):
        annotation = yaml.safe_load(path.with_suffix(".yaml").read_text(encoding="utf-8"))
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        nodes = ([NodeIn(*line.split(" ", 1)) for line in lines] if annotation.get("loc_prefixed") else
                 [NodeIn(f"F1-P{i}", line) for i, line in enumerate(lines, 1)])
        result = compute(nodes, annotation["stance"], main_prefix="F1-")
        for node in nodes:
            expected = [(a, b, entry["category"]) for entry in annotation.get("must_replace", [])
                        if entry["category"] in ("PERSON", "ORG", "ADDR")
                        for a, b in positions(node.text, entry["text"])]
            for value in annotation.get("must_suspect", []):
                if re.fullmatch(r"[\d -]+", value):
                    continue  # 银行账号不属于本轮 NER 的 PERSON / ORG / ADDR 指标。
                spans = positions(node.text, value)
                for a, b in spans:
                    reason = next((s.reason for s in result.suspects if s.loc == node.loc and
                                   s.char_start <= a and b <= s.char_end), None)
                    label = "PERSON" if reason in ("person_name", "person_like") else "ORG"
                    expected.append((a, b, label))
            keep = [span for value in annotation.get("must_keep", []) for span in positions(node.text, value)]
            records.append(make_record(f"{path.stem}:{node.loc}", node.text, expected, keep, result,
                                       node.loc, "regression", "golden"))
    return {"schema": 1, "samples_sha256": hashlib.sha256(samples_file.read_bytes()).hexdigest(),
            "policy": "不传入已替换文本；以等长空白遮盖，保留字符位置；严格同类别、同起止位置算实体召回，覆盖率另列；部分规则重叠单列，不计入完整残余实体。",
            "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/srv"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.root)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"records": len(result["records"]), "samples_sha256": result["samples_sha256"],
                      "residual_person_org": sum(e[2] in ("PERSON", "ORG") for r in result["records"] for e in r["expected"])}, ensure_ascii=False))
