"""V32-A-01：文件序号只增、被审核轮次引用的文件不能移除、材料变化后发送被拒绝。"""
import uuid

from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import ReviewRun, Task, TaskFile
from tests.test_assistant_sessions import (BASE, create, files_dir, mock_ner, parse, session_client,  # noqa: F401
                                           upload)
from tests.review_support import env  # noqa: F401


def test_seq_not_reused_and_referenced_file_cannot_be_removed(files_dir, session_client, db_sessionmaker):
    c, h, user = session_client
    tid = create(c, h)["id"]
    first = upload(c, h, tid).json()["files"][0]
    parse(db_sessionmaker, tid)
    assert c.delete(f"{BASE}/{tid}/files/{first['id']}", headers=h).status_code == 200
    second = upload(c, h, tid).json()["files"][0]
    with db_sessionmaker() as db:
        assert db.get(TaskFile, uuid.UUID(second["id"])).seq == 2
        assert db.get(Task, uuid.UUID(tid)).last_file_seq == 2
    parse(db_sessionmaker, tid)
    # 旧轮次 file_ids 为空，视为引用全部文件；新轮次只引用列出的文件
    with db_sessionmaker() as db:
        run = ReviewRun(task_id=uuid.UUID(tid), run_no=1, status="completed", stance="sales", source="outgoing",
                        main_doc_type="contract", created_by=user.id, file_ids=None)
        db.add(run); db.commit()
    r = c.delete(f"{BASE}/{tid}/files/{second['id']}", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "FILE_IN_USE"
    with db_sessionmaker() as db:
        db.get(ReviewRun, db.scalar(select(ReviewRun.id))).file_ids = [str(uuid.uuid4())]
        db.commit()
    assert c.delete(f"{BASE}/{tid}/files/{second['id']}", headers=h).status_code == 200


def test_send_rejected_when_material_changed(files_dir, session_client, db_sessionmaker):
    c, h, _ = session_client
    tid = create(c, h)["id"]
    detail = c.get(f"{BASE}/{tid}").json()
    version = detail["material_version"]
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.material_version += 1
        db.commit()
    r = c.post(f"{BASE}/{tid}/messages", headers=h, json={"message": "你好", "expected_material_version": version})
    assert r.status_code == 409 and r.json()["code"] == "MATERIAL_CHANGED"
