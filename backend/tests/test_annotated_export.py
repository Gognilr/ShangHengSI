"""批注版真实接口：虚构任务、模拟转换，不调用模型或 converter。"""
import io
import json
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from sqlalchemy import delete, select

from app.admin.settings_store import set_setting
from app.backup.retention import run_retention
from app.db.base import utcnow
from app.db.models import AuditLog, DocNode, Finding, LocalFinding, RedactionMap, ReviewRun, Task, TaskFile
from app.export import annotated_docx
from app.parsing.docx import parse_docx
from app.security import crypto
from app.tasks import pipeline, storage
from app.tasks.service import delete_task
from tests.review_support import env  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def case(make_user, db_sessionmaker):
    user = make_user("annotated-owner")
    doc = Document()
    doc.add_paragraph("星河虚构公司应按期交货。")
    doc.add_paragraph("未约定争议解决方式。")
    stream = io.BytesIO()
    doc.save(stream)
    data = stream.getvalue()
    with db_sessionmaker() as db:
        task = Task(owner_id=user.id, title="虚构合同", stance="sales", source="outgoing", policy="ai", status="reviewed")
        db.add(task)
        db.flush()
        key, digest = storage.save_original(data)
        file = TaskFile(task_id=task.id, seq=1, is_main=True, original_name="虚构合同.docx", ext="docx",
                        size_bytes=len(data), sha256=digest, storage_key=key)
        db.add(file)
        db.flush()
        for n in parse_docx(data, 1).nodes:
            db.add(DocNode(task_id=task.id, file_id=file.id, loc=n.loc, ord=n.ord, kind=n.kind, part=n.part,
                           text_enc=crypto.encrypt_text(n.text)))
        db.add(RedactionMap(task_id=task.id, map_enc=crypto.encrypt_text(json.dumps({"[客户_A]": "星河虚构公司"}))))
        run = ReviewRun(task_id=task.id, run_no=1, status="completed", stance="sales", source="outgoing",
                        main_doc_type="contract", created_by=user.id)
        db.add(run)
        db.flush()
        for seq, loc, quote, status in [(1, "F1-P1", "[客户_A]应按期交货", "pending"),
                                         (2, "F1-P2", "不存在的原文", "pending"),
                                         (3, "F1-P1", "[客户_A]", "ignored")]:
            db.add(Finding(task_id=task.id, run_id=run.id, seq=seq, title="[客户_A]的义务", category="legal",
                           severity="high", locs=[loc], cite_status="unverified", quote_model=quote,
                           problem="[客户_A]责任不明确", suggestion="请[客户_A]明确", suggested_text="[客户_A]应及时履行",
                           basis="虚构合同依据", handling_status=status))
        db.add(LocalFinding(task_id=task.id, file_id=file.id, check_code="L-08", severity="medium",
                            locs=["F1-P1"], detail_enc=crypto.encrypt_text("[客户_A]的交期需核对"), ai_summary="虚构校验"))
        db.commit()
    return user, task.id, run.id, file.id, data


def test_comments_restore_body_headers_and_audit(case, login, db_sessionmaker):
    user, tid, rid, fid, original = case
    client, _ = login(user.username)
    assert client.get(f"/api/v1/tasks/{tid}").json()["files"][0]["annotatable"] is True
    response = client.get(f"/api/v1/runs/{rid}/annotated?file_seq=1")
    assert response.status_code == 200, response.text
    assert response.headers["X-SI-Total"] == "3"
    assert response.headers["X-SI-Unanchored"] == "1"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    doc = Document(io.BytesIO(response.content))
    assert len(doc.comments) == 3
    assert all(c.author == "商衡SI" and c.initials == "SI" for c in doc.comments)
    text = "\n".join(c.text for c in doc.comments)
    assert "星河虚构公司" in text and "[客户_A]" not in text
    assert "问题 3" not in text and "未能精确定位" in text and "本地校验" in text
    comments = list(doc.comments)
    assert "问题 1" in comments[0].text and "问题 2" in comments[1].text
    annotated_docx.validate(original, response.content)
    assert storage.load_original(_file(db_sessionmaker, fid).storage_key) == original
    with db_sessionmaker() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.action == "export_annotated"))
        assert row.target_id == str(rid)
        assert row.detail == dict(run_no=1, file_seq=1, total=3, unanchored=1)


def _file(sm, fid):
    with sm() as db:
        return db.get(TaskFile, fid)


