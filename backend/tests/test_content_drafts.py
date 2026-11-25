"""V35-D-05：专职人员共享脱敏结果和撤回的代表性流程。"""
import uuid

from sqlalchemy import select

from app.db import jobs
from app.db.models import BidCategory, ContentDraft, Finding, LocalFinding, ReviewRun, TaskClauseVector
from app.review.prompt import ReviewContext
from app.review.validate import DocNodeView
from app.standards import compare, job
from app.worker import process_job
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_bid_projects import BODY, ROOT, grant
from tests.test_standards import vector


def session(login, user):
    client, csrf = login(user.username)
    return client, {"X-CSRF-Token": csrf}


def test_specialist_shares_chosen_clause_and_can_withdraw(make_user, login, ready_task, db_sessionmaker, monkeypatch):
    owner = make_user("clause_author")
    colleague = make_user("clause_reader")
    admin = make_user("clause_biz", role="biz_admin")
    c, headers = session(login, owner)
    reader, reader_headers = session(login, colleague)
    a, admin_headers = session(login, admin)
    task = ready_task(owner)
    with db_sessionmaker() as db:
        run = ReviewRun(task_id=task.id, run_no=1, status="completed", stance="sales", source="outgoing",
                        main_doc_type="contract", created_by=owner.id)
        db.add(run)
        db.flush()
        finding = Finding(task_id=task.id, run_id=run.id, seq=1, title="交付条件", category="commercial",
                          severity="medium", cite_status="unverified", problem="约定不清",
                          suggested_text="原审核意见仅为示例。", use_discussed=True,
                          discussed_text="若［［ORG_7］］未按 2028年3月15日 交付，须核实费用人民币88万元、REF_9 和签收凭证。")
        db.add(finding)
        db.commit()
        run_id, fid = run.id, finding.id

    path = f"/api/v1/findings/{fid}/template-reuse"
    shared_path = "/api/v1/admin/templates/shared-clauses"
    assert a.post(path, headers=admin_headers).status_code == 404
    assert c.post(path, headers=headers).json() == {"active": True}
    assert c.post(path, headers=headers).json() == {"active": True}
    assert c.get(f"/api/v1/runs/{run_id}").json()["findings"][0]["shared"]
    assert c.get(shared_path).status_code == 403
    row, = a.get(shared_path).json()["items"]
    assert set(row) == {"id", "text", "doc_type", "stance", "category", "shared_at", "reuse_status"}
    assert row["reuse_status"] == "preparing"
    retry = f"{shared_path}/{row['id']}/retry"
    assert c.post(retry, headers=headers).status_code == 403
    assert a.post(retry, headers=admin_headers).status_code == 409

    def unavailable(texts, kind):
        raise RuntimeError("虚构向量准备失败")

    def run_job():
        with db_sessionmaker() as db:
            claimed = jobs.claim(db, "v351-d02", ("template_embed",))
            assert claimed is not None
            job_id, kind, payload = claimed.id, claimed.type, claimed.payload
        process_job(db_sessionmaker, job_id, kind, payload, heartbeat_seconds=0.05)

    monkeypatch.setattr(job.engine, "embed", unavailable)
    run_job()
    assert a.get(shared_path).json()["items"][0]["reuse_status"] == "failed"
    assert c.get(f"/api/v1/runs/{run_id}").json()["findings"][0]["shared"]
    response = a.post(retry, headers=admin_headers)
    assert response.status_code == 202 and response.json() == {"reuse_status": "preparing"}
    assert a.post(retry, headers=admin_headers).status_code == 409
    assert "签收凭证" in row["text"] and "（待填写）" in row["text"]
    assert not any(x in row["text"] for x in ("ORG_7", "REF_9", "2028", "88", "原审核意见"))

    monkeypatch.setattr(job.engine, "embed", lambda texts, _: [vector() for _ in texts])
    run_job()
    assert a.get(shared_path).json()["items"][0]["reuse_status"] == "ready"
    assert a.post(retry, headers=admin_headers).status_code == 409
    next_task = ready_task(colleague)
    response = reader.post(f"/api/v1/tasks/{next_task.id}/runs", headers=reader_headers)
    assert response.status_code == 201, response.text
    next_run_id = uuid.UUID(response.json()["id"])
    shared_id = uuid.UUID(row["id"])
    nodes = {"F1-P1": DocNodeView("F1-P1", "交付与签收", "交付与签收")}
    with db_sessionmaker() as db:
        stored = db.get(ContentDraft, shared_id)
        assert row["text"].encode() not in stored.text_enc
        assert stored.embedding is not None
        next_run = db.get(ReviewRun, next_run_id)
        assert next_run.use_shared_clauses and next_run.template_id is None
        db.add(TaskClauseVector(task_id=next_task.id, file_seq=1, first_loc="F1-P1", locs=["F1-P1"],
                                embedding=vector(), model="test"))
        db.flush()
        ctx = ReviewContext(next_task, "sales", "outgoing", None, "contract", [], [], "", "")
        compare.start(db, ctx, next_run, nodes)
        compare.prepare(db, ctx, None)
        assert "公司沉淀条文（供参考）" in ctx.template_section and row["text"] in ctx.template_section
        assert not db.scalar(select(LocalFinding).where(LocalFinding.task_id == next_task.id, LocalFinding.check_code == "L-12"))
        next_run.use_shared_clauses = False
        compare.start(db, ctx, next_run, nodes)
        assert ctx.template_candidates == []

    assert reader.delete(path, headers=reader_headers).status_code == 404
    assert c.delete(path, headers=headers).json() == {"active": False}
    assert not c.get(f"/api/v1/runs/{run_id}").json()["findings"][0]["shared"]
    assert a.get(shared_path).json()["items"] == []
    assert a.post(retry, headers=admin_headers).status_code == 404


