"""V34-B-03、B-04：本地检查（混装、联系信息、属性、图片；相似段落、报价、符合项、扫描页延后识别）；样本全部虚构，不调用模型。"""
import uuid

import pytest
from sqlalchemy import func, select

from app.bid import fileprops
from app.db.models import LlmCall
from tests.fixtures.bid.samples import AGENT_PHONE, BIDDERS, bid, bid2, pdf, scan_bid, tender_with_logo
from tests.review_support import env  # noqa: F401
from tests.test_bid_projects import BODY, ROOT, drain, owner  # noqa: F401
from tests.test_bid_requirements import run_bid_jobs

pytestmark = pytest.mark.usefixtures("env")


def test_local_check_clues_and_baseline(owner, db_sessionmaker):
    c, h, _ = owner
    base = ROOT + "/" + c.post(ROOT, headers=h, json=BODY).json()["id"]
    c.post(base + "/files", headers=h, files={"files": ("虚构招标文件.docx", tender_with_logo())})
    drain(db_sessionmaker, base)
    files = [("files", (f"投标文件{i}.docx", bid(i))) for i in range(3)]
    r = c.post(base + "/files", headers=h, files=files, data={"groups": __import__("json").dumps(list(BIDDERS), ensure_ascii=False)})
    assert r.status_code == 200, r.text
    drain(db_sessionmaker, base)
    assert c.post(base + "/check", headers=h).status_code == 409  # 要求清单未确认
    assert c.post(base + "/confirm", headers=h).json()["stage"] == "ready"
    assert c.post(base + "/check", headers=h).json()["stage"] == "checking"
    run_bid_jobs(db_sessionmaker, base)

    project = c.get(base).json()
    assert project["stage"] == "checked" and project["check"]["state"] == "done"
    ids = {b["name"]: b["id"] for b in project["bidders"]}
    a, b, x = (ids[n] for n in BIDDERS)
    result = c.get(base + "/check").json()
    by = {(i["type"], tuple(i["bidder_ids"])): i for i in result["items"]}

    contact = by[("contact", (a, b))]
    values = {row["label"]: row["value"] for row in contact["rows"]}
    assert contact["level"] == "strong" and "（三）" in contact["article"]
    assert values["电话"] == "13912345678" and values["项目经理"] == "周示例"
    assert not any(AGENT_PHONE in row["value"] for i in result["items"] if i["type"] == "contact" for row in i["rows"])  # 基线
    assert by[("props", (a, b))]["level"] == "strong"  # 作者相同；python-docx 模板自带的 rsid 已被招标文件剔除
    assert not any(i["type"] == "props" and x in i["bidder_ids"] for i in result["items"])
    image = by[("image", (a, b))]
    assert image["level"] == "medium" and len(image["rows"]) == 1  # 招标文件里的标识图片不报
    assert by[("mixed", (x, a))]["level"] == "strong" and by[("mixed", (x, a))]["article"] == "（五）"
    assert {i["type"] for i in result["items"]} == {"contact", "props", "image", "mixed"}
    assert result["coverage"]
    assert c.post(base + "/bidders", headers=h, json={"name": "虚构丁"}).status_code == 409  # 开始初审后材料锁定
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 0


def test_pdf_properties():
    props, _ = fileprops.collect("pdf", pdf(encrypted=False), [])
    assert props and len(props["pdf_id"]) == 32 and props.get("producer")


def _checked(c, h, sm, uploads):
    import json
    base = ROOT + "/" + c.post(ROOT, headers=h, json=BODY).json()["id"]
    c.post(base + "/files", headers=h, files={"files": ("虚构招标文件.docx", tender_with_logo())})
    drain(sm, base)
    r = c.post(base + "/files", headers=h, files=[("files", u) for u in uploads],
               data={"groups": json.dumps(list(BIDDERS[:len(uploads)]), ensure_ascii=False)})
    assert r.status_code == 200, r.text
    drain(sm, base)
    assert c.post(base + "/confirm", headers=h).json()["stage"] == "ready"
    assert c.post(base + "/check", headers=h).json()["stage"] == "checking"
    _jobs(sm, base, "check")
    project = c.get(base).json()
    return base, project, [b["id"] for b in project["bidders"]]


