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
    assert "timeline" not in result  # 文件时间线已撤下（V35-E-01）
    assert clues[("pricing", (a, b))]["level"] == "strong" and "1.03" in clues[("pricing", (a, b))]["title"]
    assert not any(i["type"] == "pricing" and x in i["bidder_ids"] for i in result["items"])
    issues = {(i["type"], i["bidder_ids"][0]): i for i in result["items"] if i["kind"] == "issue"}
    assert issues[("uppercase", b)]["level"] == "general"
    assert issues[("multi_total", x)]["level"] == "veto" and issues[("cert_expired", x)]["level"] == "general"
    # 关键参数已撤下：不再比对最高限价、投标有效期与保证金（V35-E-01）
    assert not any(t in ("ceiling", "validity", "deposit") for t, _b in issues)
    assert not any(bidder == a for _t, bidder in issues)  # 甲无问题
    # 严重：多个报价、证书过期；大小写只是提示
    c_ = result["conclusion"]
    severe = {(e["title"], e["bidder_ids"][0]) for e in c_["severe"] if not e["case_id"]}
    assert {bid for _t, bid in severe} == {x} and any("个不同的" in t for t, _b in severe)
    assert any(e["bidder_ids"] == [b] and "大写" in e["title"] for e in c_["hint"])
    pricing = result["pricing"]
    assert pricing["bidders"][a]["total"] == "2,616,000.00" and pricing["bidders"][x]["totals_found"] == ["4,108,000.00", "4,180,000.00"]
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
    assert not names_only["cases"] and len(names_only["hint"]) >= 2
    case, = view["conclusion"]["cases"]
    assert case["bidder_ids"] == ids[:2] and case["level"] == "hint" and not case["strong_pairs"]  # 中等证据只是提示
    assert set(i["id"] for i in new.values()) <= {e["item_id"] for e in case["evidence"]}
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
    assert cell["verdict"] == "no" and issue["level"] == "veto"  # ★ 条款驱动，不满足即严重
    assert cell["locs"] and issue["rows"][0]["places"]
    assert any(entry["item_id"] == issue["id"] for entry in first["conclusion"]["severe"])
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
        if 'tax' in case:  # 总价旁单独写明的含税口径与税率
            row = pricing.analyze([0], {0: nodes})[0]['bidders']['0']
            assert [row.get('tax_basis'), row.get('tax_rate')] == case['tax'], case['name']
        if case.get('separate_scopes'):
            from datetime import date
            from app.bid.compliance import evaluate
            analysis, _, facts = pricing.analyze([0], {0: nodes})
            assert facts[0]['separate_scopes'] and facts[0]['total'] is None
            assert analysis['bidders']['0']['not_compared'] == '不同范围报价，未参加比较'
            issues, verdicts = evaluate(0, nodes, facts[0], {}, date(2026,11,17), lambda n, *_: {'loc': n.loc})
            assert not any(i['type'] == 'multi_total' for i in issues), case['name']  # 范围不同不报（V35-E-02）
            assert verdicts['multi_total'][0] == 'yes' and 'ceiling' not in verdicts


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


def test_last_ocr_page_committed_before_first_check_publication(owner, db_sessionmaker, monkeypatch):
    from app.bid import checks
    from app.parsing import ocr
    c, h, _ = owner
    monkeypatch.setattr(ocr, "recognize", lambda _image: [ocr.OcrLine("虚构扫描文字", [100, 100, 900, 160], .95)])
    original = checks._run_once

    def overlap(db, task, *, progressive=True):
        result = original(db, task, progressive=progressive)
        if progressive:
            # 三步已提交但首检状态仍在运行，在另一会话写入末页并尝试补算。
            _jobs(db_sessionmaker, f"{ROOT}/{task.id}", "ocr")
        return result

    monkeypatch.setattr(checks, "_run_once", overlap)
    _base, project, _ids = _checked(c, h, db_sessionmaker, [("虚构扫描件.pdf", scan_bid("FICTIVE"))])
    assert project["check"]["ocr"] == {"done": 1, "total": 1}
    assert project["check"]["ocr_result"]["state"] == "final"
    assert project["check"]["ocr_result"]["included"] == {"done": 1, "total": 1}


