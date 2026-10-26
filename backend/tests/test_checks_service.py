"""完整样本精确验收、汇总说明分支、事务和外发隐私。"""
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select

from app.checks.model import CheckFile, CheckNode
from app.checks.service import evaluate, load_inputs, local_summary, run_checks, table_notes
from app.db.models import DocNode, LlmCall, LocalFinding, RedactionMap, Task
from app.parsing import docx, pdf, xlsx
from app.redaction.engine import build_outbound, load_real_values
from app.redaction.recheck import recheck
from app.review import service
from app.review.validate import load_node_views
from app.security import crypto
from tests.acceptance_support import Sample, confirm_and_review, ensure_our_party, settle_suspects, upload_and_parse
from tests.review_support import env, llm, ready_task  # noqa: F401

ROOT = Path(__file__).parent / "fixtures" / "checks"
MANIFEST = yaml.safe_load((ROOT / "manifest.yaml").read_text(encoding="utf-8"))


def inputs(names, types):
    nodes, files = [], []
    for seq, (name, kind) in enumerate(zip(names, types), 1):
        ext = name.rsplit(".", 1)[-1]
        parsed = {"docx": docx.parse_docx, "pdf": pdf.parse_pdf, "xlsx": xlsx.parse_xlsx}[ext]((ROOT / name).read_bytes(), seq)
        nodes += [CheckNode(n.loc, n.text, seq, n.clause_no) for n in parsed.nodes if n.part == "body"]
        files.append(CheckFile(seq, seq == 1, ext, kind, parsed.warnings))
    return nodes, files


@pytest.mark.parametrize("entry", MANIFEST["samples"], ids=lambda x: x["file"])
def test_full_manifest_exact(entry):
    result = evaluate(*inputs([entry["file"]], [entry["doc_type"]]), date(2026, 10, 20))
    assert Counter((r.code, tuple(r.locs)) for r in result.results) == Counter(
        (r["code"], tuple(r["locs"])) for r in entry["expected"])


@pytest.mark.parametrize("entry", MANIFEST["consistency_cases"])
def test_cross_manifest_exact(entry):
    result = evaluate(*inputs(entry["files"], ["contract", "quotation"]), date(2026, 10, 20))
    assert Counter((r.code, tuple(r.locs)) for r in result.results) == Counter(
        (r["code"], tuple(r["locs"])) for r in entry["expected"])


@pytest.mark.parametrize("name,note", [
    ("row_error.xlsx", "金额计算有 1 行不一致"),
    ("total_error.xlsx", "合计不一致"),
    ("unreadable.xlsx", "1 行未核验"),
    ("formula_uncached.xlsx", "金额未核验"),
    ("valid_quote.docx", "金额计算一致，合计一致"),
])
def test_notes_real_fixtures(name, note):
    assert note in next(iter(table_notes(*inputs([name], ["quotation"])).values()))


def test_note_unrecognized_and_cross_branches():
    nodes = [CheckNode("F1-S1-A1", "单价", 1, None), CheckNode("F1-S1-A2", "100", 1, None)]
    assert table_notes(nodes, [CheckFile(1, True, "xlsx", "quotation", [])]) == {
        "F1-S1": "未识别报价表结构，未做金额核验"}
    for name, note in (("quote_cny.xlsx", "合计与合同总价不一致"), ("quote_usd.xlsx", "币种与合同总价不一致")):
        nodes, files = inputs(["contract_total.docx", name], ["contract", "quotation"])
        assert note in table_notes(nodes, files)["F2-S1"]
    nodes = [CheckNode("F1-P1", "合同总价：CNY 850,000.00", 1, None)] + [n for n in nodes if n.file_seq == 2]
    # USD 改为 CNY，使同币种且等额的成功分支确实参与跨文件比较。
    nodes = [CheckNode(n.loc, n.text.replace("USD", "CNY"), n.file_seq, n.clause_no) for n in nodes]
    assert "合计与合同总价一致" in table_notes(nodes, files)["F2-S1"]


def test_pipeline_local_only_encrypted_idempotent(db_sessionmaker, make_user, ready_task):
    owner = make_user("local-check")
    task = ready_task(owner, [("报价单.xlsx", (ROOT / "row_error.xlsx").read_bytes())], policy="local_only")
    assert task.status == "completed_local"
    with db_sessionmaker() as db:
        findings = list(db.scalars(select(LocalFinding).where(LocalFinding.task_id == task.id)))
        assert len(findings) == 1 and findings[0].check_code == "L-01"
        detail = crypto.decrypt_text(findings[0].detail_enc)
        assert "52,400.00" in detail and detail.encode() not in findings[0].detail_enc
        assert db.get(RedactionMap, task.id) is None
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 0
        run_checks(db, task, date(2026, 10, 20))
        db.commit()
        assert db.scalar(select(func.count()).select_from(LocalFinding)) == 1
        assert local_summary(db, task).startswith("[L-01]")


