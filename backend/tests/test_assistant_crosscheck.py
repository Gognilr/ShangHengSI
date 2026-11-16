"""V34-A-08：单证核对本机比较，样本见 fixtures/crosscheck/。"""
import json
from pathlib import Path

import pytest

from app.assistant.crosscheck import build
from app.checks.model import CheckFile, CheckNode

CASES = sorted((Path(__file__).parent / "fixtures/crosscheck").glob("*.json"))


@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
def test_crosscheck_fixture(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    nodes = [CheckNode(loc, text, int(seq), None) for seq, items in case["files"].items() for loc, text in items]
    files = [CheckFile(int(seq), seq == "1", "docx", None, []) for seq in case["files"]]
    result = build(nodes, files)
    statuses = {row["field"]: row["status"] for row in result["rows"]}
    assert {field: statuses.get(field) for field in case["expect"]} == case["expect"], statuses
    assert len(result["findings"]) == case["findings"], result["findings"]
