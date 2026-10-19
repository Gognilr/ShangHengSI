"""日志安全（SPEC 17.7，设计 docs/design/M4.md 第七节）：在 DEBUG 级别捕获解析、脱敏、第二步各接口、确认发送、
审核全过程（含出口复检拦截、模型出错、输出无法解析、解析失败、错误输入等分支）的日志，
断言不含样本中的原文片段、真实名称、金额和 API 密钥。

测试客户端（httpx，logger 名 httpx、目标 testserver）记录的请求 URL 属于测试进程而不是应用，不在检查范围内；
网关发往模型服务的请求日志在检查范围内。生产环境 uvicorn 以 --no-access-log 运行。需要 TEST_DATABASE_URL。
"""
import io
import logging
import uuid

import docx
import pytest
from sqlalchemy import select

from app.db.models import DocNode, RedactionTerm, Task
from app.main import API_PREFIX
from app.redaction.engine import load_real_values
from app.review import service
from app.security.crypto import decrypt_text
from tests.acceptance_support import (
    FIXTURES,
    Sample,
    confirm_and_review,
    golden_samples,
    settle_suspects,
    upload_and_parse,
    url,
)
from tests.review_support import env, llm  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")
API_KEY = "sk-test-0123456789"  # tests.review_support 写入设置的模型密钥


@pytest.fixture
def alice(make_user, login):
    make_user("alice")
    client, csrf = login("alice")
    return client, {"X-CSRF-Token": csrf}


def _app_log(caplog) -> str:
    return "\n".join(
        f"{r.name} {r.getMessage()} {r.exc_text or ''}" for r in caplog.records
        if not (r.name == "httpx" and "://testserver/" in r.getMessage())
    )


def _secrets(db_sessionmaker) -> set[str]:
    """全部任务的对照表真实值、节点原文（6 字以上）、已记住名称。"""
    out = set()
    with db_sessionmaker() as db:
        for tid in db.scalars(select(Task.id)):
            out.update(v for v in load_real_values(db, tid) if len(v.strip()) >= 2)
        for blob in db.scalars(select(DocNode.text_enc).where(DocNode.text_enc.is_not(None))):
            text = decrypt_text(blob).strip()
            if len(text) >= 6:
                out.add(text)
        out.update(decrypt_text(t.term_enc) for t in db.scalars(select(RedactionTerm)))
    return out


