"""公司补充要求、公司说明、版本历史（SPEC 15.3、18.21、18.22，T-14，设计 M7 第六节）。全部内容虚构。"""
import io
import threading

import pytest
from docx import Document
from sqlalchemy import func, select, text

from app.admin import settings_store
from app.db.models import AuditLog, CompanyRequirement, RequirementResult, RequirementVersion, ReviewRun, Task
from app.db.models import RedactionTerm
from app.main import API_PREFIX
from app.review import job, service
from app.review.prompt import fixed_prompt_text
from app.security import crypto
from tests.review_support import env, llm, ready_task  # noqa: F401

REQ = f"{API_PREFIX}/admin/company-requirements"
PROFILE = f"{API_PREFIX}/admin/company-profile"
VERSIONS = f"{API_PREFIX}/admin/requirement-versions"
OPEN = f"{API_PREFIX}/requirements"


def _hdr(csrf):
    return {"X-CSRF-Token": csrf}


def body(**over):
    b = {"text": "付款条款必须写明开户银行名称", "doc_types": ["all"], "stances": ["all"], "enabled": True, "sort": 1}
    b.update(over)
    return b


@pytest.fixture
def admin(make_user, login, env):  # noqa: F811
    make_user("boss", role="admin")
    return login("boss")


def _create(c, csrf, **over):
    return c.post(REQ, json=body(**over), headers=_hdr(csrf))


def _versions(sm):
    with sm() as s:
        return list(s.scalars(select(RequirementVersion).order_by(RequirementVersion.version)))


def _audits(sm):
    with sm() as s:
        return [(r.target_type, r.target_id, r.detail) for r in s.scalars(
            select(AuditLog).where(AuditLog.action == "setting_update").order_by(AuditLog.id))]


# ---------------------------------------------------------------- 权限与基本增删改


def test_permissions(make_user, login, api):
    make_user("alice")
    c, csrf = login("alice")
    uid = "00000000-0000-0000-0000-000000000000"
    assert c.get(REQ).status_code == 403
    assert _create(c, csrf).status_code == 403
    assert c.put(f"{REQ}/{uid}", json=body(), headers=_hdr(csrf)).status_code == 403
    assert c.delete(f"{REQ}/{uid}", headers=_hdr(csrf)).status_code == 403
    assert c.get(PROFILE).status_code == 403
    assert c.put(PROFILE, json={"text": "x"}, headers=_hdr(csrf)).status_code == 403
    assert c.get(VERSIONS).status_code == 403
    assert api.get(REQ).status_code == 401


def test_create_update_delete_flow(admin, db_sessionmaker):
    c, csrf = admin
    r = _create(c, csrf)
    assert r.status_code == 201
    row = r.json()
    assert row["code"].startswith("CO-") and row["enabled"] is True and row["doc_types"] == ["all"]
    listing = c.get(REQ).json()
    assert [i["code"] for i in listing["items"]] == [row["code"]] and listing["version"] == 1

    r = c.put(f"{REQ}/{row['id']}", json=body(text="改后的要求文字", enabled=False, sort=5,
                                               doc_types=["contract", "quotation"], stances=["sales"]),
              headers=_hdr(csrf))
    assert r.status_code == 200
    assert r.json()["text"] == "改后的要求文字" and r.json()["enabled"] is False
    assert r.json()["doc_types"] == ["contract", "quotation"] and r.json()["code"] == row["code"]

    assert c.delete(f"{REQ}/{row['id']}", headers=_hdr(csrf)).status_code == 204
    assert c.get(REQ).json() == {"items": [], "version": 3}
    assert c.delete(f"{REQ}/{row['id']}", headers=_hdr(csrf)).json()["code"] == "REQUIREMENT_NOT_FOUND"