def test_real_api_gateway_summary_and_quote_citation(db_sessionmaker, make_user, login, llm):
    make_user("checks-ai")
    client, token = login("checks-ai")
    headers = {"X-CSRF-Token": token}
    calls = llm()
    tid = upload_and_parse(client, headers, db_sessionmaker, Sample("报价单.xlsx", (ROOT / "row_error.xlsx").read_bytes()))
    settle_suspects(client, headers, tid, [])
    ensure_our_party(client, headers, tid)
    settle_suspects(client, headers, tid, [])
    items = client.get(f"/api/v1/tasks/{tid}/local-findings").json()["items"]
    assert items[0]["check_code"] == "L-01" and "52,400.00" in items[0]["detail"]
    with db_sessionmaker() as db:
        task = db.get(Task, tid)
        built = build_outbound(db, task)
        views = load_node_views(db, tid, built.quote_tables)
        for table in built.quote_tables:
            assert views[table.loc].redacted_text in built.text
            assert "本地校验：" in views[table.loc].redacted_text
        summary = local_summary(db, task)
        assert not recheck(summary, real_values=load_real_values(db, tid), terms=[])
    detail = confirm_and_review(client, headers, db_sessionmaker, tid)
    assert detail["status"] == "completed" and calls
    with db_sessionmaker() as db:
        requests = list(db.scalars(select(LlmCall.request_text).where(LlmCall.task_id == tid)))
        assert requests and all("【本地校验摘要】" in text and "[L-01]" in text for text in requests)
        assert all("52,400" not in text and "52400" not in text for text in requests)


def test_t42_database_read_failure_keeps_other_checks(db_sessionmaker, make_user, ready_task, caplog, monkeypatch):
    from app.laws import index
    from sqlalchemy import text
    owner = make_user('law-failure')
    task = ready_task(owner, [("报价单.xlsx", (ROOT/'row_error.xlsx').read_bytes())], policy='local_only')
    with db_sessionmaker() as db:
        node = db.scalar(select(DocNode).where(DocNode.task_id == task.id).order_by(DocNode.ord))
        node.text_enc = crypto.encrypt_text(crypto.decrypt_text(node.text_enc)+'《示例秘密法》')
        db.commit()
        execute = db.execute
        def fail_law_query(statement, *a, **kw):
            if 'FROM laws' in str(statement):
                # 真正使 PostgreSQL 子事务失败，验证保存点回滚后仍可持久化其他校验结果。
                return execute(text("SELECT CAST('示例秘密法' AS integer)"))
            return execute(statement, *a, **kw)
        monkeypatch.setattr(db, 'execute', fail_law_query)
        caplog.clear()
        run_checks(db, task, date(2026, 10, 20))
        db.commit()
        codes = [r.check_code for r in db.scalars(select(LocalFinding).where(LocalFinding.task_id == task.id))]
    assert 'L-01' in codes and 'L-10' not in codes
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1 and '示例秘密法' not in caplog.text and 'secret' not in caplog.text


def test_t42_pipeline_passes_actual_law_index(db_sessionmaker, make_user, ready_task):
    from app.db.models import Law
    from docx import Document
    import io
    with db_sessionmaker() as db:
        db.add(Law(title='中华人民共和国示例供货合同法', category='法律', source='import',
                   status='repealed', published_on=date(2020, 1, 1)))
        db.commit()
    document = Document()
    document.add_paragraph('依据《示例供货合同法》订立合同。')
    buf = io.BytesIO()
    document.save(buf)
    task = ready_task(make_user('law-pipeline'), [('示例.docx', buf.getvalue())], policy='local_only')
    with db_sessionmaker() as db:
        rows = list(db.scalars(select(LocalFinding).where(LocalFinding.task_id == task.id, LocalFinding.check_code == 'L-10')))
        assert len(rows) == 1
        assert crypto.decrypt_text(rows[0].detail_enc) == '引用《示例供货合同法》，该法已废止'
        assert rows[0].ai_summary == '引用《示例供货合同法》，该法已废止'
