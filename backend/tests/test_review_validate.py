"""模型输出校验（M2-11，SPEC 11.4，T-10、T-11、A-07）。"""
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import func, select

from app.admin import settings_store
from app.config import get_settings
from app.db.models import DocNode, LlmCall, Task, TaskFile
from app.llm import gateway
from app.parsing.docx import parse_docx
from app.redaction import outbound
from app.redaction.engine import NodeIn, compute
from app.review.prompt import ReviewContext, build_messages, note_requirement
from app.review.requirements import Requirement, select_builtin
from app.review.validate import (
    MISSING_NOTE,
    DocNodeView,
    OutputError,
    map_category,
    map_sanctions,
    map_severity,
    quote_matches,
    review_once,
    validate_output,
)
from app.security import crypto
from tests.mock_llm import MODES, build_content, mock_transport

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"
MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))["samples"]


def sample(name: str):
    """(ReviewContext, nodes)：用真实的解析和脱敏结果构造。"""
    parsed = parse_docx((FIXTURES / MANIFEST[name]["file"]).read_bytes(), 1)
    body = [n for n in parsed.nodes if n.part == "body"]
    r = compute([NodeIn(n.loc, n.text, n.clause_no) for n in body], "sales")
    text = outbound.build(
        [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in r.parties],
        [(n.loc, n.clause_no, r.redacted[n.loc]) for n in body],
    )
    reqs = select_builtin("contract", "sales", "incoming") + [note_requirement("请重点看付款")]
    ctx = ReviewContext(task=Task(title=f"{name}.docx"), stance="sales", source="incoming", user_note="请重点看付款",
                        main_doc_type="contract", attachment_types=[], requirements=reqs, company_profile="",
                        outbound_text=text)
    nodes = {n.loc: DocNodeView(n.loc, r.redacted[n.loc], n.text, n.clause_no) for n in body}
    return ctx, nodes


def reply(ctx, mode="normal") -> str:
    return build_content(build_messages(ctx), mode)


# ---------------------------------------------------------------- T-10、T-11、A-07


@pytest.mark.parametrize("mode", [m for m in MODES if m not in ("non_json", "http_500", "timeout")])
def test_a07_one_result_per_sent_requirement(mode):
    ctx, nodes = sample("contract_basic")
    v = validate_output(reply(ctx, mode), ctx, nodes)
    assert [r.req_id for r in v.requirement_results] == [r.id for r in ctx.requirements]
    assert v.requirement_results[-1].req_source == "note" and v.requirement_results[0].req_text == ctx.requirements[0].text


def test_normal_reply_verified_with_snapshot():
    ctx, nodes = sample("contract_basic")
    v = validate_output(reply(ctx), ctx, nodes)
    (f,) = v.findings
    assert (f.locs, f.cite_status, f.category, f.severity) == (["F1-P1"], "verified", "commercial", "medium")
    assert f.quote_snapshot == nodes["F1-P1"].text and f.quote_model == "国际销售合同"
    assert v.requirement_results[0].verdict == "issue" and {r.verdict for r in v.requirement_results[1:]} == {"ok"}
    assert v.sanctions_level == "none" and v.doc_summary.startswith("模拟摘要")


def test_t10_invalid_loc_and_fabricated_quote():
    ctx, nodes = sample("contract_basic")
    bad_loc = validate_output(reply(ctx, "bad_loc"), ctx, nodes).findings[0]
    assert bad_loc.locs == [] and bad_loc.cite_status == "unverified" and bad_loc.quote_snapshot is None
    fake = validate_output(reply(ctx, "fake_quote"), ctx, nodes).findings[0]
    assert fake.locs == ["F1-P1"] and fake.cite_status == "unverified" and fake.quote_snapshot is None


def test_t11_missing_requirement_becomes_unable():
    ctx, nodes = sample("contract_basic")
    v = validate_output(reply(ctx, "missing_requirement"), ctx, nodes)
    last = v.requirement_results[-1]
    assert (last.req_id, last.verdict, last.note) == ("NOTE", "unable", MISSING_NOTE)


def test_fenced_and_non_json():
    ctx, nodes = sample("contract_basic")
    assert len(validate_output(reply(ctx, "fenced"), ctx, nodes).requirement_results) == len(ctx.requirements)
    with pytest.raises(OutputError, match="不是合法的 JSON"):
        validate_output(reply(ctx, "non_json"), ctx, nodes)
    with pytest.raises(OutputError, match="JSON 对象"):
        validate_output("[1, 2]", ctx, nodes)
    with pytest.raises(OutputError, match="requirement_results"):
        validate_output('{"findings": []}', ctx, nodes)
    with pytest.raises(OutputError, match=r"findings\.0\.problem"):
        validate_output('{"requirement_results": [], "findings": [{"title": "t"}]}', ctx, nodes)


