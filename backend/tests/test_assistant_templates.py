"""T-50：五个模板的参数、固定消息、原样说明与回复草稿边界。"""
import uuid
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
import httpx
import json
from sqlalchemy import select
from app.db.models import AssistantMessage, DocNode, TaskFile
from app.redaction import engine
from app.security import crypto
from app.llm import gateway
from tests.review_support import env  # noqa: F401
from tests.test_assistant_chat import BASE, conversation, run  # noqa: F401
from tests.test_gateway_stream import Events

FILES = {str(uuid.UUID(int=i)): i for i in range(1, 8)}
IDS = list(FILES)


@pytest.mark.parametrize("template,params,message", [
    ("translate", {"file_ids": IDS[:2], "target": "英文"}, ""),
    ("translate", {"file_id": IDS[0], "target": "意大利文"}, ""),
    ("translate", {"target": "英文"}, ""),
    ("summary", {"file_ids": IDS[:4]}, ""),
    ("summary", {"file_ids": IDS[:1], "focus": "自定义正文"}, ""),
    ("crosscheck", {"file_ids": IDS[:1]}, ""),
    ("compare", {"file_ids": IDS[:7]}, ""),
    ("compare", {"file_ids": [IDS[0], IDS[0]]}, ""),
    ("reply", {"file_ids": [], "tone": "正式", "language": "中文"}, ""),
    ("reply", {"file_ids": IDS[:1], "tone": "强硬", "language": "中文"}, ""),
    ("reply", {"file_ids": IDS[:1], "tone": "正式", "language": "法文"}, ""),
    ("summary", {"file_ids": [str(uuid.uuid4())]}, ""),
])
def test_t50_1_invalid_parameters(template, params, message):
    from app.assistant.templates import prepare
    from app.security.errors import AppError
    with pytest.raises(AppError) as caught:
        prepare(template, params, FILES, message)
    assert caught.value.status == 422


@pytest.mark.parametrize("target", ["中文", "英文", "日文", "韩文", "俄文", "西班牙文", "法文", "德文", "阿拉伯文"])
def test_all_translation_targets(target):
    from app.assistant.templates import prepare
    result = prepare("translate", {"file_id": IDS[1], "target": target}, FILES)
    assert result.params["file_ids"] == [IDS[1]] and result.params["target"] == target
    assert result.message == f"把文件 2 翻译为{target}"


def test_template_defaults_and_pasted_reply():
    from app.assistant.templates import prepare
    summary = prepare("summary", {"file_ids": IDS[:2]}, FILES)
    assert summary.params["focus"] == "全部" and summary.message == "概括文件 1、2 的要点"
    reply = prepare("reply", {"file_ids": [], "tone": "正式", "language": "同来函"}, FILES, "普通来函正文")
    assert reply.message == "普通来函正文"
    assert prepare("crosscheck", {"file_ids": IDS[:3]}, FILES).message == "核对文件 1～3 之间是否一致"
    assert prepare("compare", {"file_ids": IDS[:3]}, FILES).message == "比较文件 1～3 的报价"


def test_t50_11_draft_only():
    from app.assistant.templates import draft_only
    assert draft_only("对方要求说明\n\n---\n\n尊敬的客户：这是草稿") == "尊敬的客户：这是草稿"
    assert draft_only("没有草稿分隔线") == ""


@pytest.mark.parametrize("template", ["translate", "summary", "reply", "crosscheck", "compare"])
def test_t50_12_literal_template_resource(template):
    from app.assistant.templates import instruction, RESOURCE
    text = RESOURCE.read_text(encoding="utf-8")
    block = text.split(f"## {template}\n", 1)[1].split("\n## ", 1)[0].strip()
    assert instruction(template) == block


@pytest.mark.parametrize("template,options", [("summary", {}), ("reply", {"tone": "正式", "language": "中文"})])
def test_t50_11_12_template_instruction_in_actual_request(conversation, db_sessionmaker, monkeypatch, template, options):
    from app.assistant.templates import instruction
    from app.assistant.prompt import system_prompt
    c, h, tid, _ = conversation
    with db_sessionmaker() as db:
        fid = str(db.scalar(select(TaskFile.id).where(TaskFile.task_id == uuid.UUID(tid))))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=Events(["说明\n---\n回复草稿" if template == "reply" else "摘要内容"]))

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    response = c.post(f"{BASE}/{tid}/messages", headers=h,
                      json={"template": template, "params": {"file_ids": [fid], **options}})
    assert response.status_code == 201, response.text
    run(db_sessionmaker, tid)
    messages = json.loads(requests[0].content)["messages"]
    assert messages[0]["content"] == system_prompt() + "\n\n" + instruction(template)
    if template == "reply":
        items = c.get(f"{BASE}/{tid}/messages").json()["items"]
        assert items[-1]["draft"] == "回复草稿"


