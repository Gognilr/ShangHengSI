"""V34-A-08：单证核对本机比较，样本见 fixtures/crosscheck/。"""
import json
from pathlib import Path

import pytest

from app.assistant.crosscheck import build, missing, summary
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


def test_credit_rule_boundaries():
    case = json.loads((Path(__file__).parent / "fixtures/crosscheck/credit_rules.json").read_text(encoding="utf-8"))
    files = [CheckFile(int(seq), False, "docx", None, []) for seq in case["files"]]

    def check(changes):
        nodes = [CheckNode(loc, changes.get(loc, text), int(seq), None)
                 for seq, items in case["files"].items() for loc, text in items]
        return build(nodes, files)

    result = check({})
    assert [text.split("（")[0] for text in result["findings"]] == [
        "提单日晚于最迟装运期", "保单签发日晚于提单日", "发票金额超过信用证金额上限",
    ]
    assert all(loc in result["findings"][2] for loc in ("F1-T1-R1-C2", "F1-P4", "F4-P3"))
    assert not result["notes"] and "10,500.01" not in summary(result)

    equal = {"F2-P3": "On Board Date: 15 Nov 2026", "F3-P3": "保单日期：2026/11/15",
             "F4-P3": "Invoice Amount: USD 10,500.00"}
    result = check(equal)
    assert not result["findings"] and not result["notes"]
    assert missing(result["rows"], result["files"]) == {"amount": [1, 2, 3], "ship_date": [1, 3, 4]}
    result = check({**equal, "F1-P4": ""})
    assert len(result["findings"]) == 1 and "未列明溢短装比例" in result["findings"][0]
    for changes in ({"F4-P3": "Total Amount: EUR 10,500.01"}, {"F1-P4": "溢短装比例：见补充条款"},
                    {"F1-P4": "溢短装比例："},
                    {"F4-P3": "合同金额：USD 10,500.01"},
                    {"F4-P2": "L/C No.: LC-SAMPLE-02"}):
        result = check({**equal, **changes})
        assert not result["findings"] and len(result["notes"]) == 1
