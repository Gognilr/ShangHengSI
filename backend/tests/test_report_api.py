"""报告下载归属、状态、中文文件名与审计；真实接口 T-19。"""
import io
from pathlib import Path
from time import perf_counter
from urllib.parse import unquote

import pytest
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from sqlalchemy import select

from app.db.models import AuditLog, DocNode, Finding, ReviewRun, Task, TaskFile
from app.export.router import CONTENT_TYPE
from app.tasks import storage
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
    paths = (f"/api/v1/runs/{run.id}/report", f"/api/v1/tasks/{task.id}/report", f"/api/v1/runs/{run.id}/issue-list")
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


@pytest.mark.parametrize("sample", ["text_contract", "scanned_contract"])
def test_issue_list_from_pdf(sample, make_user, ready_task, login, db_sessionmaker, tmp_path):
    owner = make_user("issue-list-owner")
    data = (Path(__file__).with_name("fixtures") / "pdf" / f"{sample}.pdf").read_bytes()
    name = "虚构销售合同.pdf"
    task = ready_task(owner, [(name, data)])
    with db_sessionmaker() as db:
        file = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
        nodes = list(db.scalars(select(DocNode).where(DocNode.file_id == file.id, DocNode.part == "body")
                                .order_by(DocNode.ord)))
        if sample == "text_contract":
            heading = next(i for i, node in enumerate(nodes) if node.clause_no == "第2条")
            anchor = nodes[heading + 1]
            assert (anchor.page, anchor.clause_no) == (2, None)
            location = f"{name} 第 2 页 · 第2条"
        else:
            anchor = next(node for node in nodes if node.kind == "ocr_block")
            assert anchor.page == 1
            location = f"{name} 第 1 页"
        run = ReviewRun(task_id=task.id, run_no=1, status="running", created_by=owner.id,
                        stance="sales", source="outgoing", main_doc_type="contract", file_ids=[str(file.id)])
        db.add(run)
        db.flush()
        shared = dict(run_id=run.id, task_id=task.id, category="commercial", severity="high")
        db.add_all([
            Finding(**shared, seq=1, title="明确付款凭证", locs=[anchor.loc], cite_status="verified",
                    problem="付款起算点没有绑定可查验的凭证。", basis="合同的付款条款应便于双方核验。",
                    suggested_text="未选用的原审核建议", use_discussed=True,
                    discussed_text="买方应在收到发票和签收单后五个工作日内支付货款，[客户_Z]负责确认。"),
            Finding(**shared, seq=2, title="已忽略的意见", problem="本条已忽略。", handling_status="ignored", cite_status="missing"),
            Finding(**shared, seq=3, title="已解决的意见", problem="本条已解决。", carry="resolved", cite_status="missing"),
        ])
        db.commit()
        rid, key = run.id, file.storage_key

    client, _ = login(owner.username)
    path = f"/api/v1/runs/{rid}/issue-list"
    assert client.get(path).status_code == 409
    with db_sessionmaker() as db:
        db.get(ReviewRun, rid).status = "completed"
        db.commit()
    started = perf_counter()
    response = client.get(path)
    print(f"{sample} 导出 {perf_counter() - started:.3f} 秒，{len(response.content)} 字节")
    assert response.status_code == 200 and response.headers["content-type"] == CONTENT_TYPE
    assert "问题清单_虚构销售合同_" in unquote(response.headers["content-disposition"])
    doc, text = contents(response.content)
    table, = doc.tables
    assert [cell.text for cell in table.rows[0].cells] == ["原页码与条款", "问题与依据", "建议条文"]
    assert len(table.rows) == 2 and table.cell(1, 0).text.startswith(location)
    assert "付款起算点" in table.cell(1, 1).text and "依据：合同的付款条款" in table.cell(1, 1).text
    assert "五个工作日内支付货款" in table.cell(1, 2).text
    assert any(run.font.highlight_color == WD_COLOR_INDEX.YELLOW for run in table.cell(1, 2).paragraphs[0].runs)
    assert all(value not in text for value in ("未选用的原审核建议", "已忽略的意见", "已解决的意见"))
    assert storage.load_original(key) == data
    with db_sessionmaker() as db:
        record = db.scalar(select(AuditLog).where(AuditLog.action == "report_export"))
        assert record.target_id == str(rid) and record.detail == {"run_no": 1, "format": "issue_list"}
    (tmp_path / f"{sample}.docx").write_bytes(response.content)


def test_issue_list_for_docx(report_case, login):
    owner, _, run = report_case
    client, _ = login(owner.username)
    response = client.get(f"/api/v1/runs/{run.id}/issue-list")
    assert response.status_code == 200
    _, text = contents(response.content)
    assert "缺失项" in text and "Fictional Metals Trading FZE应按期交货" in text


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
