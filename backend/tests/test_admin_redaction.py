"""脱敏设置接口（SPEC 9.10、15.4，设计 M7 第七节）。全部内容虚构。"""
import logging

import pytest
from sqlalchemy import select

from app.admin import settings_store
from app.db.base import utcnow
from app.db.models import AuditLog, DocNode, RedactionTerm
from app.main import API_PREFIX
from app.redaction.engine import term_hash
from app.redaction.recheck import recheck_context
from app.security import crypto
from tests.review_support import env, ready_task  # noqa: F401

SETTING = f"{API_PREFIX}/admin/settings/redaction"
TERMS = f"{API_PREFIX}/admin/terms"
PRODUCT = "煅后石油焦"  # contract_basic 样本中出现的词，默认不会被脱敏


def _hdr(csrf):
    return {"X-CSRF-Token": csrf}


@pytest.fixture
def admin(make_user, login, env):  # noqa: F811
    make_user("boss", role="admin")
    return login("boss")


def _add(c, csrf, term):
    return c.post(TERMS, json={"term": term}, headers=_hdr(csrf))


def _learned(sm, text, *, our=False, category="ORG", enabled=True, created_by=None):
    with sm() as s:
        t = RedactionTerm(kind="learned", term_enc=crypto.encrypt_text(text), term_hash=term_hash(text),
                          category=category, is_our_party=our, enabled=enabled, hit_count=3,
                          created_by=created_by, created_at=utcnow())
        s.add(t)
        s.commit()
        return t.id


def _audits(sm):
    with sm() as s:
        return [(r.target_type, r.target_id, r.detail) for r in s.scalars(
            select(AuditLog).where(AuditLog.action == "setting_update").order_by(AuditLog.id))]


def _task_texts(sm, task):
    with sm() as s:
        return [n.redacted_text or "" for n in s.scalars(select(DocNode).where(DocNode.task_id == task.id))]


def test_permissions(make_user, login, api):
    make_user("alice")
    c, csrf = login("alice")
    uid = "00000000-0000-0000-0000-000000000000"
    assert c.get(SETTING).status_code == 403
    assert c.put(SETTING, json={"mask_quantity": True}, headers=_hdr(csrf)).status_code == 403
    assert c.get(TERMS, params={"kind": "custom"}).status_code == 403
    assert c.post(TERMS, json={"term": "abc"}, headers=_hdr(csrf)).status_code == 403
    assert c.patch(f"{TERMS}/{uid}", json={"enabled": False}, headers=_hdr(csrf)).status_code == 403
    assert c.delete(f"{TERMS}/{uid}", headers=_hdr(csrf)).status_code == 403
    assert api.get(SETTING).status_code == 401


def test_mask_quantity_toggle_and_audit(admin, db_sessionmaker):
    c, csrf = admin
    assert c.get(SETTING).json() == {"mask_quantity": False}
    assert c.put(SETTING, json={"mask_quantity": True}, headers=_hdr(csrf)).json() == {"mask_quantity": True}
    assert c.get(SETTING).json() == {"mask_quantity": True}
    c.put(SETTING, json={"mask_quantity": True}, headers=_hdr(csrf))  # 没有变化，不写审计
    c.put(SETTING, json={"mask_quantity": False}, headers=_hdr(csrf))
    assert _audits(db_sessionmaker) == [
        ("setting", "redaction", {"keys": ["redaction.mask_quantity"]}),
        ("setting", "redaction", {"keys": ["redaction.mask_quantity"]}),
    ]
    assert c.put(SETTING, json={"mask_quantity": "x"}, headers=_hdr(csrf)).status_code == 422
    assert c.put(SETTING, json={}, headers=_hdr(csrf)).status_code == 422


def test_create_custom_term(admin, db_sessionmaker):
    c, csrf = admin
    r = _add(c, csrf, "  内部项目代号青松  ")
    assert r.status_code == 201
    out = r.json()
    assert out["kind"] == "custom" and out["term"] == "内部项目代号青松" and out["category"] == "CUSTOM"
    assert out["enabled"] is True and out["hit_count"] == 0 and out["created_by_name"] == "boss"
    with db_sessionmaker() as s:
        row = s.scalar(select(RedactionTerm))
        assert b"\xe9\x9d\x92\xe6\x9d\xbe" not in row.term_enc  # 密文中没有明文字节
        assert row.term_hash == term_hash("内部项目代号青松")