def _jobs(sm, base, stage):
    from app.bid.extract import handle_bid
    from app.db.models import Job
    with sm() as db:
        for job in db.scalars(select(Job).where(Job.type == "bid", Job.payload["task_id"].astext == base.rsplit("/", 1)[-1],
                                                Job.payload["stage"].astext == stage, Job.status == "queued")).all():
            job.status = "done"
            db.commit()
            handle_bid(db, job.payload)


def test_local_check_two_similar_pricing_compliance(owner, db_sessionmaker):
    """V34-B-04：相似段落、报价规律与确定性符合项；照抄招标文件的句子不报。"""
    c, h, _ = owner
    base, project, (a, b, x) = _checked(c, h, db_sessionmaker, [(f"投标文件{i}.docx", bid2(i)) for i in range(3)])
    assert project["stage"] == "checked"
    result = c.get(base + "/check").json()
    assert result["steps"] == ["clues", "pricing", "similar"]
    clues = {(i["type"], tuple(i["bidder_ids"])): i for i in result["items"] if i["kind"] == "clue"}
    similar = clues[("similar", (a, b))]
    assert similar["level"] == "strong" and similar["article"] == "（四）"
    assert not any(i["type"] == "similar" and x in i["bidder_ids"] for i in result["items"])  # 照抄招标文件的句子已剔除
    assert "两小时内响应" in similar["rows"][0]["left"]["text"]
    seg = similar["map"]["segs"][0]  # 位置条（V34-B-06d）：两侧句序范围落在各自总句数内
    assert 0 <= seg["a"][0] <= seg["a"][1] < similar["map"]["n"][a] and 0 <= seg["b"][0] <= seg["b"][1] < similar["map"]["n"][b]
    assert {t["bidder_id"] for t in result["timeline"]} >= {a, b}
    assert clues[("pricing", (a, b))]["level"] == "strong" and "1.03" in clues[("pricing", (a, b))]["title"]
    assert not any(i["type"] == "pricing" and x in i["bidder_ids"] for i in result["items"])
    issues = {(i["type"], i["bidder_ids"][0]): i for i in result["items"] if i["kind"] == "issue"}
    assert issues[("uppercase", b)]["level"] == "general"
    assert issues[("validity", b)]["level"] == "veto" and "60" in issues[("validity", b)]["title"]
    for kind in ("multi_total", "ceiling", "deposit"):
        assert issues[(kind, x)]["level"] == "veto", kind
    assert issues[("ceiling", x)]["requirement_id"]  # 关联到“本地规则”要求
    assert issues[("cert_expired", x)]["level"] == "general"
    assert not any(bidder == a for _t, bidder in issues)  # 甲无问题
    cells = {}
    for cell in result["cells"]:
        cells.setdefault(cell["bidder_id"], set()).add(cell["verdict"])
    assert cells[a] == {"yes"} and "no" in cells[x]
    pricing = result["pricing"]
    assert pricing["bidders"][a]["total"] == "2,616,000.00" and pricing["bidders"][x]["totals_found"] == ["4,108,000.00", "4,180,000.00"]
    # 多个总价按最高者给出超限差额（V34Q-25），over_ceiling 仍为空
    assert pricing["bidders"][x]["over_ceiling"] is None and pricing["bidders"][x]["over_ceiling_max"] == "380,000.00"
    assert len(pricing["items"]) == 6 and pricing["items"][0]["prices"][b] == "123,600.00"
    # 最高 / 最低（V34Q-27）：多个总价的一家不参加比较
    assert pricing["bidders"][a]["mark"] == "lowest" and pricing["bidders"][b]["mark"] == "highest"
    assert pricing["bidders"][b]["above_lowest"] == "78,480.00" and pricing["bidders"][b]["above_lowest_pct"] == "3.00%"
    assert pricing["bidders"][x]["mark"] is None and pricing["bidders"][x]["not_compared"] == "多个总价，未参加比较"
    assert pricing["compare"]["n"] == 2


