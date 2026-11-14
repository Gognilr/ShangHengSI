"""V3-A 6：全局搜索只返回本人的任务；搜索词中的 % 与 _ 按字面匹配。"""
from app.db.models import Finding, ReviewRun, Task
from app.main import API_PREFIX

URL = API_PREFIX + "/search"


def add_task(db_sessionmaker, owner, title, kind="review"):
    with db_sessionmaker() as db:
        db.add(Task(kind=kind, owner_id=owner.id, title=title, stance="sales", source="incoming",
                    policy="ai", status="reviewed" if kind == "review" else "active"))
        db.commit()


def test_search_only_own_tasks_and_groups(db_sessionmaker, make_user, login):
    alice, bob = make_user("search_alice"), make_user("search_bob")
    add_task(db_sessionmaker, alice, "虚构采购合同.docx")
    add_task(db_sessionmaker, alice, "虚构合同问答", kind="assistant")
    add_task(db_sessionmaker, alice, "无关报价单.docx")
    add_task(db_sessionmaker, bob, "别人的虚构合同.docx")
    client, csrf = login(alice.username)
    response = client.post(URL, json={"q": " 合同 "}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    data = response.json()
    assert [r["title"] for r in data["reviews"]] == ["虚构采购合同.docx"]
    assert [s["title"] for s in data["sessions"]] == ["虚构合同问答"]


def test_search_wildcards_are_literal(db_sessionmaker, make_user, login):
    user = make_user("search_wild")
    add_task(db_sessionmaker, user, "折扣100%_确认.docx")
    add_task(db_sessionmaker, user, "折扣100X确认.docx")
    client, csrf = login(user.username)
    headers = {"X-CSRF-Token": csrf}
    hit = client.post(URL, json={"q": "100%_"}, headers=headers).json()
    assert [r["title"] for r in hit["reviews"]] == ["折扣100%_确认.docx"]
    assert client.post(URL, json={"q": "%"}, headers=headers).json()["reviews"][0]["title"] == "折扣100%_确认.docx"
    assert len(client.post(URL, json={"q": "100_确"}, headers=headers).json()["reviews"]) == 0


def test_search_finds_latest_run_findings_of_own_tasks(db_sessionmaker, make_user, login):
    alice, bob = make_user("search_fa"), make_user("search_fb")
    ids = {}
    for owner in (alice, bob):
        with db_sessionmaker() as db:
            task = Task(kind="review", owner_id=owner.id, title="虚构合同.docx", stance="sales", source="incoming", policy="ai", status="reviewed")
            db.add(task)
            db.flush()
            run = ReviewRun(task_id=task.id, run_no=1, status="completed", stance="sales", source="incoming", main_doc_type="contract", created_by=owner.id)
            db.add(run)
            db.flush()
            task.latest_run_id = run.id
            db.add(Finding(task_id=task.id, run_id=run.id, seq=1, title="违约金过高", category="legal", severity="low", cite_status="missing", problem="虚构说明"))
            db.commit()
            ids[owner.id] = task.id
    client, csrf = login(alice.username)
    data = client.post(URL, json={"q": "违约金"}, headers={"X-CSRF-Token": csrf}).json()
    assert [(f["title"], f["task_id"]) for f in data["findings"]] == [("违约金过高", str(ids[alice.id]))]
