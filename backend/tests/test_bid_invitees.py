"""V34-B-01b：发标单位一览表识别与按名单分组；单位名称全部虚构，不调用模型。"""
import io
import json

import pytest
from docx import Document
from openpyxl import Workbook

from tests.fixtures.bid.samples import BIDDERS, bid, tender_with_logo
from tests.review_support import env  # noqa: F401
from tests.test_bid_checks import _jobs
from tests.test_bid_projects import BODY, ROOT, drain, owner  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")
OTHER = "虚构青岚测控股份有限公司"


def _tender_with_list():
    """虚构招标文件末尾附“发标单位一览表”：三家投标人 + 一家未投标的，表上有联系电话；招标人不进名单。"""
    doc = Document(io.BytesIO(tender_with_logo()))
    doc.add_paragraph("招标人：虚构松针建设有限公司")
    doc.add_paragraph("附件：发标单位一览表")
    rows = [("序号", "单位名称", "联系电话"), *((str(i), name, f"0571-8800{i:04d}") for i, name in enumerate([*BIDDERS, OTHER], 1))]
    rows.append(("5", "虚构松针建设有限公司", "0571-88009999"))
    table = doc.add_table(rows=len(rows), cols=3)
    for r, row in enumerate(rows):
        for col, text in enumerate(row):
            table.cell(r, col).text = text
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _project(c, h, sm, name, content):
    base = ROOT + "/" + c.post(ROOT, headers=h, json=BODY).json()["id"]
    assert c.post(base + "/files", headers=h, files={"files": (name, content)}).status_code == 200
    drain(sm, base)
    return base


def test_word_list_groups_and_mixed_baseline(owner, db_sessionmaker):
    c, h, _ = owner
    base = _project(c, h, db_sessionmaker, "虚构招标文件.docx", _tender_with_list())
    data = c.get(base + "/requirements").json()
    names = [e["name"] for e in data["invitees"]]
    assert names == [*BIDDERS, OTHER]  # 招标人剔除
    assert {e["source"] for e in data["invitees"]} == {"table"} and all(e["loc"].startswith("F1-T") for e in data["invitees"])
    assert c.get(base).json()["invitees"] == names

    # 文件夹分组按名单全称建卡；未投标的一家不建卡、不占代号
    files = [("files", (f"投标文件{i}.docx", bid(i))) for i in range(3)]
    assert c.post(base + "/files", headers=h, files=files, data={"groups": json.dumps(list(BIDDERS), ensure_ascii=False)}).status_code == 200
    drain(db_sessionmaker, base)
    project = c.get(base).json()
    assert [b["code"] for b in project["bidders"]] == ["A", "B", "C"]
    assert c.post(base + "/confirm", headers=h).json()["stage"] == "ready"
    assert c.post(base + "/check", headers=h).json()["stage"] == "checking"
    _jobs(db_sessionmaker, base, "check")
    ids = {b["name"]: b["id"] for b in project["bidders"]}
    items = c.get(base + "/check").json()["items"]
    # 名单在招标文件中时，混装仍能报出（名单表格不作基线）
    assert any(i["type"] == "mixed" and i["bidder_ids"] == [ids[BIDDERS[2]], ids[BIDDERS[0]]] for i in items)


def test_excel_list_edit_and_duplicates(owner, db_sessionmaker):
    c, h, _ = owner
    wb = Workbook()
    ws = wb.active
    ws.append(["拟邀请投标单位名单"])
    ws.append(["序号", "投标人名称", "备注"])
    for i, name in enumerate(["虚构云杉仪表有限公司", "虚构（杭州）白桦电气有限公司", "虚构柏木自动化有限公司"], 1):
        ws.append([i, name, ""])
    out = io.BytesIO()
    wb.save(out)
    base = _project(c, h, db_sessionmaker, "虚构名单.xlsx", out.getvalue())
    data = c.get(base + "/requirements").json()
    assert [(e["name"], e["source"], e["loc"]) for e in data["invitees"]] == [
        ("虚构云杉仪表有限公司", "table", "F1-S1-B3"), ("虚构（杭州）白桦电气有限公司", "table", "F1-S1-B4"),
        ("虚构柏木自动化有限公司", "table", "F1-S1-B5")]

    # 用户修改：规范化后同名拒绝；改过后重新抽取不覆盖
    dup = ["虚构云杉仪表有限公司", "虚构 云杉仪表有限公司"]
    assert c.put(base + "/invitees", headers=h, json={"names": dup}).status_code == 422
    kept = ["虚构云杉仪表有限公司", "虚构手工补充有限公司"]
    saved = c.put(base + "/invitees", headers=h, json={"names": kept}).json()
    assert [(e["name"], e["source"]) for e in saved["invitees"]] == [(kept[0], "table"), (kept[1], "manual")] and saved["invitees_edited"]
    assert [e["name"] for e in c.post(base + "/requirements/extract", headers=h).json()["invitees"]] == kept

    # 投标人：全半角括号、空白规范化后同名一律拒绝
    assert c.post(base + "/bidders", headers=h, json={"name": "虚构（杭州）白桦电气有限公司"}).status_code == 201
    assert c.post(base + "/bidders", headers=h, json={"name": "虚构(杭州)白桦电气 有限公司"}).status_code == 422
    groups = ["虚构柏木自动化有限公司", "虚构柏木自动化 有限公司"]
    files = [("files", (f"文件{i}.docx", bid(i))) for i in range(2)]
    assert c.post(base + "/files", headers=h, files=files, data={"groups": json.dumps(groups, ensure_ascii=False)}).status_code == 422
