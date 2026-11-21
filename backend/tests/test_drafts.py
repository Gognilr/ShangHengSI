"""V35-D-02 多稿对比：对齐与暗改（纯函数）、会话里的认稿、改关系、对比与对照表接口。样本全部虚构。"""
import io
from pathlib import Path
from types import SimpleNamespace

from docx import Document

from app.parsing.docx import parse_docx
from app.review.drafts import compare, name_stem
from tests.test_assistant_sessions import BASE, create, mock_ner, parse, session_client  # noqa: F401
from tests.test_tasks_api import files_dir  # noqa: F401

DRAFTS = Path(__file__).parent / "fixtures" / "drafts"
NAMES = ["设备采购合同_第1稿.docx", "设备采购合同_第2稿.docx", "设备采购合同_第3稿.docx"]


def body(name, seq):
    parsed = parse_docx((DRAFTS / name).read_bytes(), seq)
    return [SimpleNamespace(loc=n.loc, kind=n.kind, clause_no=n.clause_no, page=n.page, has_revision=n.has_revision,
                            text=n.text, before=n.text_before) for n in parsed.nodes if n.part == "body"]


def brief(c):
    b = c["brief"]
    return (c["type"], c["clause"], b.get("text", "")[:4] or f"{b['old']}→{b['new']}", c["untracked"])


def test_three_drafts_tracked_and_silent_changes():
    one, two, three = (body(n, i) for i, n in enumerate(NAMES, 1))
    first = compare(one, two)
    # 留痕的两处为“已留痕”；关修订改的争议解决地、删的保密条款、插入的质量保证为“未留痕”；后面自动编号顺延不算变化
    assert [brief(c) for c in first["changes"]] == [
        ("mod", "第 3 条", "30→60", False), ("add", "第 5 条", "质量保证", True), ("mod", "第 6 条", "0.5%→0.1%", False),
        ("del", "第 6 条", "保密条款", True), ("mod", "第 7 条", "甲→乙", True)]
    assert first["summary"] == {"mod": 3, "add": 1, "del": 1, "move": 0, "format": 0, "untracked": 3}
    second = compare(two, three)
    assert [brief(c) for c in second["changes"]] == [
        ("mod", "第 3 条", "60→30", False), ("mod", "第 4 条", "45→60", True), ("format", "第 8 条", "，→；", True)]
    assert second["summary"]["untracked"] == 1  # 只改标点不计入未留痕


def test_mixed_trace_in_one_paragraph_and_move():
    node = lambda loc, text, before=None: SimpleNamespace(loc=loc, kind="paragraph", clause_no=None, page=None,
                                                         has_revision=before is not None, text=text, before=before)
    old = [node("F1-P1", "付款期限为30日，违约金为0.5%。"), node("F1-P2", "保密期限为三年。"), node("F1-P3", "本合同自签字之日起生效。")]
    # 先悄悄把 0.5% 改成 1%，再开修订把 30 改成 60；保密一段挪到最后
    new = [node("F2-P1", "付款期限为60日，违约金为1%。", "付款期限为30日，违约金为1%。"),
           node("F2-P2", "本合同自签字之日起生效。"), node("F2-P3", "保密期限为三年。")]
    changes = compare(old, new)["changes"]
    mod = changes[0]
    assert mod["untracked"] and mod["mixed"]
    assert [(s["s"], s["u"]) for s in mod["segs"] if s["t"] == "ins"] == [("60", False), ("1%", True)]
    assert [c["type"] for c in changes] == ["mod", "move"]  # 两段互换位置只算一处位置调整
    assert compare(old, new, mode="none")["changes"][0]["untracked"] is None  # PDF、扫描件不判断留痕


def test_name_stem_ignores_draft_words():
    assert name_stem("设备采购合同_第2稿.docx") == name_stem("设备采购合同（1）.docx") == name_stem("设备采购合同-v3 20261118.docx")


def _upload(client, headers, sid, name, draft_of=None):
    data = {"draft_of": draft_of} if draft_of else None
    response = client.post(f"{BASE}/{sid}/files", headers=headers, data=data,
                           files=[("files", (name, (DRAFTS / name).read_bytes(), "application/octet-stream"))])
    assert response.status_code == 200, response.text
    return response.json()


def test_session_links_drafts_diff_and_export(files_dir, session_client, db_sessionmaker, make_user, login):
    c, h, _ = session_client
    sid = create(c, h)["id"]
    _upload(c, h, sid, NAMES[0])
    parse(db_sessionmaker, sid)
    _upload(c, h, sid, NAMES[1])
    parse(db_sessionmaker, sid)
    files = c.get(f"{BASE}/{sid}").json()["files"]
    one, two = files
    assert (one["draft_no"], two["draft_no"], two["prev_file_id"]) == (1, 2, one["id"])  # 相似度自动认稿
    diff = c.get(f"{BASE}/{sid}/diff", params={"file_id": two["id"]}).json()
    assert diff["summary"]["untracked"] == 3 and diff["trace"] == "exact" and diff["prev_no"] == 1
    assert c.get(f"{BASE}/{sid}/diff", params={"file_id": one["id"]}).status_code == 404
    exported = c.get(f"{BASE}/{sid}/diff/export", params={"file_id": two["id"]})
    assert exported.status_code == 200
    table = Document(io.BytesIO(exported.content)).tables[0]
    assert [row.cells[3].text for row in table.rows[1:]] == ["已留修订痕迹", "未留痕", "已留修订痕迹", "未留痕", "未留痕"]
    # 改为独立文件，再改回
    detail = c.put(f"{BASE}/{sid}/files/{two['id']}/draft", headers=h, json={"prev_file_id": None}).json()
    assert all(f["draft_no"] is None for f in detail["files"])
    assert c.put(f"{BASE}/{sid}/files/{one['id']}/draft", headers=h, json={"prev_file_id": two["id"]}).json()["files"][0]["draft_no"] == 2
    assert c.put(f"{BASE}/{sid}/files/{two['id']}/draft", headers=h, json={"prev_file_id": one["id"]}).status_code == 422  # 成环
    # “传新一稿”：直接接在链的最新一稿后面，不靠相似度
    third = _upload(c, h, sid, NAMES[2], draft_of=two["id"])["files"][-1]
    assert third["prev_file_id"] == one["id"] and third["draft_no"] == 3
    # 非本人一律 404
    other, csrf = login(make_user("drafts_other").username)
    assert other.get(f"{BASE}/{sid}/diff", params={"file_id": two["id"]}).status_code == 404
    assert other.put(f"{BASE}/{sid}/files/{two['id']}/draft", headers={"X-CSRF-Token": csrf}, json={"prev_file_id": None}).status_code == 404
