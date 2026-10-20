"""报告下载归属、状态、中文文件名与审计；真实接口 T-19。"""
import io
from urllib.parse import unquote

import pytest
from docx import Document
from sqlalchemy import select

from app.db.models import AuditLog, ReviewRun, Task
from app.export.router import CONTENT_TYPE
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_report_docx import contents, report_case  # noqa: F401


def test_download_headers_and_audit(report_case, login, db_sessionmaker):
    owner, task, run = report_case
    client, _ = login(owner.username)
    response = client.get(f"/api/v1/runs/{run.id}/report")
    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="report.docx"; filename*=UTF-8\'\'')
    assert unquote(disposition.split("UTF-8''")[1]).startswith("审核报告_销售合同_")
    assert "星河虚构进出口有限公司快照" in contents(response.content)[1]
    with db_sessionmaker() as db:
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "report_export"))
        assert (audit.target_type, audit.target_id, audit.user_id, audit.detail) == ("run", str(run.id), owner.id, {"run_no": 2})


@pytest.mark.parametrize("status", ["queued", "running", "blocked", "completed", "partial", "failed"])
def test_run_states(status, report_case, login, db_sessionmaker):
    owner, task, run = report_case
    with db_sessionmaker() as db:
        db.get(ReviewRun, run.id).status = status
        db.commit()
    client, _ = login(owner.username)
    response = client.get(f"/api/v1/runs/{run.id}/report")
    if status in ("queued", "running", "blocked"):
        assert response.status_code == 409 and response.json()["code"] == "REPORT_UNAVAILABLE"
    else:
        assert response.status_code == 200
        Document(io.BytesIO(response.content))


def test_owner_isolation_and_invalid_id(report_case, make_user, login, api):
    owner, task, run = report_case
    paths = (f"/api/v1/runs/{run.id}/report", f"/api/v1/tasks/{task.id}/report")
    for path in paths:
        assert api.get(path).status_code == 401
    for role in ("user", "admin"):
        make_user(f"other-{role}", role=role)
        client, _ = login(f"other-{role}")
        for path in paths:
            assert client.get(path).status_code == 404
    client, _ = login(owner.username)
    for path in ("/api/v1/runs/bad/report", "/api/v1/tasks/bad/report"):
        assert client.get(path).status_code == 404
    assert client.get(paths[1]).status_code == 409


def test_local_download_audit_and_no_node_original(make_user, ready_task, login, db_sessionmaker):
    owner = make_user("api-local-report")
    task = ready_task(owner, policy="local_only")
    client, _ = login(owner.username)
    response = client.get(f"/api/v1/tasks/{task.id}/report")
    assert response.status_code == 200 and "未做 AI 审核" in contents(response.content)[1]
    with db_sessionmaker() as db:
        record = db.scalar(select(AuditLog).where(AuditLog.action == "report_export"))
        assert (record.target_type, record.target_id, record.detail) == ("task", str(task.id), {})
        db.get(Task, task.id).status = "parsing"
        db.commit()
    assert client.get(f"/api/v1/tasks/{task.id}/report").status_code == 409