def test_total_compare_low_price_and_pattern():
    """V34-B-06b：未填限价时也比较最高 / 最低；低于其余各家平均 20% 以上提示；三家总价等差、等比记线索。"""
    from app.bid import pricing
    from app.bid.compliance import BidNode

    def run(totals):
        nodes = {i: [BidNode(f"p{i}", f"投标总价：人民币 {t} 元。", i, None)] for i, t in enumerate(totals)}
        return pricing.analyze(list(nodes), nodes, None, "CNY", lambda n: {"loc": n.loc})

    analysis, clues, facts = run(["1,000,000.00", "1,100,000.00", "1,200,000.00", "700,000.00"])
    rows = analysis["bidders"]
    assert rows["2"]["mark"] == "highest" and rows["3"]["mark"] == "lowest" and rows["3"]["above_lowest"] is None
    assert (rows["0"]["vs_mean"], rows["1"]["vs_mean"], rows["3"]["vs_mean"]) == ("0.00%", "+10.00%", "-30.00%")
    assert rows["0"]["over_ceiling"] is None and "over_ceiling_max" not in rows["0"]  # 无限价不出限价字段
    assert facts[3]["low_price"]["below_pct"] == "36.36%" and facts[0]["low_price"] is None
    assert [c["ids"] for c in clues] == [(0, 1, 2)] and "相差 100,000.00" in clues[0]["title"]
    _a, clues, _f = run(["1,000,000.00", "1,030,000.00", "1,060,900.00"])
    assert len(clues) == 1 and "1.03 倍" in clues[0]["title"] and clues[0]["level"] == "medium"
    _a, clues, _f = run(["1,000,000.00", "1,030,000.00", "1,070,000.00"])
    assert not clues


def test_total_labels_fixture():
    """报价常见结构；不调用模型，金额与文本全部虚构。"""
    import json
    from decimal import Decimal
    from pathlib import Path
    from app.bid import pricing
    from app.bid.compliance import BidNode

    cases = json.loads((Path(__file__).parent / 'fixtures/bid/pricing_labels.json').read_text(encoding='utf-8'))
    for case in cases:
        nodes = [BidNode(row[2] if len(row) > 2 else f'F1-PG{row[1]}-B{i}', row[0], 1, None,
                         page=row[1], kind='table_cell' if len(row) > 2 else 'ocr_block')
                 for i, row in enumerate(case['nodes'])]
        assert {v for v, _c, _n in pricing.labelled_totals(nodes)} == {Decimal(v) for v in case['totals']}, case['name']


def test_scanned_pages_deferred_and_added(owner, db_sessionmaker, monkeypatch):
    """投标文件的扫描页延后识别：先出本地检查，识别后补入节点并重跑检查。"""
    from app.parsing import ocr as ocr_mod
    calls = []
    monkeypatch.setattr(ocr_mod, "recognize", lambda img: calls.append(1) or [ocr_mod.OcrLine("扫描页上的虚构文字内容", [100, 100, 900, 160], 0.95)])
    c, h, _ = owner
    base, project, (a, b) = _checked(c, h, db_sessionmaker, [(f"投标文件{i}.pdf", scan_bid(f"FICTIONAL BID {i}")) for i in range(2)])
    assert not calls and project["check"]["ocr"] == {"done": 0, "total": 2}
    assert any("扫描页识别中 0 / 2" in n for n in c.get(base + "/check").json()["coverage"])
    _jobs(db_sessionmaker, base, "ocr")
    project = c.get(base).json()
    assert len(calls) == 2 and project["check"]["ocr"] == {"done": 2, "total": 2}
    assert any("已用 OCR 识别" in w for f in project["files"] for w in f["warnings"])
    assert not any("识别中" in n for n in c.get(base + "/check").json()["coverage"])  # 重跑过检查