def test_specialist_shares_for_new_bids_in_the_same_category(make_user, login, db_sessionmaker, env):
    owner = make_user("check_author")
    colleague = make_user("check_reader")
    admin = make_user("check_biz", role="biz_admin")
    grant(db_sessionmaker, owner)
    grant(db_sessionmaker, colleague)
    c, headers = session(login, owner)
    reader, reader_headers = session(login, colleague)
    a, admin_headers = session(login, admin)
    with db_sessionmaker() as db:
        first = BidCategory(name="虚构设备", seq=11)
        second = BidCategory(name="虚构服务", seq=12)
        db.add_all([first, second])
        db.commit()

    def create(client, auth, category_id):
        result = client.post(ROOT, headers=auth, json={**BODY, "category_id": category_id})
        assert result.status_code == 201, result.text
        return f"{ROOT}/{result.json()['id']}/requirements"

    path = create(c, headers, first.id)
    before = create(reader, reader_headers, first.id)
    text = f"须核查{BODY['title']}的签收凭证，最高限价为人民币88万元。"
    added = c.post(path, headers=headers, json={"group": "general", "text": text})
    assert added.status_code == 201, added.text
    original, = added.json()["items"]
    publish = f"{path}/{original['id']}/company-reuse"
    root = "/api/v1/admin/bid-shared-requirements"
    assert reader.post(publish, headers=reader_headers).status_code == 404
    assert c.post(publish, headers=headers).json() == {"active": True}
    assert c.get(root).status_code == 403
    shared, = a.get(root).json()["items"]
    assert shared["category"] == first.name
    assert "签收凭证" in shared["text"] and BODY["title"] not in shared["text"] and "88" not in shared["text"]
    assert c.get(path).json()["items"] == [dict(original, shared=True)]
    assert reader.get(before).json()["company_checks"] == []
    assert reader.get(create(reader, reader_headers, second.id)).json()["company_checks"] == []
    after = create(reader, reader_headers, first.id)
    next_result = reader.get(after).json()
    assert next_result["company_checks"] == [shared["text"]] and next_result["items"] == []

    assert a.delete(f"{root}/{shared['id']}", headers=admin_headers).status_code == 204
    assert reader.get(after).json()["company_checks"] == [shared["text"]]
    assert reader.get(create(reader, reader_headers, first.id)).json()["company_checks"] == []
