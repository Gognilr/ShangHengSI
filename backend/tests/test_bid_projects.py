"""V34-B-01：少量代表性流程；全部虚构，任何模型调用均应失败。"""
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.bid import service
from app.db.models import BidBidder, BidProject, DocNode, Job, Role, Task, TaskFile, UserRole
from app.parsing import pdf
from app.parsing.model import ParseError, ParsedFile, ParsedNode
from app.security import crypto
from app.tasks.pipeline import handle_parse
from tests.fixtures.bid.samples import PASSWORD, document, pdf as sample_pdf
from tests.review_support import env  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")
ROOT = "/api/v1/bid"
BODY = {"title": "虚构设备采购项目", "opening_date": "2026-11-16", "policy": "local_only"}


def grant(sm, user):
    with sm() as db:
        role = Role(name="虚构标书权限" + user.username, permissions=["use.bid"])
        db.add(role)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()


@pytest.fixture
def owner(make_user, login, db_sessionmaker):
    user = make_user("bid_owner")
    grant(db_sessionmaker, user)
    c, csrf = login(user.username)
    return c, {"X-CSRF-Token": csrf}, user


def create(c, h):
    response = c.post(ROOT, headers=h, json=BODY)
    assert response.status_code == 201, response.text
    return ROOT + "/" + response.json()["id"]


def drain(sm, base):
    tid = base.rsplit("/", 1)[-1]
    with sm() as db:
        for job in db.scalars(select(Job).where(Job.type == "parse", Job.payload["task_id"].astext == tid,
                                              Job.status == "queued").order_by(Job.id)).all():
            job.status = "running"
            db.commit()
            handle_parse(db, job.payload)
            job.status = "done"
            db.commit()


def test_permissions_owner_and_cross_project_files(owner, make_user, login, db_sessionmaker):
    ordinary = make_user("ordinary")
    c0, token = login(ordinary.username)
    assert c0.get(ROOT).status_code == 403
    assert c0.post(ROOT, headers={"X-CSRF-Token": token}, json=BODY).status_code == 403
    c, h, _ = owner
    base = create(c, h)
    other = make_user("other_admin", role="admin")
    grant(db_sessionmaker, other)
    cb, tb = login(other.username)
    for method, path, body in [("GET", base, None), ("DELETE", base, None),
                               ("POST", base + "/bidders", {"name": "虚构乙"}),
                               ("GET", base + "/files/invalid/password", None)]:
        assert cb.request(method, path, headers={"X-CSRF-Token": tb}, json=body).status_code == 404
    assert c.get(ROOT).json()["total"] == 1 and cb.get(ROOT).json()["total"] == 0
    b1 = c.post(base + "/bidders", headers=h, json={"name": "虚构甲"}).json()["bidders"][0]["id"]
    second = create(c, h)
    assert c.post(second + "/files", headers=h, data={"bidder_id": b1}, files={"files": ("虚构.docx", document())}).status_code == 404


def test_group_upload_parse_encryption_and_deletion(owner, db_sessionmaker, caplog):
    c, h, _ = owner
    base = create(c, h)
    names = ["虚构甲投标人", "虚构乙投标人"]
    response = c.post(base + "/files", headers=h, data={"groups": json.dumps(names)},
                      files=[("files", ("虚构甲.docx", document())), ("files", ("虚构乙.docx", document()))])
    assert response.status_code == 200, response.text
    assert [b["code"] for b in response.json()["bidders"]] == ["A", "B"]
    drain(db_sessionmaker, base)
    data = c.get(base).json()
    assert all(f["state"] == "done" for f in data["files"])
    with db_sessionmaker() as db:
        tid = uuid.UUID(data["id"])
        for bidder in db.scalars(select(BidBidder).where(BidBidder.task_id == tid)):
            assert all(n.encode() not in bidder.name_enc for n in names)
            assert crypto.decrypt_text(bidder.name_enc) in names
        assert db.scalar(select(func.count()).select_from(DocNode).where(DocNode.task_id == tid)) == 2
        before = set(db.scalars(select(DocNode.id).where(DocNode.task_id == tid)))
    # 逐卡再加文件：只解析新文件，已解析的节点保留
    bidder = data["bidders"][0]["id"]
    assert c.post(base + "/files", headers=h, data={"bidder_id": bidder}, files={"files": ("虚构甲补充.docx", document())}).status_code == 200
    drain(db_sessionmaker, base)
    with db_sessionmaker() as db:
        after = set(db.scalars(select(DocNode.id).where(DocNode.task_id == tid)))
        assert before < after and len(after) == 3
        from app.tasks import storage
        keys = [f.storage_key for f in db.scalars(select(TaskFile).where(TaskFile.task_id == tid))]
    # 同一用户其他项目的 file id 也不能借用。
    second = create(c, h)
    assert c.get(second + f"/files/{data['files'][0]['id']}/password").status_code == 404
    assert c.delete(base, headers=h).status_code == 204
    with db_sessionmaker() as db:
        assert db.get(BidProject, tid) is None
        assert db.scalar(select(func.count()).select_from(BidBidder).where(BidBidder.task_id == tid)) == 0
        assert db.scalar(select(func.count()).select_from(DocNode).where(DocNode.task_id == tid)) == 0
    assert all(not storage._path(key).exists() for key in keys)
    assert all(n not in caplog.text for n in names)


