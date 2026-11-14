"""V321-E-06：确认并发起审核同一事务；解析阶段上报（虚构样本）。"""
import uuid

from sqlalchemy import select, update

from app.db.models import Job, ReviewRun, TaskFile
from app.main import API_PREFIX
from app.parsing import progress
from app.redaction import engine
from tests.review_support import env  # noqa: F401
from tests.test_session_review import BASE, OUR, SAMPLE, _job, model, no_ner  # noqa: F401
from tests.test_tasks_api import files_dir  # noqa: F401
from app.tasks import pipeline


def _parsed(c, h, sm):
    """上传并解析，选好我方，但不确认文件。"""
    tid = c.post(BASE, headers=h).json()["id"]
    assert c.post(f"{BASE}/{tid}/files", headers=h, files=[("files", ("虚构合同.docx", SAMPLE.read_bytes()))]).status_code == 200
    with sm() as db:
        job = _job(sm, "parse", tid)
        pipeline.handle_parse(db, job.payload)
        db.execute(update(Job).where(Job.id == job.id).values(status="done"))
        db.commit()
    parties = c.get(f"{API_PREFIX}/tasks/{tid}/parties").json()["items"]
    with sm() as db:
        mapping = engine.load_redaction_map(db, uuid.UUID(tid))
    items = [{"id": p["id"], "is_our_party": mapping[p["code"]] == OUR, "country": p["country"], "org_type": p["org_type"]} for p in parties]
    assert c.put(f"{API_PREFIX}/tasks/{tid}/parties", headers=h, json={"items": items}).status_code == 200
    return tid


def test_confirm_pending_is_one_transaction(files_dir, model, make_user, login, db_sessionmaker):
    c, csrf = login(make_user("workspace").username)
    h = {"X-CSRF-Token": csrf}
    tid = _parsed(c, h, db_sessionmaker)
    detail = c.get(f"{BASE}/{tid}").json()
    assert detail["status"] == "awaiting_confirm" and detail["files"][0]["confirmed_at"] is None
    # 确认前的外发预览要含待确认的文件，默认（不带 include_pending）为空
    assert "第四条" in c.get(f"{BASE}/{tid}/outbound", params={"mode": "review", "include_pending": True}).json()["text"]
    body = {"stance": "sales", "source": "outgoing", "confirm_pending": True}
    # 材料版本过期：整体回滚，文件仍未确认
    stale = c.post(f"{BASE}/{tid}/runs", headers=h, json={**body, "expected_material_version": detail["material_version"] - 1})
    assert stale.json()["code"] == "MATERIAL_CHANGED"
    after = c.get(f"{BASE}/{tid}").json()
    assert after["status"] == "awaiting_confirm" and after["files"][0]["confirmed_at"] is None
    # 一次调用：文件已确认，且有一轮排队
    r = c.post(f"{BASE}/{tid}/runs", headers=h, json={**body, "expected_material_version": detail["material_version"]})
    assert r.status_code == 201, r.text
    done = c.get(f"{BASE}/{tid}").json()
    assert done["status"] == "active" and done["files"][0]["confirmed_at"] and done["review_running"] is True
    with db_sessionmaker() as db:
        assert db.scalar(select(ReviewRun.status).where(ReviewRun.task_id == uuid.UUID(tid))) == "queued"


def test_parse_progress_reported(files_dir, make_user, login, db_sessionmaker):
    c, csrf = login(make_user("workspace").username)
    h = {"X-CSRF-Token": csrf}
    tid = c.post(BASE, headers=h).json()["id"]
    assert c.post(f"{BASE}/{tid}/files", headers=h, files=[("files", ("虚构合同.docx", SAMPLE.read_bytes()))]).status_code == 200
    with db_sessionmaker() as db:
        job = _job(db_sessionmaker, "parse", tid)
        db.execute(update(Job).where(Job.id == job.id).values(status="running"))
        db.commit()
        fid = db.scalar(select(TaskFile.id).where(TaskFile.task_id == uuid.UUID(tid)))
        progress.start(db, uuid.UUID(tid), [fid])
        progress.file(db, uuid.UUID(tid), fid, "ocr", 2, 5)
        progress.finish(uuid.UUID(tid))
    got = c.get(f"{BASE}/{tid}").json()["parse_progress"]
    assert got["files"][str(fid)] == {"stage": "ocr", "done": 2, "total": 5}
