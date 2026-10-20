"""本地结果接口：归属、中文位置、空状态及敏感字段边界。"""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import LocalFinding, Task
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_checks_service import ROOT


def test_owner_and_admin_isolation(make_user, login, ready_task, db_sessionmaker, api):
    owner = make_user("checks-owner")
    make_user("checks-other")
    make_user("checks-admin", role="admin")
    task = ready_task(owner, [("报价单.xlsx", (ROOT / "row_error.xlsx").read_bytes())], policy="local_only")
    url = f"/api/v1/tasks/{task.id}/local-findings"
    assert api.get(url).status_code == 401
    for username in ("checks-other", "checks-admin"):
        other, _ = login(username)
        assert other.get(url).status_code == 404
    client, _ = login(owner.username)
    result = client.get(url)
    assert result.status_code == 200
    item = result.json()["items"][0]
    assert set(item) == {"id", "check_code", "severity", "locs", "file_id", "file_name", "location", "detail"}
    assert item["location"].endswith(" D2") and item["file_name"] == "报价单.xlsx"
    assert client.get("/api/v1/tasks/bad/local-findings").status_code == 404
    assert client.get(f"/api/v1/tasks/{uuid.uuid4()}/local-findings").status_code == 404
    with db_sessionmaker() as db:
        row = db.scalar(select(LocalFinding).where(LocalFinding.task_id == task.id))
        row.severity = "low"
        db.add(LocalFinding(task_id=task.id, file_id=row.file_id, check_code="L-INFO", severity="high",
                            locs=[], detail_enc=row.detail_enc, ai_summary=""))
        db.commit()
    items = client.get(url).json()["items"]
    assert [item["severity"] for item in items] == ["high", "low"]
    assert items[0]["location"] == "报价单.xlsx"
    for status in ("parsing", "parse_failed"):
        with db_sessionmaker() as db:
            db.get(Task, task.id).status = status
            db.commit()
        assert client.get(url).json() == {"items": []}


@pytest.mark.parametrize("name,location", [("row_error.pdf", "表1 第2行"), ("uppercase_error.docx", "F1-P1")])
def test_other_location_formats(name, location, make_user, login, ready_task):
    owner = make_user("location")
    task = ready_task(owner, [(name, (ROOT / name).read_bytes())], policy="local_only")
    client, _ = login(owner.username)
    item = client.get(f"/api/v1/tasks/{task.id}/local-findings").json()["items"][0]
    assert item["location"] == f"{name} {location}"
