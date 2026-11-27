"""V34-B-01a：文件夹、分类与列表筛选的代表性流程；全部虚构。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import BidCategory, Role, UserRole
from tests.review_support import env  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")
ROOT = "/api/v1/bid"


def grant(sm, user, perms):
    with sm() as db:
        role = Role(name="虚构角色" + user.username, permissions=perms)
        db.add(role)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()


@pytest.fixture
def setup(make_user, login, db_sessionmaker):
    with db_sessionmaker() as db:  # 测试库每次清空，按迁移的默认值补两类
        db.add_all([BidCategory(name="工程类", seq=1), BidCategory(name="采购类", seq=2)])
        db.commit()
    user = make_user("folder_owner")
    grant(db_sessionmaker, user, ["use.bid"])
    c, csrf = login(user.username)
    cats = {x["name"]: x["id"] for x in c.get(ROOT + "/categories").json()["items"]}
    return c, {"X-CSRF-Token": csrf}, cats


def test_folders_depth_filters_and_isolation(setup, make_user, login, db_sessionmaker):
    c, h, cats = setup
    one = c.post(ROOT + "/folders", headers=h, json={"name": "2026 年"}).json()["folders"][0]["id"]
    two = next(f["id"] for f in c.post(ROOT + "/folders", headers=h, json={"name": "设备", "parent_id": one}).json()["folders"] if f["parent_id"] == one)
    three = next(f["id"] for f in c.post(ROOT + "/folders", headers=h, json={"name": "空压机", "parent_id": two}).json()["folders"] if f["parent_id"] == two)
    deep = c.post(ROOT + "/folders", headers=h, json={"name": "第四级", "parent_id": three})
    assert deep.status_code == 422 and deep.json()["code"] == "FOLDER_TOO_DEEP"

    body = {"opening_date": "2026-11-20", "policy": "local_only"}
    a = c.post(ROOT, headers=h, json={**body, "title": "虚构空压机采购", "category_id": cats["采购类"], "folder_id": three}).json()["id"]
    b = c.post(ROOT, headers=h, json={**body, "title": "虚构厂房施工", "category_id": cats["工程类"], "opening_date": "2026-12-05"}).json()["id"]
    ids = lambda **q: {x["id"] for x in c.get(ROOT, params=q).json()["items"]}  # noqa: E731
    assert ids(q="空压") == {a} and ids(category=cats["工程类"]) == {b}
    assert ids(folder=one) == {a} and ids(folder="none") == {b}  # 上级文件夹包含子文件夹里的项目
    assert ids(**{"from": "2026-12-01"}) == {b} and ids(stage="preparing") == {a, b}
    # 文件夹计数与右侧搜索、分类、阶段、日期使用同一筛选；空文件夹仍可选择。
    filters = {"q": "空压", "category": cats["采购类"], "stage": "preparing", "from": "2026-11-01", "to": "2026-11-30"}
    filtered = c.get(ROOT + "/folders", params=filters).json()
    assert filtered["all"] == c.get(ROOT, params=filters).json()["total"] == 1
    assert filtered["unfiled"] == 0 and len(filtered["folders"]) == 3
    assert {f["id"]: f["count"] for f in filtered["folders"]} == {one: 0, two: 0, three: 1}
    assert c.get(ROOT + "/folders", params={"from": "2026-12-01"}).json()["unfiled"] == 1
    assert c.get(ROOT + "/folders").json()["all"] == 2

    # 拖动归档、删除文件夹后项目回到未归档
    assert c.put(f"{ROOT}/{b}/folder", headers=h, json={"folder_id": two}).status_code == 200
    tree = c.delete(f"{ROOT}/folders/{one}", headers=h).json()
    assert tree["folders"] == [] and tree["unfiled"] == 2

    # 他人的文件夹与项目 404
    other = make_user("folder_other")
    grant(db_sessionmaker, other, ["use.bid"])
    co, to = login(other.username)
    ho = {"X-CSRF-Token": to}
    mine = c.post(ROOT + "/folders", headers=h, json={"name": "我的"}).json()["folders"][0]["id"]
    assert co.patch(f"{ROOT}/folders/{mine}", headers=ho, json={"name": "改"}).status_code == 404
    assert co.delete(f"{ROOT}/folders/{mine}", headers=ho).status_code == 404
    assert co.get(ROOT, params={"folder": mine}).status_code == 404
    assert co.put(f"{ROOT}/{a}/folder", headers=ho, json={"folder_id": None}).status_code == 404
    theirs = co.post(ROOT, headers=ho, json={**body, "title": "虚构他人项目", "category_id": cats["工程类"]}).json()["id"]
    assert co.put(f"{ROOT}/{theirs}/folder", headers=ho, json={"folder_id": mine}).status_code == 404


def test_opening_view_orders_before_page_and_keeps_date_markers(setup, make_user, login, db_sessionmaker, monkeypatch):
    from app.bid import router

    monkeypatch.setattr(router, "utcnow", lambda: datetime(2026, 11, 26, 16, tzinfo=timezone.utc))
    c, h, cats = setup
    today = datetime(2026, 11, 27).date()
    body = {"policy": "local_only", "category_id": cats["工程类"]}

    def create(client, headers, offset, title):
        response = client.post(ROOT, headers=headers, json={**body, "title": title,
                                                           "opening_date": (today + timedelta(days=offset)).isoformat()})
        assert response.status_code == 201
        return response.json()["id"]

    first = create(c, h, 0, "虚构今日开标")
    later = create(c, h, 7, "虚构七日后开标")
    for n in range(19):
        create(c, h, 1, f"虚构明日开标 {n + 1}")
    other = make_user("ruler_other")
    co, csrf = login(other.username)
    assert co.get(ROOT, params={"opening_view": True}).status_code == 403
    grant(db_sessionmaker, other, ["use.bid"])
    create(co, {"X-CSRF-Token": csrf}, 0, "虚构他人开标")

    query = {"opening_view": True}
    first_page = c.get(ROOT, params=query).json()
    assert first_page["total"] == 21 and first_page["items"][0]["id"] == first
    second_page = c.get(ROOT, params={**query, "page": 2}).json()
    assert [item["id"] for item in second_page["items"]] == [later]
    filtered = c.get(ROOT, params={**query, "from": "2026-11-27", "to": "2026-11-27"}).json()
    assert [item["id"] for item in filtered["items"]] == [first]
    assert first_page["opening_ruler"] == second_page["opening_ruler"] == filtered["opening_ruler"] == {
        "today": "2026-11-27", "start": "2026-11-20", "end": "2026-12-19",
        "days": [{"date": "2026-11-27", "count": 1}, {"date": "2026-11-28", "count": 19},
                 {"date": "2026-12-04", "count": 1}],
    }
    assert "opening_ruler" not in c.get(ROOT).json()


def test_disabled_category_cannot_be_chosen(setup, make_user, login, db_sessionmaker):
    c, h, cats = setup
    admin = make_user("bid_cat_admin")
    grant(db_sessionmaker, admin, ["content.bid_categories"])
    ca, ta = login(admin.username)
    ha = {"X-CSRF-Token": ta}
    body = {"title": "虚构服务项目", "opening_date": "2026-11-20", "policy": "local_only", "category_id": cats["采购类"]}
    pid = c.post(ROOT, headers=h, json=body).json()["id"]
    assert ca.delete(f"/api/v1/admin/bid-categories/{cats['采购类']}", headers=ha).status_code == 409  # 已用，只能停用
    assert ca.patch(f"/api/v1/admin/bid-categories/{cats['采购类']}", headers=ha, json={"enabled": False}).status_code == 200
    refused = c.post(ROOT, headers=h, json=body)
    assert refused.status_code == 422 and refused.json()["code"] == "CATEGORY_UNAVAILABLE"
    # 已用的项目保留原分类，改名等其他设置照常保存
    kept = c.patch(f"{ROOT}/{pid}", headers=h, json={**body, "title": "虚构服务项目二"})
    assert kept.status_code == 200 and kept.json()["category_id"] == cats["采购类"]
    assert c.get("/api/v1/admin/bid-categories").status_code == 403
