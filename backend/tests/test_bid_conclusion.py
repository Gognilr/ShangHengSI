from app.bid.conclusion import build


def item(id_, kind, type_, level, bidders, rows):
    return {"id": id_, "kind": kind, "type": type_, "level": level, "bidder_ids": list(bidders),
            "rows": [{"label": r} for r in rows], "article": "（四）", "title": id_, "source": "local",
            "review_status": "pending", "requirement_id": None}


def test_layers_cases_and_site_checklist():
    view = {
        "items": [
            item("id", "clue", "props", "strong", "ab", ["PDF 原始文件标识", "生成软件相同"]),
            item("image", "clue", "image", "medium", "bc", ["图片"]),
            item("author", "clue", "props", "strong", "bc", ["作者 / 最后保存者"]),
            item("software", "clue", "props", "weak", "de", ["生成软件相同"]),
            item("price", "issue", "multi_total", "hint", "d", ["不同范围报价"]),
        ],
        "requirements": [
            {"id": "r1", "method": "manual", "group": "qualification", "text": "查验证书原件", "seq": 1},
            {"id": "r2", "method": "model", "group": "veto", "text": "提供承诺书", "seq": 2},
        ],
        "cells": [{"requirement_id": "r2", "bidder_id": "e", "verdict": "no", "cite": "verified", "review_status": "pending"}],
    }
    before = build(view, dict(zip("abcde", "甲乙丙丁戊")), model_state="done")
    assert len(before["cases"]) == 1
    case = before["cases"][0]
    assert case["bidder_ids"] == list("abc")
    assert case["certain_pairs"] == [["a", "b"]]
    assert case["tier"] == "verify"
    assert [e["tier"] for e in case["evidence"]] == ["certain", "verify", "verify", "reference"]
    assert [b["tier"] for b in before["bidders"]] == ["certain", "certain", "verify", None, "verify"]
    assert [i["id"] for i in before["verify"]] == ["cell:r2:e", case["id"]]
    assert {i["id"] for i in before["reference"]} == {"item:software", "item:price"}
    assert [r["id"] for r in before["site"]] == ["r1"]

    after = build(view, dict(zip("abcde", "甲乙丙丁戊")), second={"cell:r2:e": {"outcome": "confirmed"}})
    assert [i["id"] for i in after["certain"]] == ["cell:r2:e"]
