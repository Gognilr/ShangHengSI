"""V35-E-01：初审结论只分严重、提示两档；“不是问题”可撤销。样本全部虚构。"""
from app.bid.conclusion import build


def item(id_, kind, type_, level, bidders, rows):
    return {"id": id_, "kind": kind, "type": type_, "level": level, "bidder_ids": list(bidders),
            "rows": [{"label": r} for r in rows], "article": "（四）", "title": id_, "source": "local", "requirement_id": None}


def test_severe_hint_cases_and_dismissed():
    view = {
        "items": [
            item("id", "clue", "props", "strong", "ab", ["PDF 原始文件标识", "生成软件相同"]),
            item("image", "clue", "image", "medium", "bc", ["图片"]),
            item("author", "clue", "props", "strong", "bc", ["作者 / 最后保存者"]),
            item("software", "clue", "props", "weak", "de", ["生成软件相同"]),
            item("price", "issue", "multi_total", "hint", "d", ["不同范围报价"]),
            item("cert", "issue", "cert_expired", "general", "d", ["证书有效期"]),
        ],
        "requirements": [
            {"id": "r2", "method": "model", "group": "veto", "text": "提供承诺书", "seq": 2},
            {"id": "r3", "method": "model", "group": "substantive", "text": "提供检测报告", "seq": 3},
        ],
        "cells": [{"requirement_id": "r2", "bidder_id": "e", "verdict": "no", "cite": "verified", "source": "model"},
                  {"requirement_id": "r3", "bidder_id": "a", "verdict": "missing", "cite": None, "source": "model"}],
        "signing": [{"bidder_id": "c", "file_id": "f", "page": 3, "loc": None, "title": "授权委托书", "file": "丙.pdf", "mark": "missing"}],
    }
    names = dict(zip("abcde", "甲乙丙丁戊"))
    out = build(view, names, model_state=None)
    case, = out["cases"]
    assert case["bidder_ids"] == list("abc") and case["strong_pairs"] == [["a", "b"]] and case["level"] == "severe"
    severe = {e["id"] for e in out["severe"]}
    assert severe == {"cell:r2:e", "item:cert", "sign:f:3", case["id"]}  # ★ / 否决不响应、证书过期、缺章、串标强证据
    assert {e["id"] for e in out["hint"]} == {"item:software", "item:price", "cell:r3:a"}
    assert [b["severe"] for b in out["bidders"]] == [1, 1, 2, 1, 1]
    assert "可交模型补查" in out["sub"]  # r3 还有 4 家没有模型判断

    after = build(view, names, dismissed={"cell:r2:e", "item:software"})
    assert {e["id"] for e in after["dismissed"]} == {"cell:r2:e", "item:software"}
    assert after["bidders"][4]["severe"] == 0 and "cell:r2:e" not in {e["id"] for e in after["severe"]}
