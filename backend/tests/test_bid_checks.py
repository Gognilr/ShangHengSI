"""V34-B-03：本地检查一（混装、联系信息、文件属性、相同图片与基线剔除）；样本全部虚构，不调用模型。"""
import pytest
from sqlalchemy import func, select

from app.bid import fileprops
from app.db.models import LlmCall
from tests.fixtures.bid.samples import AGENT_PHONE, BIDDERS, bid, pdf, tender_with_logo
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
