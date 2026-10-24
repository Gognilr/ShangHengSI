"""M4 验收用例 T-06、T-08（SPEC 23.2）：经真实接口上传文件，模型用模拟服务。需要 TEST_DATABASE_URL。"""
import json
import re

import pytest
import yaml
from sqlalchemy import select

from app.db.models import LlmCall, RedactionItem, RedactionTerm
from app.main import API_PREFIX
from app.security.crypto import decrypt_text
from tests.acceptance_support import (
    FIXTURES,
    Sample,
    _docx_bytes,
    confirm_and_review,
    settle_suspects,
    upload_and_parse,
    url,
)
from tests.review_support import env, llm  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def alice(make_user, login):
    make_user("alice")
    client, csrf = login("alice")
    return client, {"X-CSRF-Token": csrf}


def outbound(c, task_id) -> str:
    r = c.get(url(task_id, "redaction/outbound"))
    assert r.status_code == 200, r.text
    return r.json()["text"]


def test_t06_contract_all_categories(db_sessionmaker, alice, llm):
    """合同含公司名、简称、人名、电话、邮箱、账号、SWIFT、地址、金额：外发文本中全部替换；
    【主体说明】含国家和类型；金额附比例。"""
    c, h = alice
    manifest = yaml.safe_load((FIXTURES / "docx" / "manifest.yaml").read_text(encoding="utf-8"))
    expected = manifest["samples"]["contract_basic"]["must_replace"]
    assert {"ORG", "PERSON", "PHONE", "EMAIL", "ACCOUNT", "SWIFT", "ADDR", "AMOUNT"} <= {i["category"] for i in expected}
    sample = Sample("销售合同.docx", (FIXTURES / "docx" / "contract_basic.docx").read_bytes())
    tid = upload_and_parse(c, h, db_sessionmaker, sample)

    text = outbound(c, tid)
    leaked = [i["text"] for i in expected if i["text"] in text]
    assert leaked == []
    summary = c.get(url(tid, "redaction")).json()["summary"]
    for cat in ("OUR", "ORG", "PERSON", "PHONE", "EMAIL", "ACCOUNT", "SWIFT", "ADDR", "AMOUNT", "PRICE", "IDNO", "REFNO"):
        assert summary.get(cat, 0) >= 1, cat
    # 【主体说明】：对方带角色、国家和企业类型
    first = text.splitlines()[0]
    assert first == "【主体说明】我方=卖方；客户_A：阿联酋，贸易商"
    # 地址代号带国家；人名、简称用代号
    assert "[地址：阿联酋]" in text and "人员_1" in text and "（以下简称“我方”）" in text
    # 金额附比例：预付款 = 合同总价的 30%；合同总价本身不加比例
    assert "USD [金额_2]（=合同总价的 30.0%）" in text
    assert re.search(r"Total Amount: USD \[金额_1\](?!（)", text)

    llm("normal")
    run = confirm_and_review(c, h, db_sessionmaker, tid)
    assert run["status"] == "completed"
    with db_sessionmaker() as db:
        sent = db.scalars(select(LlmCall)).one()
    assert sent.status == "sent" and first in sent.request_text and "（=合同总价的 30.0%）" in sent.request_text
    assert [i["text"] for i in expected if i["text"] in sent.request_text] == []


def test_t08_manual_org_auto_replaced_in_second_task(db_sessionmaker, alice, llm):
    """第一个任务中手动补充一个公司名，确认发送后第二个任务（另一个文件）自动识别并替换该名称。"""
    c, h = alice
    requests = llm("normal")
    name = "青松示例物流"
    first = Sample("销售确认书.docx", _docx_bytes([
        "销售确认书", "卖方：星河虚构进出口有限公司", "买方：Fictional Metals Trading FZE",
        f"货物由{name}负责运输，运费由买方承担。",
    ]))
    t1 = upload_and_parse(c, h, db_sessionmaker, first)
    assert name in outbound(c, t1)  # 自动识别不了
    fid = c.get(f"{API_PREFIX}/tasks/{t1}").json()["files"][0]["id"]
    node = next(n for n in c.get(url(t1, f"files/{fid}/nodes")).json()["items"] if name in (n["text"] or ""))
    start = node["text"].index(name)
    r = c.post(url(t1, "redaction/manual"), headers=h,
               json={"loc": node["loc"], "char_start": start, "char_end": start + len(name), "category": "ORG"})
    assert r.status_code == 200, r.text
    assert name not in outbound(c, t1)
    settle_suspects(c, h, t1, [])
    assert confirm_and_review(c, h, db_sessionmaker, t1)["status"] == "completed"
    with db_sessionmaker() as db:
        learned = {decrypt_text(t.term_enc) for t in db.scalars(select(RedactionTerm).where(RedactionTerm.kind == "learned"))}
    assert name in learned

    second = Sample("到访通知.docx", _docx_bytes([
        "到访通知", "卖方：星河虚构进出口有限公司", "买方：Fictional Metals Trading FZE",
        f"{name}将于下月到访贵司仓库，请{name}的人员提前登记。",
    ]))
    t2 = upload_and_parse(c, h, db_sessionmaker, second)
    text = outbound(c, t2)
    assert name not in text
    line = next(line for line in text.splitlines() if "将于下月到访" in line)
    code = re.match(r"\[F1-P\d+\] (\S+?)将于", line).group(1)
    assert line.count(code) == 2  # 同一名称同一代号
    with db_sessionmaker() as db:
        by = {i.detected_by for i in db.scalars(select(RedactionItem).where(RedactionItem.task_id == t2))
              if i.loc == line[1:].split("]")[0]}
    assert by == {"term"}
    settle_suspects(c, h, t2, [])
    assert confirm_and_review(c, h, db_sessionmaker, t2)["status"] == "completed"
    assert len(requests) == 2
    assert all(name not in m["content"] for r in requests for m in json.loads(r.content)["messages"])