def test_unclassified_valid_certificate_does_not_hide_an_unrelated_expiry():
    from datetime import date

    from app.bid.compliance import BidNode, expired_certificates

    expired = BidNode("F1-PG1-P1", "虚构凭证甲 有效期至：2025-01-01", 1, None, file_id=1, page=1)
    valid = BidNode("F2-PG1-P1", "虚构凭证乙 有效期至：2027-01-01", 2, None, file_id=2, page=1)
    [result] = expired_certificates([expired, valid], date(2026, 11, 24))
    assert result[1] == expired and not result[-1]


def test_dismiss_reopen_and_signing(owner, db_sessionmaker):
    """V35-E-01：唯一的人工操作“不是问题”可撤销，返回修改材料、重新初审后保留；签章页标为缺章即为严重问题。"""
    c, h, _ = owner
    base, _project, (a, b, x) = _checked(c, h, db_sessionmaker, [(f"投标文件{i}.docx", bid2(i)) for i in range(3)])
    result = c.get(base + "/check").json()
    entry = next(e for e in result["conclusion"]["severe"] if e["bidder_ids"] == [x] and e["item_id"])
    out = c.put(base + "/dismissed", headers=h, json={"key": entry["id"], "dismissed": True}).json()["conclusion"]
    assert entry["id"] in {e["id"] for e in out["dismissed"]} and entry["id"] not in {e["id"] for e in out["severe"]}
    assert c.put(base + "/dismissed", headers=h, json={"key": "item:" + str(uuid.uuid4()), "dismissed": True}).status_code == 404
    for gone in ("/review", "/cells", f"/items/{entry['item_id']}", "/conclusion/sign"):  # 核实、签认已撤下
        assert c.patch(base + gone, headers=h, json={}).status_code in (404, 405)
    assert any(p["n"] >= 5 and p["cv"] for p in result["pricing"]["pairs"])  # 未构成线索的一对也给比值
    sign = result["signing"]
    assert sign and all(s["title"] for s in sign)
    s0 = sign[0]
    assert c.put(base + "/signing", headers=h, json={"file_id": s0["file_id"], "page": s0["page"], "loc": s0["loc"], "mark": "missing"}).status_code == 200
    signs = [{k: s[k] for k in ("file_id", "page", "loc")} for s in sign[1:]]
    bad = c.post(base + "/signing/batch", headers=h, json={"signing": signs + [{"file_id": str(uuid.uuid4()), "page": 1}], "mark": "stamped"})
    assert bad.status_code == 404
    if signs:
        out = c.post(base + "/signing/batch", headers=h, json={"signing": signs, "mark": "stamped"}).json()
        assert all(out["marks"][f"{s['file_id']}:{s['page'] if s['page'] is not None else s['loc']}"] == "stamped" for s in signs)
    now = c.get(base + "/check").json()["conclusion"]
    assert any(e["id"].startswith("sign:") and e["bidder_ids"] == [s0["bidder_id"]] for e in now["severe"])

    assert c.post(base + "/reopen", headers=h).json()["stage"] == "ready"
    assert c.post(base + "/check", headers=h).json()["stage"] == "checking"
    _jobs(db_sessionmaker, base, "check")
    again = c.get(base + "/check").json()
    assert entry["id"] in {e["id"] for e in again["conclusion"]["dismissed"]}
    assert next(s for s in again["signing"] if s["loc"] == s0["loc"])["mark"] == "missing"
    restored = c.put(base + "/dismissed", headers=h, json={"key": entry["id"], "dismissed": False}).json()["conclusion"]
    assert entry["id"] in {e["id"] for e in restored["severe"]}
    other = c.post(ROOT, headers=h, json=BODY).json()["id"]  # 别的项目 409（未初审），不泄露本项目的事项
    assert c.put(f"{ROOT}/{other}/dismissed", headers=h, json={"key": entry["id"], "dismissed": True}).status_code == 409


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