def test_owner_format_state_and_mismatch(case, make_user, login, db_sessionmaker, monkeypatch):
    user, tid, rid, fid, _ = case
    make_user("other", role="admin")
    other, _ = login("other")
    path = f"/api/v1/runs/{rid}/annotated?file_seq=1"
    assert other.get(path).status_code == 404
    client, _ = login(user.username)
    with db_sessionmaker() as db:
        db.get(TaskFile, fid).ext = "pdf"
        db.commit()
    assert client.get(path).status_code == 409
    with db_sessionmaker() as db:
        db.get(TaskFile, fid).ext = "docx"
        db.get(ReviewRun, rid).status = "running"
        db.commit()
    assert client.get(path).json()["message"] == "本轮尚未完成"
    with db_sessionmaker() as db:
        db.get(ReviewRun, rid).status = "partial"
        node = db.scalar(select(DocNode).where(DocNode.file_id == fid).limit(1))
        previous = node.text_enc
        node.text_enc = crypto.encrypt_text("改动一个字")
        db.commit()
    assert client.get(path).json()["code"] == "ANCHOR_MISMATCH"
    with db_sessionmaker() as db:
        node = db.scalar(select(DocNode).where(DocNode.file_id == fid).limit(1))
        node.text_enc = previous
        db.commit()
    def fail(*args):
        raise annotated_docx.AnnotationValidationError("BODY_TEXT_CHANGED")
    monkeypatch.setattr(annotated_docx, "validate", fail)
    assert client.get(path).json()["message"] == "生成批注版时校验未通过，未输出文件"
    with db_sessionmaker() as db:
        db.execute(delete(Finding).where(Finding.run_id == rid))
        db.execute(delete(LocalFinding).where(LocalFinding.task_id == tid))
        db.commit()
    assert client.get(path).json()["message"] == "本轮没有可批注的问题"


def test_legacy_save_export_and_retention(case, login, db_sessionmaker, monkeypatch):
    user, tid, rid, fid, _ = case
    root = Path(__file__).parent / "fixtures" / "legacy"
    converted = (root / "contract_basic_doc.docx").read_bytes()
    monkeypatch.setattr(pipeline.legacy, "convert", lambda data, ext: converted)
    with db_sessionmaker() as db:
        file = db.get(TaskFile, fid)
        storage.delete_original(file.storage_key)
        file.storage_key, file.sha256 = storage.save_original((root / "contract_basic.doc").read_bytes())
        file.ext, file.original_name = "doc", "旧合同.doc"
        db.execute(delete(DocNode).where(DocNode.file_id == fid))
        pipeline._parse_file(db, db.get(Task, tid), file, 0, [])
        db.commit()
        key = file.converted_storage_key
        assert key and storage.load_original(key) == converted
        assert storage._path(key).read_bytes() != converted
    client, _ = login(user.username)
    path = f"/api/v1/runs/{rid}/annotated?file_seq=1"
    result = client.get(path)
    assert result.status_code == 200, result.text
    annotated_docx.validate(converted, result.content)
    with db_sessionmaker() as db:
        file = db.get(TaskFile, fid)
        file.converted_storage_key = None
        db.commit()
    assert "升级前上传" in client.get(path).json()["message"]
    with db_sessionmaker() as db:
        db.get(TaskFile, fid).converted_storage_key = key
        db.get(Task, tid).last_activity_at = utcnow() - timedelta(days=2)
        set_setting(db, "retention.original_days", 1, None)
        db.commit()
        run_retention(db)
    assert not storage._path(key).exists()
    assert not storage._path(_file(db_sessionmaker, fid).storage_key).exists()
    assert client.get(path).json()["message"] == "原件已按保留期删除，不能生成批注版"
    assert client.get(f"/api/v1/tasks/{tid}").json()["files"][0]["annotatable"] is False
    with db_sessionmaker() as db:
        file = db.get(TaskFile, fid)
        file.converted_storage_key, _ = storage.save_original(converted)
        replacement = file.converted_storage_key
        db.commit()
        delete_task(db, db.get(Task, tid), user, None)
    assert not storage._path(replacement).exists()


def test_validation_detects_body_and_other_part_changes(case):
    original = case[-1]
    for part in ("word/document.xml", "word/styles.xml"):
        stream = io.BytesIO()
        with ZipFile(io.BytesIO(original)) as source, ZipFile(stream, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == part:
                    data = data.replace("星河".encode(), "星海".encode()) if part.endswith("document.xml") else data + b" "
                target.writestr(name, data)
        with pytest.raises(annotated_docx.AnnotationValidationError):
            annotated_docx.validate(original, stream.getvalue())