def test_review_reopen_and_signing(owner, db_sessionmaker):
    """V34-B-06：核对状态与备注在返回修改材料、重新初审后保留；单元格详情、人工核对单元格、签章页标注、两两比值。"""
    c, h, _ = owner
    base, _project, (a, b, x) = _checked(c, h, db_sessionmaker, [(f"投标文件{i}.docx", bid2(i)) for i in range(3)])
    result = c.get(base + "/check").json()
    item = next(i for i in result["items"] if i["type"] == "validity")
    assert c.patch(f"{base}/items/{item['id']}", headers=h, json={"review_status": "verified", "note": "已核对原件第 3 页"}).json() == \
        {"review_status": "verified", "note": "已核对原件第 3 页"}
    cell = next(cl for cl in result["cells"] if cl["verdict"] == "no")
    key = {"requirement_id": cell["requirement_id"], "bidder_id": cell["bidder_id"]}
    assert c.put(base + "/cells", headers=h, json={**key, "review_status": "excluded"}).status_code == 200
    detail = c.get(base + "/cell", headers=h, params=key).json()
    assert detail["cell"]["review_status"] == "excluded" and detail["requirement"]["text"] and detail["places"][0]["text"]
    manual = next(r for r in result["requirements"] if r["method"] == "manual")
    assert c.put(base + "/cells", headers=h, json={"requirement_id": manual["id"], "bidder_id": a, "note": "签字齐全"}).status_code == 200
    model_row = next((r for r in result["requirements"] if r["method"] == "model"), None)
    if model_row:  # 模型判断行在没有结果前不能标注
        assert c.put(base + "/cells", headers=h, json={"requirement_id": model_row["id"], "bidder_id": a, "note": "x"}).status_code == 409
    assert any(p["n"] >= 5 and p["cv"] for p in result["pricing"]["pairs"])  # 未构成线索的一对也给比值
    sign = result["signing"]
    assert sign and all(s["title"] for s in sign)
    s0 = sign[0]
    assert c.put(base + "/signing", headers=h, json={"file_id": s0["file_id"], "page": s0["page"], "loc": s0["loc"], "mark": "missing"}).status_code == 200

    # 批量核对（V34-B-06c）：一次事务；任一项不属于本项目则全部不改
    calc = [i["id"] for i in result["items"] if i["type"] in ("uppercase", "sum_total", "row_calc", "sum_rows", "multi_total", "ceiling")]
    refs = [{"requirement_id": manual["id"], "bidder_id": bid} for bid in (a, b, x)]
    signs = [{k: s[k] for k in ("file_id", "page", "loc")} for s in sign[1:]]
    bad = c.post(base + "/review", headers=h, json={"items": [str(uuid.uuid4())], "cells": refs, "review_status": "verified"})
    assert bad.status_code == 404
    assert all(cl["review_status"] == "pending" for cl in c.get(base + "/check").json()["cells"] if cl["requirement_id"] == manual["id"])
    out = c.post(base + "/review", headers=h, json={"items": calc, "cells": refs + refs[:1], "review_status": "verified",
                                                  "signing": signs, "mark": "stamped"}).json()
    assert out["cells"] == 3 and all(out["marks"][f"{s['file_id']}:{s['page'] if s['page'] is not None else s['loc']}"] == "stamped" for s in signs)
    now = c.get(base + "/check").json()
    assert sum(cl["requirement_id"] == manual["id"] and cl["review_status"] == "verified" for cl in now["cells"]) == 3
    assert all(i["review_status"] == "verified" for i in now["items"] if i["id"] in calc)

    assert c.post(base + "/reopen", headers=h).json()["stage"] == "ready"
    assert c.post(base + "/check", headers=h).json()["stage"] == "checking"
    _jobs(db_sessionmaker, base, "check")
    again = c.get(base + "/check").json()
    same = next(i for i in again["items"] if i["type"] == "validity")
    assert same["review_status"] == "verified" and same["note"] == "已核对原件第 3 页"
    by = {(cl["requirement_id"], cl["bidder_id"]): cl for cl in again["cells"]}
    assert by[(cell["requirement_id"], cell["bidder_id"])]["review_status"] == "excluded"
    assert by[(manual["id"], a)]["note"] == "签字齐全"
    assert next(s for s in again["signing"] if s["loc"] == s0["loc"])["mark"] == "missing"
    other = c.post(ROOT, headers=h, json=BODY).json()["id"]  # 别的项目的条目 404
    assert c.patch(f"{ROOT}/{other}/items/{item['id']}", headers=h, json={"review_status": "pending"}).status_code == 404