def test_crosscheck_equal_amounts_keep_both_currencies_and_export_sources(conversation, db_sessionmaker):
    from tests.test_assistant_compare import compare_input
    c, h, tid, _ = conversation
    body = {**compare_input(db_sessionmaker, tid), "template": "crosscheck"}
    with db_sessionmaker() as db:
        first, second = db.scalars(select(DocNode).where(DocNode.task_id == uuid.UUID(tid)).order_by(DocNode.loc))
        first.text_enc = crypto.encrypt_text("总金额：USD 10,000")
        first.redacted_text = "总金额：USD [[AMT_1]]"
        second.text_enc = crypto.encrypt_text("Total Amount: USD 10,000")
        second.redacted_text = "Total Amount: USD [[AMT_1]]"
        db.add(DocNode(task_id=uuid.UUID(tid), file_id=first.file_id, loc="F1-P2", ord=2, kind="paragraph", part="body",
                       text_enc=crypto.encrypt_text("总金额：EUR 10,000"), redacted_text="总金额：EUR [[AMT_2]]"))
        db.commit()
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 201, response.text
    mid = response.json()["assistant"]["id"]
    run(db_sessionmaker, tid)
    table = next(m["crosscheck"] for m in c.get(f"{BASE}/{tid}/messages").json()["items"] if m["id"] == mid)
    amount, currency = (next(r for r in table["rows"] if r["field"] == field) for field in ("amount", "currency"))
    assert amount["status"] == "文件内有多个值" and currency["status"] == "不一致"
    usd, eur = currency["values"]["1"]
    assert usd["text"] == "USD" and eur["text"] == "EUR" and usd["loc"] != eur["loc"]
    for fmt, part in (("docx", "word/document.xml"), ("xlsx", "xl/worksheets/sheet1.xml")):
        exported = c.get(f"{BASE}/{tid}/messages/{mid}/export", params={"format": fmt})
        assert exported.status_code == 200
        with ZipFile(BytesIO(exported.content)) as archive:
            content = ''.join(ElementTree.fromstring(archive.read(part)).itertext())
            assert all(f"{item['text']}（{item['place']}）" in content for item in [usd, eur])


def test_crosscheck_local_table_and_model_only_locates(conversation, db_sessionmaker, monkeypatch):
    """V34-A-08：结果表由本机生成；模型只为本机没找到的项目给位置与原样文字，核对不上的丢弃。"""
    from tests.test_assistant_compare import compare_input
    from app.assistant.templates import instruction
    c, h, tid, _ = conversation
    body = compare_input(db_sessionmaker, tid)
    body["template"] = "crosscheck"
    with db_sessionmaker() as db:
        first, second = db.scalars(select(DocNode).where(DocNode.task_id == uuid.UUID(tid)).order_by(DocNode.loc))
        first.text_enc = crypto.encrypt_text("金额：USD 1000.00；毛重：12.5 MT；最迟装运期：2026-11-15")
        first.redacted_text = "金额：USD [[AMT_1]]；毛重：12.5 MT；最迟装运期：2026-12-13"
        second.text_enc = crypto.encrypt_text("Total Amount: USD 1,000.00; B/L Date: 2026-11-16")
        second.redacted_text = "Total Amount: USD [[AMT_2]]; B/L Date: 2026-12-14"
        db.add(DocNode(task_id=uuid.UUID(tid), file_id=second.file_id, loc="F2-P2", ord=2, kind="paragraph", part="body",
                       text_enc=crypto.encrypt_text("G.W 12500 KGS"), redacted_text="G.W 12500 KGS"))
        engine.write_full_map(db, uuid.UUID(tid), {"[[AMT_1]]": "1000.00", "[[AMT_2]]": "1,000.00",
                                                    "@date_shift_days": "28", "2026-12-13#D1": "2026-11-15",
                                                    "2026-12-14#D2": "2026-11-16"})
        db.commit()
    invalid = {"template": "crosscheck", "params": {"file_ids": body["params"]["file_ids"][:1]}}
    assert c.post(f"{BASE}/{tid}/messages", headers=h, json=invalid).status_code == 422
    requests = []
    located = {"items": [{"field": "毛重", "file": 2, "loc": "F2-P2", "text": "12500 KGS"},
                         {"field": "金额", "file": 2, "loc": "F2-P2", "text": "9999"}]}

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(located, ensure_ascii=False)}}],
                                        "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handle))
    response = c.post(f"{BASE}/{tid}/messages", headers=h, json=body)
    assert response.status_code == 201, response.text
    mid = response.json()["assistant"]["id"]
    run(db_sessionmaker, tid)
    messages = json.loads(requests[0].content)["messages"]
    assert messages[0]["content"].endswith(instruction("crosscheck"))
    assert messages[-1]["content"].endswith("【待定位项目】\n毛重：文件 2")
    sent = requests[0].content.decode()
    assert [value for value in ("1000.00", "2026-11-15", "2026-11-16") if value in sent] == []
    assert "2026-12-13" in sent and "2026-12-14" in sent
    table = next(m for m in c.get(f"{BASE}/{tid}/messages").json()["items"] if m["id"] == mid)["crosscheck"]
    rows = {row["label"]: row for row in table["rows"]}
    assert rows["金额"]["status"] == "一致" and rows["币种"]["status"] == "一致"
    assert rows["毛重"]["status"] == "一致" and rows["毛重"]["values"]["2"][0]["by"] == "model"
    assert len(table["findings"]) == 1 and table["findings"][0].startswith("提单日晚于最迟装运期")
    with db_sessionmaker() as db:
        reply = db.get(AssistantMessage, mid)
        assert reply.status == "done" and "1000" not in reply.content_redacted and "1000" not in json.dumps(reply.extras)
        assert "2026-11-16" not in reply.content_redacted and "提单日晚于最迟装运期" not in reply.content_redacted
    for fmt in ("docx", "xlsx"):
        exported = c.get(f"{BASE}/{tid}/messages/{mid}/export", params={"format": fmt})
        assert exported.status_code == 200
        with ZipFile(BytesIO(exported.content)) as archive:
            if fmt == "docx":
                assert "提单日晚于最迟装运期" in archive.read("word/document.xml").decode()
            else:
                assert "2026-11-16" in archive.read("xl/worksheets/sheet1.xml").decode()
