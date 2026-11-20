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
    from app.bid.checks import _authors
    assert _authors({'creator': 'Fictive PDF Writer', 'author': '虚构作者'}, 'pdf') == {'虚构作者'}
    assert not _authors({'creator': 'Fictive PDF Writer'}, 'pdf')


def _checked(c, h, sm, uploads, *, tender_bytes=None, invitees=()):
    import json
    base = ROOT + "/" + c.post(ROOT, headers=h, json=BODY).json()["id"]
    c.post(base + "/files", headers=h, files={"files": ("虚构招标文件.docx", tender_bytes or tender_with_logo())})
    drain(sm, base)
    if invitees:
        assert c.put(base + "/invitees", headers=h, json={"names": list(invitees)}).status_code == 200
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


def test_shared_external_names_typos_and_fractional_tails(owner, db_sessionmaker):
    """三类线索从虚构原件进入证据链，重复的名称只计一类。"""
    from decimal import Decimal
    from io import BytesIO

    from docx import Document

    from app.bid.conclusion import build
    from app.bid.model import second_targets
    from tests.fixtures.bid.samples import _quote_table
    from tests.test_bid_report import _text

    expected = "本项目要求供应商在收到通知以后提交设备运行记录并确认交接清单。"
    typo = expected.replace("清单", "单清")
    copied = "投标文件应在指定时间内连续编页装订并按招标文件要求提交。"
    shared_invitee = "虚构双河仪器有限公司"
    owner_name = "虚构临江供热有限公司"
    old_project = "虚构龙湾供热改造工程"
    current_project = "虚构北港仓库新建工程"
    current_owner = "虚构北港物资有限公司"

    def content(project_name, organization, sentence, prices=(), *, cover_table=False):
        doc = Document()
        if cover_table:
            cover = doc.add_table(rows=2, cols=2)
            for row, (label, value) in enumerate((("项目名称", project_name), ("招标人：", organization))):
                cover.cell(row, 0).text = label
                cover.cell(row, 1).text = value
        else:
            doc.add_paragraph(f"项目名称：{project_name}")
            doc.add_paragraph(f"招标人：{organization}")
        doc.add_paragraph(f"项目名称：{current_project}")
        doc.add_paragraph(f"业主单位：{current_owner}")
        if prices:
            doc.add_paragraph(f"业主单位：{shared_invitee}")
        doc.add_paragraph(sentence)
        doc.add_paragraph(copied)
        if prices:
            _quote_table(doc, [Decimal(p) for p in prices])
        stream = BytesIO()
        doc.save(stream)
        return stream.getvalue()

    c, h, _ = owner
    a_price = ["103.27", "207.38", "317.62", "402.13", "517.27", "691.41"]
    b_price = ["167.27", "231.38", "365.62", "433.13", "653.27", "797.59"]
    base, _, ids = _checked(c, h, db_sessionmaker, [
        ("甲.docx", content(old_project, owner_name, typo, a_price)),
        ("乙.docx", content(old_project, owner_name, typo, b_price, cover_table=True)),
        ("丙.docx", content(current_project, current_owner, expected, ["121.99"] * 6)),
    ], tender_bytes=content(current_project, current_owner, expected), invitees=(shared_invitee,))
    view = c.get(base + "/check").json()
    labels = {"外部项目名称", "外部业主名称", "同句同错", "分项单价小数尾数共现"}
    candidates = [i for i in view["items"] if i["rows"][0]["label"] in labels]
    assert len(candidates) == len(labels)
    new = {i["rows"][0]["label"]: i for i in candidates}
    assert new.keys() == labels
    assert all(i["bidder_ids"] == ids[:2] and i["level"] == "medium" for i in new.values())
    assert new["同句同错"]["rows"][0]["places"]["tender"][0]["excerpt"] == expected
    assert len(new["分项单价小数尾数共现"]["rows"][0]["items"]) == 5
    bidders = dict(zip(ids, BIDDERS))
    names_only = build(view | {"items": [new["外部项目名称"], new["外部业主名称"]]}, bidders)
    assert not names_only["cases"] and len(names_only["reference"]) >= 2
    case, = view["conclusion"]["cases"]
    assert case["bidder_ids"] == ids[:2] and case["tier"] == "verify" and not case["certain_pairs"]
    assert set(i["id"] for i in new.values()) <= {e["item_id"] for e in case["evidence"]}
    assert not second_targets(view, {})[1]
    report = _text(c.get(base + "/report", headers=h).content)
    assert all(label in report for label in labels)
    assert expected in report and "招标文件 · " in report