# ---------------------------------------------------------------- 取值映射与整理


def make(results=(), findings=(), sanctions=None, **extra) -> str:
    data = {"doc_summary": "摘要", "requirement_results": list(results), "findings": list(findings), **extra}
    if sanctions is not None:
        data["sanctions"] = sanctions
    return json.dumps(data, ensure_ascii=False)


def finding(**kw) -> dict:
    data = {"title": "违约金比例偏高", "category": "法律", "severity": "高", "req_ids": ["C-06"],
            "locs": ["F1-P23"], "quote": "违约金总额最高不超过合同总价的 5%", "missing": False, "problem": "说明"}
    data.update(kw)
    return data


def test_value_mapping_and_fallbacks():
    assert [map_category(c) for c in ("表述", "逻辑", "法律", "合规与制裁", "商务", "legal", "出口管制", "法规",
                                      "前后矛盾", "措辞", "其他", "")] == [
        "wording", "logic", "legal", "compliance", "commercial", "legal", "compliance", "legal",
        "logic", "wording", "commercial", "commercial"]
    assert [map_severity(s) for s in ("高", "中", "低", "HIGH", "严重", "")] == ["high", "medium", "low", "high",
                                                                               "medium", "medium"]
    assert [map_sanctions(s) for s in ("无明显风险", "需注意", "高风险", "未知", "")] == ["none", "attention", "high",
                                                                                     None, None]


def test_results_filtering_priority_and_extra_fields():
    ctx, nodes = sample("contract_basic")
    first, second = ctx.requirements[0].id, ctx.requirements[1].id
    raw = make(
        results=[{"req_id": first, "verdict": "ok"}, {"req_id": first, "verdict": "issue", "note": "有问题"},
                 {"req_id": first, "verdict": "na"}, {"req_id": "X-99", "verdict": "issue"},
                 {"req_id": second, "verdict": "无法判断"}, {"req_id": "NOTE", "verdict": "maybe"}],
        findings=[finding(category="未知类", severity="特别高", req_ids="C-06", source_url="http://x", locs="F1-P23")],
        sanctions={"level": "需注意", "reasons": "需合规人员确认最新规定", "links": ["http://x"]},
        extra_field="丢弃",
    )
    v = validate_output(raw, ctx, nodes)
    by_id = {r.req_id: r for r in v.requirement_results}
    assert (by_id[first].verdict, by_id[first].note) == ("issue", "有问题")  # issue > unable > ok > na
    assert by_id[second].verdict == "unable"
    assert (by_id["NOTE"].verdict, by_id["NOTE"].note) == ("unable", MISSING_NOTE)  # 无效结论按缺失处理
    assert "X-99" not in by_id and len(v.requirement_results) == len(ctx.requirements)
    (f,) = v.findings
    assert (f.category, f.severity, f.req_ids, f.locs) == ("commercial", "medium", ["C-06"], ["F1-P23"])
    assert not hasattr(f, "source_url")
    assert (v.sanctions_level, v.sanctions_reasons) == ("attention", ["需合规人员确认最新规定"])


def test_quote_verification_rules():
    ctx, nodes = sample("contract_basic")
    text = "逾期交货每周按迟交部分货款的 0.5% 计违约金，违约金总额最高不超过合同总价的 5%。"
    assert quote_matches("违约金总额最高不超过合同总价的5%", text)  # 去空白
    assert quote_matches("违约金总额最高不超过合同总价的 5％", text)  # 全角
    assert quote_matches("逾期交货每周按迟交部分货款的 0.5% 计违约金，违约金总额最高不超过合同总价的 6%", text)  # 相似度 ≥ 0.9
    assert not quote_matches("逾期交货每日按全部货款的 1% 计违约金", text)
    assert not quote_matches("", text)
    v = validate_output(make(findings=[
        finding(quote="第2条 价格与付款", locs=["F1-P14"], title="a"),  # 手写编号在原文中
        finding(quote="定义与解释", locs=["F1-P23", "F9-X"], title="b"),
        finding(missing=True, locs=[], quote=None, title="缺少不可抗力条款"),
        finding(quote="2.2 预付款为合同总价的 30%，即 USD [金额_2]", locs=["F1-P16"], title="c"),
    ]), ctx, nodes)
    assert [f.cite_status for f in v.findings] == ["verified", "unverified", "missing", "verified"]
    assert v.findings[2].quote_snapshot is None and v.findings[1].locs == ["F1-P23"]
    # 快照是原文（真实金额），引用校验用的是脱敏文本
    assert "USD 120,000.00" in v.findings[3].quote_snapshot