@pytest.mark.parametrize("uid", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_not_found(admin, uid):
    c, csrf = admin
    assert c.put(f"{REQ}/{uid}", json=body(), headers=_hdr(csrf)).status_code == 404
    assert c.delete(f"{REQ}/{uid}", headers=_hdr(csrf)).status_code == 404


@pytest.mark.parametrize(
    "over",
    [
        {"text": ""},
        {"text": "   "},
        {"text": "字" * 201},
        {"doc_types": []},
        {"doc_types": ["all", "contract"]},
        {"doc_types": ["contract", "bogus"]},
        {"stances": []},
        {"stances": ["sales", "all"]},
        {"stances": ["buyer"]},
        {"sort": -1},
        {"sort": 10000},
        {"enabled": "maybe"},
        {"extra": 1},
    ],
)
def test_validation(admin, over):
    c, csrf = admin
    r = _create(c, csrf, **over)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR", over


def test_validation_boundaries_and_dedup(admin):
    c, csrf = admin
    r = _create(c, csrf, text="字" * 200, sort=0, doc_types=["contract", "contract"], stances=["sales", "purchase", "other"])
    assert r.status_code == 201 and r.json()["doc_types"] == ["contract"]
    assert _create(c, csrf, sort=9999).status_code == 201


# ---------------------------------------------------------------- 上限与编号


def test_limit_30_and_codes_not_reused(admin):
    c, csrf = admin
    rows = [_create(c, csrf, text=f"第{i}条要求", sort=i).json() for i in range(30)]
    r = _create(c, csrf, text="多出来的一条")
    assert r.status_code == 409 and r.json()["code"] == "COMPANY_REQ_LIMIT"
    assert r.json()["message"] == "公司补充要求最多 30 条"
    nums = [int(x["code"][3:]) for x in rows]
    assert nums == sorted(nums) and len(set(nums)) == 30
    # 停用的也占名额；删除后可再加，但编号不复用
    assert c.delete(f"{REQ}/{rows[-1]['id']}", headers=_hdr(csrf)).status_code == 204
    again = _create(c, csrf, text="补一条")
    assert again.status_code == 201
    assert int(again.json()["code"][3:]) > max(nums)


# ---------------------------------------------------------------- 敏感内容拒绝


def test_sensitive_text_rejected_without_echo(admin, db_sessionmaker):
    c, csrf = admin
    with db_sessionmaker() as s:
        s.add(RedactionTerm(kind="custom", term_enc=crypto.encrypt_text("星河虚构"), term_hash="h" * 64,
                            category="CUSTOM", enabled=True, is_our_party=False, hit_count=0))
        s.commit()
    for bad in ("联系电话 13800138000 付款", "合同总额 USD 12,345.00 以内", "须通知星河虚构公司"):
        r = _create(c, csrf, text=bad)
        assert r.status_code == 422 and r.json()["code"] == "REQ_TEXT_SENSITIVE", bad
        assert "13800138000" not in r.text and "星河虚构" not in r.text and "12,345" not in r.text
    assert c.put(PROFILE, json={"text": "联系 13800138000"}, headers=_hdr(csrf)).json()["code"] == "REQ_TEXT_SENSITIVE"
    assert c.get(REQ).json()["items"] == []
    assert _versions(db_sessionmaker) == []


def test_sensitive_checked_on_update_only_when_text_changes(admin, db_sessionmaker):
    c, csrf = admin
    row = _create(c, csrf).json()
    r = c.put(f"{REQ}/{row['id']}", json=body(text="新增电话 13800138000"), headers=_hdr(csrf))
    assert r.json()["code"] == "REQ_TEXT_SENSITIVE"


# ---------------------------------------------------------------- 版本与审计


def test_every_change_creates_version_with_enabled_only_snapshot(admin, db_sessionmaker):
    c, csrf = admin
    a = _create(c, csrf, text="要求甲", sort=2).json()
    b = _create(c, csrf, text="要求乙", sort=1, enabled=False).json()
    c.put(f"{REQ}/{b['id']}", json=body(text="要求乙", sort=1, enabled=True), headers=_hdr(csrf))
    c.put(PROFILE, json={"text": "我们是一家虚构的贸易公司"}, headers=_hdr(csrf))
    c.delete(f"{REQ}/{a['id']}", headers=_hdr(csrf))
    versions = _versions(db_sessionmaker)
    assert [v.version for v in versions] == [1, 2, 3, 4, 5]
    assert [r["text"] for r in versions[0].snapshot["requirements"]] == ["要求甲"]  # 乙停用，不进快照
    assert [r["text"] for r in versions[2].snapshot["requirements"]] == ["要求乙", "要求甲"]  # 按 sort
    assert versions[3].snapshot["company_profile"] == "我们是一家虚构的贸易公司"
    assert [r["text"] for r in versions[4].snapshot["requirements"]] == ["要求乙"]
    assert set(versions[0].snapshot["requirements"][0]) == {"code", "text", "doc_types", "stances", "sort"}

    listing = c.get(VERSIONS).json()["items"]
    assert [v["version"] for v in listing] == [5, 4, 3, 2, 1]
    assert listing[0]["created_by_name"] == "boss"
    assert c.get(PROFILE).json() == {"text": "我们是一家虚构的贸易公司", "version": 5}


def test_profile_empty_allowed_and_length_limit(admin):
    c, csrf = admin
    assert c.put(PROFILE, json={"text": ""}, headers=_hdr(csrf)).status_code == 200
    assert c.put(PROFILE, json={"text": "字" * 300}, headers=_hdr(csrf)).status_code == 200
    assert c.put(PROFILE, json={"text": "字" * 301}, headers=_hdr(csrf)).status_code == 422


def test_audit_detail_has_no_text(admin, db_sessionmaker):
    c, csrf = admin
    row = _create(c, csrf, text="独特的要求文字ABC").json()
    c.put(PROFILE, json={"text": "独特的公司说明XYZ"}, headers=_hdr(csrf))
    c.put(f"{REQ}/{row['id']}", json=body(text="独特的要求文字ABC", sort=3), headers=_hdr(csrf))
    c.delete(f"{REQ}/{row['id']}", headers=_hdr(csrf))
    audits = _audits(db_sessionmaker)
    assert audits == [
        ("company_requirement", row["code"], {"op": "create", "version": 1}),
        ("company_profile", None, {"op": "update", "version": 2}),
        ("company_requirement", row["code"], {"op": "update", "version": 3}),
        ("company_requirement", row["code"], {"op": "delete", "version": 4}),
    ]


def test_concurrent_creates_keep_versions_consecutive(admin, db_sessionmaker, login):
    c, csrf = admin
    results = []
    barrier = threading.Barrier(4)

    def run(i):
        client, token = login("boss")
        barrier.wait()
        results.append(_create(client, token, text=f"并发要求{i}", sort=i).status_code)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [201] * 4
    assert [v.version for v in _versions(db_sessionmaker)] == [1, 2, 3, 4]


# ---------------------------------------------------------------- 与审核流程的联动（T-14）


def test_new_requirement_visible_in_drawer_api_and_prompt(admin, db_sessionmaker):
    c, csrf = admin
    row = _create(c, csrf, text="新增要求：交货须附装箱单", stances=["sales"]).json()
    d = c.get(OPEN, params={"stance": "sales", "source": "outgoing"}).json()
    assert [x["code"] for x in d["company"]] == [row["code"]] and d["company_version"] == 1
    assert c.get(OPEN, params={"stance": "purchase", "source": "outgoing"}).json()["company"] == []
    with db_sessionmaker() as s:
        assert "新增要求：交货须附装箱单" in fixed_prompt_text(s)


def test_t14_full_chain(admin, make_user, login, db_sessionmaker, ready_task, llm):  # noqa: F811
    c, csrf = admin
    row = _create(c, csrf, text="新增要求：交货须附装箱单", stances=["sales"]).json()
    version = c.get(REQ).json()["version"]
    owner = make_user("alice")
    task = ready_task(owner)
    requests = llm("normal")
    with db_sessionmaker() as db:
        run = service.create_run(db, db.get(Task, task.id), owner, None)
    with db_sessionmaker() as db:
        job.run_review(db, run.id)
    assert any(row["code"] in r.content.decode() for r in requests)
    with db_sessionmaker() as db:
        r = db.get(ReviewRun, run.id)
        assert r.company_req_version == version
        ids = [x.req_id for x in db.scalars(select(RequirementResult).where(RequirementResult.run_id == run.id))]
        assert row["code"] in ids
    # 报告基本信息写出新版本号
    oc, _ = login("alice")
    resp = oc.get(f"{API_PREFIX}/runs/{run.id}/report")
    assert resp.status_code == 200
    doc = Document(io.BytesIO(resp.content))
    flat = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
        cell.text for t in doc.tables for rw in t.rows for cell in rw.cells)
    assert f"公司补充要求 第 {version} 版" in flat