@pytest.mark.parametrize("bad", ["", " ", "a", " 中 ", "x" * 101])
def test_create_validation(admin, bad):
    c, csrf = admin
    assert _add(c, csrf, bad).status_code == 422


def test_create_length_boundaries(admin):
    c, csrf = admin
    assert _add(c, csrf, "ab").status_code == 201
    assert _add(c, csrf, "字" * 100).status_code == 201


def test_term_exists_by_normalized_form(admin):
    c, csrf = admin
    assert _add(c, csrf, "Project Falcon").status_code == 201
    for dup in ("project falcon", "Project  Falcon", "ＰＲＯＪＥＣＴ ＦＡＬＣＯＮ"):
        r = _add(c, csrf, dup)
        assert r.status_code == 409 and r.json()["code"] == "TERM_EXISTS", dup


def test_custom_term_may_equal_a_learned_name(admin, db_sessionmaker):
    c, csrf = admin
    _learned(db_sessionmaker, "Fictional Metals Trading FZE")
    assert _add(c, csrf, "Fictional Metals Trading FZE").status_code == 201


def test_term_in_prompt_rejected(admin, db_sessionmaker):
    c, csrf = admin
    r = _add(c, csrf, "审核")  # 提示词里必然出现
    assert r.status_code == 422 and r.json()["code"] == "TERM_IN_PROMPT"
    assert c.get(TERMS, params={"kind": "custom"}).json()["total"] == 0


def test_list_requires_kind_and_separates_kinds(admin, db_sessionmaker):
    c, csrf = admin
    _add(c, csrf, "自定义甲乙")
    _learned(db_sessionmaker, "已记住公司")
    assert c.get(TERMS).status_code == 422
    assert c.get(TERMS, params={"kind": "other"}).status_code == 422
    custom = c.get(TERMS, params={"kind": "custom"}).json()
    learned = c.get(TERMS, params={"kind": "learned"}).json()
    assert [i["term"] for i in custom["items"]] == ["自定义甲乙"]
    assert [i["term"] for i in learned["items"]] == ["已记住公司"]
    assert learned["items"][0]["hit_count"] == 3 and learned["items"][0]["is_our_party"] is False
    assert (custom["page"], custom["page_size"]) == (1, 20)


def test_pagination_and_order(admin, db_sessionmaker):
    c, csrf = admin
    for i in range(25):
        assert _add(c, csrf, f"词条{i:02d}号").status_code == 201
    p1 = c.get(TERMS, params={"kind": "custom"}).json()
    p2 = c.get(TERMS, params={"kind": "custom", "page": 2}).json()
    p3 = c.get(TERMS, params={"kind": "custom", "page": 3}).json()
    assert (p1["total"], len(p1["items"]), len(p2["items"]), p3["items"]) == (25, 20, 5, [])
    assert p1["items"][0]["term"] == "词条24号" and p2["items"][-1]["term"] == "词条00号"  # 新的在前
    assert c.get(TERMS, params={"kind": "custom", "page": 0}).status_code == 422


def test_search_chinese_english_case_and_fullwidth(admin, db_sessionmaker):
    c, csrf = admin
    _learned(db_sessionmaker, "星河虚构进出口有限公司")
    _learned(db_sessionmaker, "Fictional Metals Trading FZE", category="ORG")
    _learned(db_sessionmaker, "周示例", category="PERSON")

    def search(q):
        return sorted(i["term"] for i in c.get(TERMS, params={"kind": "learned", "q": q}).json()["items"])

    assert search("星河") == ["星河虚构进出口有限公司"]
    assert search("metals") == ["Fictional Metals Trading FZE"]
    assert search("ＭＥＴＡＬＳ") == ["Fictional Metals Trading FZE"]  # 全角
    assert search("metals trading") == ["Fictional Metals Trading FZE"]  # 空白忽略
    assert search("   ") and len(search("   ")) == 3  # 空白等于不筛选
    assert search("不存在的词") == []
    r = c.get(TERMS, params={"kind": "learned", "q": "示例"}).json()
    assert r["total"] == 1 and r["items"][0]["category"] == "PERSON"