def test_new_compliance_finding_and_matrix_survive_a_recheck(owner, db_sessionmaker):
    from io import BytesIO
    from docx import Document
    from app.bid.checks import _run_once
    from app.db.models import Task

    def document(text):
        doc = Document()
        doc.add_paragraph(text)
        stream = BytesIO()
        doc.save(stream)
        return stream.getvalue()

    c, h, _ = owner
    base, _, _ = _checked(c, h, db_sessionmaker, [("虚构投标文件.docx", document("投标交货期：21日历天。"))],
                          tender_bytes=document("★ 交货期不得超过20日历天。"))
    first = c.get(base + "/check", headers=h).json()
    issue = next(i for i in first["items"] if i["type"] == "delivery_max")
    cell = next(cell for cell in first["cells"] if cell["requirement_id"] == issue["requirement_id"])
    assert cell["verdict"] == "no" and issue["level"] == "general"
    assert cell["locs"] and issue["rows"][0]["places"]
    assert any(entry["item_id"] == issue["id"] for entry in first["conclusion"]["verify"])
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(base.rsplit("/", 1)[-1]))
        _run_once(db, task, progressive=False)
        db.commit()
    repeated = c.get(base + "/check", headers=h).json()
    assert [i["id"] for i in repeated["items"] if i["type"] == "delivery_max"] == [issue["id"]]


def test_new_compliance_checks_bid_commitments_and_performance_table():
    from datetime import date
    from app.bid import compliance, pricing

    rows = [
        ["项目名称", "合同签订日期", "合同金额（万元）"],
        ["虚构甲工程", "2024-01-01", "50.01"],
        ["虚构乙工程", "2023-12-31", "60"],
        ["虚构丙工程", "2025-01-01", "50.00"],
        ["虚构甲工程", "2024-01-01", "50.01"],
    ]
    nodes = [compliance.BidNode(f"F1-P{i}", line, 1, None, ocr_conf=0.99 if i == 3 else None)
             for i, line in enumerate(("我方承诺工期为121日历天。", "投标承诺：交货期为20工作日。",
                                      "我公司承诺质保期为2年。", "我方承诺缺陷责任期为12个月。",
                                      "类似业绩一览表"))]
    nodes.extend(compliance.BidNode(f"F1-T1-R{i}-C{j}", value, 1, None, kind="table_cell")
                 for i, row in enumerate(rows, 1) for j, value in enumerate(row, 1))
    params = {"construction_max": "120天", "delivery_max": "20日历天", "warranty_min": "24个月",
              "defect_min": "24个月", "performance_from": "2024-01-01", "performance_until": "2025-01-01",
              "performance_amount": "500100.00", "performance_count": 2}
    _, _, facts = pricing.analyze([1], {1: nodes}, None, "CNY")
    issues, verdicts = compliance.evaluate(1, nodes, facts[1], params, date(2026, 1, 1),
                                          lambda n, *_: {"loc": n.loc})
    assert {i["type"] for i in issues} == {"construction_max", "performance"}
    assert [verdicts[k][0] for k in (*compliance.TERM_LABELS, "performance")] == [
        "no", "unclear", "yes", "unclear", "no"]
    assert "1 份" in verdicts["performance"][1]
    assert {"F1-T1-R3-C2", "F1-T1-R4-C3"} <= {p["loc"] for p in verdicts["performance"][2]}


