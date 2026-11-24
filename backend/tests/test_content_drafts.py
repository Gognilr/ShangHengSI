"""V35-D-05：用虚构结果验证提交、审批与后续审核的闭环。"""
import uuid

from sqlalchemy import select

from app.db.models import BidCategory, ContentDraft, Finding, ReviewRun, StandardClause, TaskClauseVector
from app.review.prompt import ReviewContext
from app.review.validate import DocNodeView
from app.security import crypto
from app.standards import compare, job, service as standards
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_bid_projects import BODY, ROOT, grant
from tests.test_standards import document, vector


def session(login, user):
    client, csrf = login(user.username)
    return client, {"X-CSRF-Token": csrf}


def test_template_draft_uses_chosen_text_and_participates_in_next_review(make_user, login, ready_task, db_sessionmaker, monkeypatch):
    owner = make_user("draft_author")
    admin = make_user("draft_biz", role="biz_admin")
    c, headers = session(login, owner)
    a, admin_headers = session(login, admin)
    task = ready_task(owner)
    with db_sessionmaker() as db:
        row = standards.create(db, document(1), dict(name="虚构交付范本", doc_types=["contract"], stances=["sales"]), admin.id)
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
        tid, fid = row.id, finding.id
    root = "/api/v1/admin/templates"
    path = f"/api/v1/findings/{fid}/template-draft"
    assert a.post(path, headers=admin_headers).status_code == 404
    assert c.get(root + "/drafts").status_code == 403
    assert c.post(path, headers=headers).json() == {"status": "pending"}
    assert c.post(path, headers=headers).json() == {"status": "pending"}
    drafts = a.get(root + "/drafts").json()["items"]
    assert len(drafts) == 1
    draft = drafts[0]
    assert set(draft) == {"id", "text", "doc_type", "stance", "category", "created_at"}
    assert "签收凭证" in draft["text"] and "（待填写）" in draft["text"]
    assert not any(x in draft["text"] for x in ("ORG_7", "REF_9", "2028", "88", "原审核意见"))
    assert a.get(f"{root}/{tid}").json()["clause_count"] == 1
    approval = f"{root}/drafts/{draft['id']}/approve"
    assert c.post(approval, headers=headers, json={"template_id": str(tid)}).status_code == 403
    assert a.post(approval, headers=admin_headers, json={"template_id": str(tid)}).status_code == 204
    assert a.post(approval, headers=admin_headers, json={"template_id": str(tid)}).status_code == 409
    assert a.get(root + "/drafts").json()["items"] == []

    monkeypatch.setattr(job.engine, "embed", lambda texts, _: [vector() for _ in texts])
    next_task = ready_task(owner)
    with db_sessionmaker() as db:
        stored = db.get(ContentDraft, uuid.UUID(draft["id"]))
        assert draft["text"].encode() not in stored.text_enc
        job.handle_embed(db, {"template_id": str(tid)})
        clause = db.scalar(select(StandardClause).where(StandardClause.template_id == tid, StandardClause.seq == 2))
        assert crypto.decrypt_text(clause.text_enc) == draft["text"] and clause.embedding is not None
        previous = db.scalar(select(StandardClause).where(StandardClause.template_id == tid, StandardClause.seq == 1))
        previous.embedding = vector(0)
        existing = db.scalar(select(TaskClauseVector).where(TaskClauseVector.task_id == next_task.id).limit(1))
        if existing is None:
            db.add(TaskClauseVector(task_id=next_task.id, file_seq=1, first_loc="F1-P1", locs=["F1-P1"],
                                    embedding=vector(), model="test"))
        else:
            existing.embedding = vector()
        db.flush()
        template = standards.choose(db, next_task, "contract")
        assert template.id == tid
        next_run = ReviewRun(task_id=next_task.id, run_no=1, status="running", stance="sales", source="outgoing",
                             main_doc_type="contract", created_by=owner.id, template_id=template.id,
                             template_name_snapshot_enc=template.name_enc, template_hash=template.content_hash)
        db.add(next_run)
        db.flush()
        ctx = ReviewContext(next_task, "sales", "outgoing", None, "contract", [], [], "", "")
        compare.start(db, ctx, next_run, {"F1-P1": DocNodeView("F1-P1", "交付与签收", "交付与签收")})
        compare.prepare(db, ctx, None)
        assert draft["text"] in ctx.template_section


def test_bid_draft_only_appears_as_a_manual_check_in_later_projects(make_user, login, db_sessionmaker, env):
    owner = make_user("check_author")
    admin = make_user("check_biz", role="biz_admin")
    grant(db_sessionmaker, owner)
    grant(db_sessionmaker, admin)
    c, headers = session(login, owner)
    a, admin_headers = session(login, admin)
    with db_sessionmaker() as db:
        first = BidCategory(name="虚构设备", seq=11)
        second = BidCategory(name="虚构服务", seq=12)
        db.add_all([first, second])
        db.commit()

    def create(category_id):
        result = c.post(ROOT, headers=headers, json={**BODY, "category_id": category_id})
        assert result.status_code == 201, result.text
        return f"{ROOT}/{result.json()['id']}/requirements"

    path = create(first.id)
    text = f"须核查{BODY['title']}的签收凭证，最高限价为人民币88万元。"
    added = c.post(path, headers=headers, json={"group": "general", "text": text})
    assert added.status_code == 201, added.text
    submit = f"{path}/{added.json()['items'][0]['id']}/company-draft"
    root = "/api/v1/admin/bid-requirement-drafts"
    assert a.post(submit, headers=admin_headers).status_code == 404
    assert c.post(submit, headers=headers).json() == {"status": "pending"}
    before = create(first.id)
    assert c.get(before).json()["company_checks"] == []
    assert c.get(root).status_code == 403
    draft = a.get(root).json()["items"][0]
    assert draft["category"] == first.name
    assert "签收凭证" in draft["text"] and BODY["title"] not in draft["text"] and "88" not in draft["text"]
    assert a.post(f"{root}/{draft['id']}/approve", headers=admin_headers).status_code == 204
    assert c.get(path).json()["items"] == added.json()["items"]
    assert c.get(before).json()["company_checks"] == []
    assert c.get(create(second.id)).json()["company_checks"] == []
    next_result = c.get(create(first.id)).json()
    assert next_result["company_checks"] == [draft["text"]] and next_result["items"] == []