def test_similar_skips_boilerplate_fixed_names_and_own_repeats():
    """V35-E-02 问题 2、10：审计报告标准意见等公共来源文字不报；招标人、项目名称比对前去掉；同一家多份文件里的重复只算一处。"""
    from app.bid import similar
    audit = ["我们按照中国注册会计师审计准则的规定执行了审计工作，审计报告中的责任部分进一步阐述了我们的责任。",
             "我们相信，我们获取的审计证据是充分、适当的，为发表审计意见提供了基础，并保持职业怀疑。",
             "管理层负责按照企业会计准则的规定编制财务报表，使其实现公允反映，并设计执行必要的内部控制。",
             "我们的目标是对财务报表整体是否不存在由于舞弊或错误导致的重大错报获取合理保证并出具报告。",
             "按照中国注册会计师职业道德守则，我们独立于被审计单位，并履行了职业道德方面的其他责任。",
             "在编制财务报表时，管理层负责评估被审计单位的持续经营能力，披露与持续经营相关的事项。"]
    a, b = uuid.uuid4(), uuid.uuid4()
    nodes = {x: [("f", f"F{k}-PG9-B{i}", 9, t, "ocr_block") for i, t in enumerate(audit)] for k, x in ((1, a), (2, b))}
    assert similar.find([a, b], nodes, [])[0] == []
    # 两家都照填项目名称与招标人名称的承诺句：去掉名称后不足 20 字，不比
    tender = [("t", "F9-P1", None, "项目名称：虚构松青示范仪器更新改造采购项目", "paragraph"),
              ("t", "F9-P2", None, "招标人：虚构松青示范检测中心有限公司", "paragraph")]
    promise = "我方响应虚构松青示范仪器更新改造采购项目并向虚构松青示范检测中心有限公司承诺第{}项按期供货。"
    nodes = {x: [("f", f"F{k}-P{i}", None, promise.format(n), "paragraph") for i, n in enumerate("一二三四五六")]
             for k, x in ((1, a), (2, b))}
    assert similar.find([a, b], nodes, tender)[0] == []
    # 自写方案在本家两份文件各出现一次，只算一处：相同句仍只有 1 处
    own = "本公司在示范区设立常驻服务站，配置专用检测车辆和备件库，接报后两小时内到场处理并留存维修记录。"
    nodes = {a: [("f1", "F1-P1", None, own, "paragraph"), ("f2", "F2-P1", None, own, "paragraph")],
             b: [("f3", "F3-P1", None, own, "paragraph")]}
    items, stats = similar.find([a, b], nodes, [])
    assert stats["sentences"] == 2 and items == []  # 单句 40 余字，不足 80 字的长段，也不够 5 处相同句


def test_scanner_app_watermark_not_image_clue():
    """扫描全能王等 App 每页叠的 logo / 二维码不算相同图片；同一文件只在一页出现的图片照常比对。"""
    from types import SimpleNamespace as NS

    from app.bid.checks import _scanner_marks
    files = {1: NS(id=1, bidder_id="a", page_count=4, parser_version=1, meta_enc=None),
             2: NS(id=2, bidder_id="b", page_count=4, parser_version=1, meta_enc=None)}
    rows = [NS(file_id=f, page=p, sha256="logo") for f in (1, 2) for p in range(1, 5)]
    rows += [NS(file_id=f, page=2, sha256="seal") for f in (1, 2)]
    nodes = {"a": [(1, "F1-PG1", 1, "3亿人都在用的扫描App", "paragraph")],
             "b": [(2, "F2-PG1", 1, "CamScanner", "paragraph")]}
    assert _scanner_marks(rows, files, nodes) == {(1, "logo"), (2, "logo")}
    assert _scanner_marks(rows, files, {}) == set()  # 没有扫描 App 标记：每页重复的抬头标识仍比对


def test_app_watermark_text_dropped_at_parse():
    """扫描 App 水印整块丢掉；正文里提到 App 名称的句子保留。"""
    from app.parsing.model import is_app_watermark
    assert all(map(is_app_watermark, ["3亿人都在用的扫描App", "CS 扫描全能王", "由 扫描全能王 扫描创建"]))
    assert not is_app_watermark("本合同扫描件由扫描全能王生成，以原件为准。")