def test_pdf_password_wrong_correct_and_reparse(owner, db_sessionmaker, caplog):
    c, h, _ = owner
    base = create(c, h)
    response = c.post(base + "/files", headers=h, files={"files": ("虚构加密.pdf", sample_pdf())})
    fid = response.json()["files"][0]["id"]
    drain(db_sessionmaker, base)
    assert c.get(base).json()["files"][0]["needs_password"]
    path = base + f"/files/{fid}/password"
    bad = c.put(path, headers=h, json={"password": "fictional-wrong"})
    assert bad.status_code == 422 and bad.json()["code"] == "PDF_PASSWORD_INCORRECT"
    good = c.put(path, headers=h, json={"password": PASSWORD})
    assert good.status_code == 200, good.text
    assert PASSWORD not in good.text
    drain(db_sessionmaker, base)
    assert c.get(base).json()["files"][0]["state"] == "done"
    revealed = c.get(path)
    assert revealed.json()["password"] == PASSWORD and revealed.headers["Cache-Control"] == "no-store"
    with db_sessionmaker() as db:
        row = db.get(TaskFile, uuid.UUID(fid))
        assert PASSWORD.encode() not in row.password_enc
        assert crypto.decrypt_text(row.password_enc) == PASSWORD
    assert c.post(base + "/parse", headers=h).status_code == 200
    drain(db_sessionmaker, base)
    assert c.get(base).json()["files"][0]["state"] == "done"
    assert PASSWORD not in caplog.text and "虚构加密" not in caplog.text


def test_bid_limits_and_retention(owner, db_sessionmaker):
    c, h, _ = owner
    base = create(c, h)
    tender = [("files", (f"虚构招标{i}.docx", document())) for i in range(10)]
    assert c.post(base + "/files", headers=h, files=tender).status_code == 200
    drain(db_sessionmaker, base)
    assert c.post(base + "/files", headers=h, files={"files": ("超出.docx", document())}).status_code == 422
    bid = [("files", (f"虚构投标{i}.docx", document())) for i in range(20)]
    response = c.post(base + "/files", headers=h, data={"groups": json.dumps(["虚构甲"] * 20)}, files=bid)
    assert response.status_code == 200, response.text
    drain(db_sessionmaker, base)
    bidder = response.json()["bidders"][0]["id"]
    assert c.post(base + "/files", headers=h, data={"bidder_id": bidder}, files={"files": ("超出.docx", document())}).status_code == 422
    data = sample_pdf(encrypted=False, pages=201)
    with pytest.raises(ParseError, match="200"):
        pdf.inspect_pdf(data)
    assert pdf.inspect_pdf(data, max_pages=600).page_count == 201
    from app.admin.settings_store import set_setting
    from app.backup.retention import run_retention
    from app.db.base import utcnow
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(base.rsplit("/", 1)[-1]))
        task.last_activity_at = utcnow() - timedelta(days=40)
        set_setting(db, "retention.unfinished_days", 30, None)
        db.commit()
        assert run_retention(db)["unfinished"] == 1


def test_project_ocr_budget_before_second_file(owner, db_sessionmaker, monkeypatch):
    c, h, _ = owner
    base = create(c, h)
    data = sample_pdf(encrypted=False)
    response = c.post(base + "/files", headers=h, files=[("files", ("虚构一.pdf", data)), ("files", ("虚构二.pdf", data))])
    assert response.status_code == 200
    monkeypatch.setattr(pdf, "inspect_pdf", lambda *a, **k: pdf.PdfInfo(600, list(range(1, 601))))
    called = []
    def parsed(data, seq, **kwargs):
        called.append(seq)
        return ParsedFile([ParsedNode(f"F{seq}-PG1-B1", 1, "ocr_block", "body", "虚构扫描文本")], 600, False, [], 600)
    monkeypatch.setattr(pdf, "parse_pdf", parsed)
    drain(db_sessionmaker, base)
    files = c.get(base).json()["files"]
    assert called == [1] and files[0]["state"] == "done" and "1000" in files[1]["error"]