def test_patch_enabled_custom_only(admin, db_sessionmaker):
    c, csrf = admin
    tid = _add(c, csrf, "青松计划").json()["id"]
    r = c.patch(f"{TERMS}/{tid}", json={"enabled": False}, headers=_hdr(csrf))
    assert r.status_code == 200 and r.json()["enabled"] is False
    lid = _learned(db_sessionmaker, "某已记住公司")
    r = c.patch(f"{TERMS}/{lid}", json={"enabled": False}, headers=_hdr(csrf))
    assert r.status_code == 409 and r.json()["code"] == "TERM_NOT_EDITABLE"
    assert c.patch(f"{TERMS}/{tid}", json={"enabled": "x"}, headers=_hdr(csrf)).status_code == 422


@pytest.mark.parametrize("uid", ["bad-id", "00000000-0000-0000-0000-000000000000"])
def test_not_found(admin, uid):
    c, csrf = admin
    assert c.patch(f"{TERMS}/{uid}", json={"enabled": True}, headers=_hdr(csrf)).json()["code"] == "TERM_NOT_FOUND"
    assert c.delete(f"{TERMS}/{uid}", headers=_hdr(csrf)).status_code == 404


def test_delete_learned_and_custom(admin, db_sessionmaker):
    c, csrf = admin
    lid = _learned(db_sessionmaker, "某已记住公司")
    cid = _add(c, csrf, "青松计划").json()["id"]
    assert c.delete(f"{TERMS}/{lid}", headers=_hdr(csrf)).status_code == 204
    assert c.delete(f"{TERMS}/{cid}", headers=_hdr(csrf)).status_code == 204
    assert c.get(TERMS, params={"kind": "learned"}).json()["total"] == 0
    assert c.get(TERMS, params={"kind": "custom"}).json()["total"] == 0


def test_custom_term_applies_to_new_tasks_then_disable_and_delete(admin, make_user, db_sessionmaker, ready_task):  # noqa: F811
    c, csrf = admin
    owner = make_user("alice")
    tid = _add(c, csrf, PRODUCT).json()["id"]

    task = ready_task(owner)
    texts = " ".join(_task_texts(db_sessionmaker, task))
    assert PRODUCT not in texts and "敏感词_" in texts
    with db_sessionmaker() as s:
        assert PRODUCT in recheck_context(s, None).terms

    c.patch(f"{TERMS}/{tid}", json={"enabled": False}, headers=_hdr(csrf))
    task2 = ready_task(owner)
    assert PRODUCT in " ".join(_task_texts(db_sessionmaker, task2))
    with db_sessionmaker() as s:
        assert PRODUCT not in recheck_context(s, None).terms

    c.patch(f"{TERMS}/{tid}", json={"enabled": True}, headers=_hdr(csrf))
    c.delete(f"{TERMS}/{tid}", headers=_hdr(csrf))
    with db_sessionmaker() as s:
        assert PRODUCT not in recheck_context(s, None).terms
    # 已存在的任务不会自动重算
    assert PRODUCT not in " ".join(_task_texts(db_sessionmaker, task))


def test_audit_has_no_term_text_and_logs_clean(admin, db_sessionmaker, caplog):
    c, csrf = admin
    secret = "独特敏感词QWERTY"
    with caplog.at_level(logging.DEBUG):
        tid = _add(c, csrf, secret).json()["id"]
        c.get(TERMS, params={"kind": "custom", "q": secret})
        c.patch(f"{TERMS}/{tid}", json={"enabled": False}, headers=_hdr(csrf))
        c.delete(f"{TERMS}/{tid}", headers=_hdr(csrf))
    audits = _audits(db_sessionmaker)
    assert audits == [
        ("term", tid, {"kind": "custom", "op": "create"}),
        ("term", tid, {"kind": "custom", "op": "update", "enabled": False}),
        ("term", tid, {"kind": "custom", "op": "delete"}),
    ]
    assert secret not in str(audits)
    assert secret not in caplog.text
