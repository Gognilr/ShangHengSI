"""V34-B-03、B-04：本地检查（混装、联系信息、属性、图片；相似段落、报价、符合项、扫描页延后识别）；样本全部虚构，不调用模型。"""
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
    assert len(pricing["items"]) == 6 and pricing["items"][0]["prices"][b] == "123,600.00"


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