# ---------------------------------------------------------------- GET /requirements 参数可选（Q-113）


def test_open_requirements_without_stance_and_source(admin, db_sessionmaker, make_user, login):
    c, csrf = admin
    _create(c, csrf, text="仅采购立场的公司要求", stances=["purchase"])
    d = c.get(OPEN).json()
    assert len(d["builtin"]) == 44 or len(d["builtin"]) > 0
    # 不按立场筛选公司补充要求
    assert [x["text"] for x in d["company"]] == ["仅采购立场的公司要求"]
    ids = [x["id"] for x in d["builtin"]]
    sales_out = {x["id"] for x in c.get(OPEN, params={"stance": "sales", "source": "outgoing"}).json()["builtin"]}
    purchase_in = {x["id"] for x in c.get(OPEN, params={"stance": "purchase", "source": "incoming"}).json()["builtin"]}
    assert sales_out | purchase_in <= set(ids)
    assert "C-13" in ids and "C-14" in ids  # 销售专属、采购专属的都在
    contract = c.get(OPEN, params={"doc_type": "contract"}).json()["builtin"]
    assert contract and all("contract" in x["doc_types"] or "all" in x["doc_types"] for x in contract)
    assert len(contract) < len(ids)
    assert c.get(OPEN, params={"doc_type": "bogus"}).status_code == 422
    # 只给一个条件时只按该条件筛选
    only_stance = c.get(OPEN, params={"stance": "sales"}).json()["builtin"]
    assert "C-13" in [x["id"] for x in only_stance] and "C-14" not in [x["id"] for x in only_stance]
    # 普通用户同样可读
    make_user("alice")
    uc, _ = login("alice")
    assert uc.get(OPEN).status_code == 200


def test_sequence_declared_and_migration_head(db_sessionmaker):
    with db_sessionmaker() as s:
        assert s.scalar(text("SELECT count(*) FROM pg_sequences WHERE sequencename='company_requirement_code_seq'")) == 1