def test_new_compliance_ignores_contract_templates_and_unlabelled_tables():
    from datetime import date
    from app.bid import compliance, pricing

    nodes = [
        compliance.BidNode("F1-P0", "合同条款：工期为180天；缺陷责任期为6个月。", 1, None),
        compliance.BidNode("F1-T2-R1-C1", "项目名称", 1, None, kind="table_cell"),
        compliance.BidNode("F1-T2-R1-C2", "合同签订日期", 1, None, kind="table_cell"),
        compliance.BidNode("F1-T2-R2-C1", "虚构甲工程", 1, None, kind="table_cell"),
        compliance.BidNode("F1-T2-R2-C2", "2024-01-01", 1, None, kind="table_cell"),
    ]
    _, _, facts = pricing.analyze([1], {1: nodes}, None, "CNY")
    issues, verdicts = compliance.evaluate(
        1, nodes, facts[1], {"construction_max": "120天", "defect_min": "12个月", "performance_count": 1},
        date(2026, 1, 1), lambda n, *_: {"loc": n.loc},
    )
    assert not issues
    assert {verdicts[k][0] for k in ("construction_max", "defect_min", "performance")} == {"missing"}


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
        totals = pricing.labelled_totals(nodes)
        assert {v for v, _c, _n in totals} == {Decimal(v) for v in case['totals']}, case['name']
        if 'currency' in case:
            assert {c for _v, c, _n in totals} == {case['currency']}, case['name']
        if 'row_errors' in case:
            _, findings = pricing.quote_tables(nodes)
            assert [r.locs[0] for r in findings if r.code == 'L-01'] == case['row_errors']
            assert nodes[5].text == '12000'  # 临时计算副本不改原始单价
        if case.get('separate_scopes'):
            from datetime import date
            from app.bid.compliance import evaluate
            analysis, _, facts = pricing.analyze([0], {0: nodes}, Decimal('500000'), 'CNY')
            assert facts[0]['separate_scopes'] and facts[0]['total'] is None
            assert analysis['bidders']['0']['not_compared'] == '不同范围报价，未参加比较'
            issues, verdicts = evaluate(0, nodes, facts[0], {'ceiling': '500000', 'currency': 'CNY'}, date(2026,11,17),
                                        lambda n, *_: {'loc': n.loc})
            assert not any(i['type'] == 'multi_total' and i['level'] == 'veto' for i in issues)
            assert verdicts['multi_total'][0] == verdicts['ceiling'][0] == 'unclear'


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


def test_template_words_not_names_scanned_signing_titles_and_short_names():
    """V34-B-08c：范本句中的词不当联系人；扫描页页首“报价单”进签章页速览；简称取字号。虚构文字。"""
    from types import SimpleNamespace

    from app.bid.checks import contacts_in
    from app.bid.conclusion import short_names
    from app.bid.results import signing_pages
    assert not [c for c in contacts_in("根据此函，签字代表 宣布同意如下：") if c[0] == "PERSON"]
    assert [c[2] for c in contacts_in("授权代表：周示例 电话：010-12345678") if c[0] == "PERSON"] == ["周示例"]
    b = SimpleNamespace(id=uuid.uuid4())
    nodes = {b.id: [("f", "F3-PG2-B1", 2, "六、报价单", "ocr_block", 0.99), ("f", "F3-PG2-B2", 2, "致：虚构采购人", "ocr_block", 0.99),
                    ("f", "F3-PG5-B9", 5, "附：报价单", "ocr_block", 0.99)]}
    assert [(s["page"], s["title"]) for s in signing_pages([b], nodes)] == [(2, "报价单")]  # 页中提及不算
    names = short_names({1: "虚构示例（杭州）泵业科技有限公司", 2: "浙江样例科技股份有限公司", 3: "山东省演示安全生产技术中心"})
    assert names == {1: "虚构示例", 2: "样例", 3: "演示"}


def test_similar_boilerplate_is_downweighted_not_dropped():
    """V34-B-08c：两家附同类审计报告的标准意见（虚构改写）只作公共来源文字降档，仍列出；不计“只有两家共有的相同句”。"""
    from app.bid import similar
    audit = ["我们按照中国注册会计师审计准则的规定执行了审计工作，审计报告中的责任部分进一步阐述了我们的责任。",
             "我们相信，我们获取的审计证据是充分、适当的，为发表审计意见提供了基础，并保持职业怀疑。",
             "管理层负责按照企业会计准则的规定编制财务报表，使其实现公允反映，并设计执行必要的内部控制。",
             "我们的目标是对财务报表整体是否不存在由于舞弊或错误导致的重大错报获取合理保证并出具报告。",
             "按照中国注册会计师职业道德守则，我们独立于被审计单位，并履行了职业道德方面的其他责任。",
             "在编制财务报表时，管理层负责评估被审计单位的持续经营能力，披露与持续经营相关的事项。"]
    a, b = uuid.uuid4(), uuid.uuid4()
    nodes = {x: [("f", f"F{k}-PG9-B{i}", 9, t, "ocr_block") for i, t in enumerate(audit)] for k, x in ((1, a), (2, b))}
    items, _ = similar.find([a, b], nodes, [])
    assert len(items) == 1 and items[0]["level"] == "medium" and items[0]["rows"][0]["common"]  # 248 字连续相同本为强，降一档
    assert "公共来源" in items[0]["title"] and "只有这两家共有" not in items[0]["title"]