def test_quote_across_two_locs_and_auto_numbering():
    ctx, nodes = sample("numbering_auto")
    v = validate_output(make(findings=[
        finding(quote="第1条 总则", locs=["F1-P2"], title="a"),  # 自动编号不在 text 中，按显示文字比较
        finding(quote="定义与解释本合同中的“货物”指合同附件所列产品", locs=["F1-P3", "F1-P4"], title="b"),
    ]), ctx, nodes)
    assert [f.cite_status for f in v.findings] == ["verified", "verified"]
    assert v.findings[0].quote_snapshot == "第1条 总则"


def test_duplicate_findings_merged():
    ctx, nodes = sample("contract_basic")
    v = validate_output(make(findings=[
        finding(title="违约金比例偏高", severity="中", req_ids=["C-06"], quote="编造"),
        finding(title="违约金比例过高", severity="高", req_ids=["G-01"], locs=["F1-P23", "F1-P16"]),
        finding(title="违约金比例偏高", category="商务"),  # 类别不同，不合并
        finding(title="争议解决条款不利", locs=["F1-P23"]),  # 标题不相似，不合并
        finding(title="缺少不可抗力条款", missing=True, locs=[], quote=None),
        finding(title="缺少不可抗力条款。", missing=True, locs=[], quote=None),
    ]), ctx, nodes)
    assert [f.title for f in v.findings] == ["违约金比例偏高", "违约金比例偏高", "争议解决条款不利", "缺少不可抗力条款"]
    merged = v.findings[0]
    assert merged.severity == "high" and merged.locs == ["F1-P23", "F1-P16"]
    assert merged.req_ids == ["C-06", "G-01"] and merged.cite_status == "verified"


def test_long_title_truncated():
    ctx, nodes = sample("contract_basic")
    v = validate_output(make(findings=[finding(title="长" * 300)]), ctx, nodes)
    assert len(v.findings[0].title) == 200


# ---------------------------------------------------------------- review_once（经网关，需要数据库）


@pytest.fixture
def configured(tmp_path, monkeypatch, db_sessionmaker, make_user):
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    monkeypatch.setattr(gateway.time, "sleep", lambda s: None)
    user = make_user("alice")
    ctx, nodes = sample("contract_basic")
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.base_url", "https://llm.example.test/v1", None)
        settings_store.set_setting(db, "model.name", "mock-model", None)
        settings_store.set_setting(db, "model.api_key_enc", crypto.encrypt_to_b64("sk-test-key-000000"), None)
        task = Task(owner_id=user.id, title="合同.docx", stance="sales", source="incoming", policy="ai",
                    status="reviewing")
        db.add(task)
        db.flush()
        f = TaskFile(task_id=task.id, seq=1, is_main=True, original_name="合同.docx", ext="docx", size_bytes=1,
                     sha256="0" * 64, storage_key=uuid.uuid4().hex)
        db.add(f)
        db.flush()
        for i, n in enumerate(nodes.values(), start=1):
            db.add(DocNode(task_id=task.id, file_id=f.id, loc=n.loc, ord=i, kind="paragraph", part="body",
                           clause_no=n.clause_no, text_enc=crypto.encrypt_text(n.text), redacted_text=n.redacted_text))
        db.commit()
        ctx.task = task
    yield user, ctx
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


def test_review_once_retries_once_on_bad_output(configured, db_sessionmaker, monkeypatch):
    user, ctx = configured
    requests = []
    monkeypatch.setattr(gateway, "TRANSPORT", mock_transport(["non_json", "normal"], requests))
    run = SimpleNamespace(id=uuid.uuid4())
    with db_sessionmaker() as db:
        v = review_once(db, ctx, run=run, user_id=user.id)
        rows = list(db.scalars(select(LlmCall).order_by(LlmCall.id)))
    assert len(v.requirement_results) == len(ctx.requirements) and v.findings[0].cite_status == "verified"
    assert v.findings[0].quote_snapshot == "国际销售合同"
    assert [r.status for r in rows] == ["sent", "sent"] and v.llm_call_ids == [r.id for r in rows]
    assert {r.run_id for r in rows} == {run.id} and v.tokens_in == sum(r.tokens_in for r in rows)
    second = json.loads(requests[1].content)["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert "不是合法的 JSON" in second[-1]["content"]


def test_review_once_fails_after_second_bad_output(configured, db_sessionmaker, monkeypatch):
    user, ctx = configured
    monkeypatch.setattr(gateway, "TRANSPORT", mock_transport("non_json"))
    with db_sessionmaker() as db:
        with pytest.raises(OutputError) as exc:
            review_once(db, ctx, run=SimpleNamespace(id=uuid.uuid4()), user_id=user.id)
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 2
    assert len(exc.value.llm_call_ids) == 2 and exc.value.tokens_in > 0