def test_logs_do_not_contain_text_names_amounts_or_key(db_sessionmaker, alice, llm, monkeypatch, caplog):
    c, h = alice
    caplog.set_level(logging.DEBUG)
    golden = {s.name: s for s in golden_samples()}

    # 1. 完整流程：上传 → 解析 → 第二步各接口（含疑似遗漏替换、手动补充、错误输入）→ 确认发送 → 审核 → 查看结果
    contract = golden["contract.docx"]
    requests = llm("normal")
    tid = upload_and_parse(c, h, db_sessionmaker, contract)
    detail = c.get(f"{API_PREFIX}/tasks/{tid}").json()
    fid = detail["files"][0]["id"]
    nodes = c.get(url(tid, f"files/{fid}/nodes")).json()["items"]
    assert c.get(url(tid, f"files/{fid}/raw")).status_code == 404  # DOCX 没有原件预览
    items = c.get(url(tid, "parties")).json()["items"]
    body = {"items": [{"id": p["id"], "is_our_party": p["is_our_party"], "country": p["country"],
                       "org_type": p["org_type"]} for p in items]}
    assert c.put(url(tid, "parties"), json=body, headers=h).status_code == 200
    bad = dict(body, items=[dict(i, is_our_party=True) for i in body["items"]])
    assert c.put(url(tid, "parties"), json=bad, headers=h).status_code == 422
    assert c.patch(url(tid, f"files/{fid}"), json={"doc_type": "contract"}, headers=h).status_code == 200
    target = next(n for n in nodes if "针状石油焦" in (n["text"] or ""))
    start = target["text"].index("针状石油焦")
    for sel in ({"char_start": start, "char_end": start + 1}, {"char_start": start, "char_end": 10 ** 6}):
        r = c.post(url(tid, "redaction/manual"), json={"loc": target["loc"], "category": "CUSTOM", **sel}, headers=h)
        assert r.status_code == 422
    r = c.post(url(tid, "redaction/manual"), headers=h,
               json={"loc": target["loc"], "char_start": start, "char_end": start + 5, "category": "CUSTOM"})
    assert r.status_code == 200, r.text
    assert c.post(url(tid, "runs"), headers=h).json()["code"] == "SUSPECTS_PENDING"
    settle_suspects(c, h, tid, contract.must_suspect)
    assert c.get(url(tid, "redaction/outbound")).status_code == 200
    run = confirm_and_review(c, h, db_sessionmaker, tid)
    assert run["status"] == "completed" and len(requests) == 1
    finding = run["findings"][0]
    r = c.patch(f"{API_PREFIX}/findings/{finding['id']}", json={"handling_status": "adopted", "handling_note": "付款按北辰公司要求"},
                headers=h)
    assert r.status_code == 200, r.text
    assert c.get(url(tid, "runs")).status_code == 200
    assert c.get(f"{API_PREFIX}/tasks", params={"q": "北辰"}).status_code == 200

    # 2. 出口复检：确认发送前的预检拦截，以及（预检被绕过时）网关拦截
    basic = Sample("销售合同 星河公司.docx", (FIXTURES / "docx" / "contract_basic.docx").read_bytes())
    tid2 = upload_and_parse(c, h, db_sessionmaker, basic)
    original = service.build_outbound

    def leaky(db, task):
        built = original(db, task)
        built.text += "\n[F1-P99] 星河虚构进出口有限公司 USD 400,000.00 13800138000"
        return built

    monkeypatch.setattr(service, "build_outbound", leaky)
    r = c.post(url(tid2, "runs"), headers=h)
    assert (r.status_code, r.json()["code"]) == (409, "OUTBOUND_BLOCKED")
    real_recheck = service.recheck
    monkeypatch.setattr(service, "recheck", lambda *a, **k: [])
    assert confirm_and_review(c, h, db_sessionmaker, tid2)["status"] == "blocked"
    monkeypatch.setattr(service, "recheck", real_recheck)
    monkeypatch.setattr(service, "build_outbound", original)

    # 3. 模型服务出错（HTTP 500，重试后失败）与输出无法解析
    llm(["http_500"])
    assert confirm_and_review(c, h, db_sessionmaker, tid2)["status"] == "failed"
    pdf = Sample("text_contract.pdf", (FIXTURES / "pdf" / "text_contract.pdf").read_bytes())
    tid3 = upload_and_parse(c, h, db_sessionmaker, pdf)
    fid3 = c.get(f"{API_PREFIX}/tasks/{tid3}").json()["files"][0]["id"]
    assert c.get(url(tid3, f"files/{fid3}/raw")).status_code == 200
    settle_suspects(c, h, tid3, [])
    llm("non_json")
    assert confirm_and_review(c, h, db_sessionmaker, tid3)["status"] == "failed"

    # 4. 解析失败（损坏的 DOCX，正文里有真实名称）
    d = docx.Document()
    d.add_paragraph("卖方：星河虚构进出口有限公司，合同总价 USD 400,000.00")
    buf = io.BytesIO()
    d.save(buf)
    broken = buf.getvalue()[:-200]
    r = c.post(f"{API_PREFIX}/tasks", headers=h, data={"stance": "sales", "source": "outgoing", "policy": "ai"},
               files=[("files", ("损坏 星河公司.docx", broken, "application/octet-stream"))])
    if r.status_code == 201:
        from app.tasks import pipeline
        with db_sessionmaker() as db:
            pipeline.parse_task(db, uuid.UUID(r.json()["id"]))
            assert db.get(Task, uuid.UUID(r.json()["id"])).status == "parse_failed"

    # 5. 删除任务（先收集要检查的原文）
    secrets = _secrets(db_sessionmaker) | set(contract.must_replace)
    assert c.delete(f"{API_PREFIX}/tasks/{tid3}", headers=h).status_code == 204

    log = _app_log(caplog)
    assert "llm_call" in log and "run " in log  # 确实捕获到了应用日志
    leaked = sorted(s for s in secrets if s in log)
    assert leaked == []
    for value in (API_KEY, "星河", "北辰", "Orion", "400,000", "625,000", "13800138000", "针状石油焦",
                  "付款按北辰公司要求", "Meridian"):
        assert value not in log, value
