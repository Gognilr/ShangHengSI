"""修订版的虚构任务与双视图校验。"""
import io
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from docx.oxml.ns import qn
from sqlalchemy import delete, select

from app.db.models import AuditLog, DocNode, Finding, TaskFile
from app.export import revised_docx
from app.parsing.docx import parse_docx
from app.security import crypto
from app.tasks import storage
from tests.test_annotated_export import case as base_case  # noqa: F401
from tests.review_support import env  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def case(base_case, db_sessionmaker):
    user, tid, rid, fid, _ = base_case
    data = (Path(__file__).parent / "fixtures/annotate/revise.docx").read_bytes()
    with db_sessionmaker() as db:
        file = db.get(TaskFile, fid)
        storage.delete_original(file.storage_key)
        file.storage_key, file.sha256 = storage.save_original(data)
        file.size_bytes = len(data)
        db.execute(delete(DocNode).where(DocNode.file_id == fid))
        for n in parse_docx(data, 1).nodes:
            db.add(DocNode(task_id=tid, file_id=fid, loc=n.loc, ord=n.ord, kind=n.kind, part=n.part,
                           text_enc=crypto.encrypt_text(n.text)))
        db.commit()
    return user, tid, rid, fid, data


def prepare(sm, rid):
    with sm() as db:
        rows = list(db.scalars(select(Finding).where(Finding.run_id == rid).order_by(Finding.seq)))
        rows[0].handling_status = "adopted"
        rows[0].suggested_text = "[客户_A]应在十日内交货"
        rows[1].handling_status = "adopted"
        rows[1].missing = True
        rows[1].suggested_text = "双方协商解决争议。"
        for seq, quote, suggested, status in [
            (4, "找不到", "补充条款", "adopted"),
            (5, "未约定", "[客户_C]承担责任", "adopted"),
            (6, "未约定", "不应写入", "pending"),
        ]:
            db.add(Finding(task_id=rows[0].task_id, run_id=rid, seq=seq, title="虚构问题", category="legal",
                           severity="high", locs=["F1-P2"], cite_status="unverified", quote_model=quote,
                           problem="虚构问题说明", suggested_text=suggested, handling_status=status))
        db.commit()


def test_shared_quote_and_local_phone_revision(case, login, db_sessionmaker):
    import json
    from app.db.models import RedactionItem, ReviewRun, Task
    from app.redaction import engine
    from app.review.prompt import ReviewContext
    from app.review.validate import load_node_views, validate_output
    from app.review.job import _save
    user, tid, rid, fid, _ = case
    lines = ['联系电话：13812345678。', '备用联系电话：13912345678，另一电话：13712345678。']
    source = Document()
    for line in lines:
        source.add_paragraph(line)
    buffer = io.BytesIO()
    source.save(buffer)
    original = buffer.getvalue()
    nodes = [engine.NodeIn(f'F1-P{i}', text) for i, text in enumerate(lines, 1)]
    result = engine.compute(nodes, 'sales', ratio=False)
    with db_sessionmaker() as db:
        file = db.get(TaskFile, fid)
        storage.delete_original(file.storage_key)
        file.storage_key, file.sha256 = storage.save_original(original)
        file.size_bytes = len(original)
        db.execute(delete(DocNode).where(DocNode.file_id == fid))
        db.execute(delete(Finding).where(Finding.run_id == rid))
        for i, n in enumerate(nodes, 1):
            db.add(DocNode(task_id=tid, file_id=fid, loc=n.loc, ord=i, kind='paragraph', part='body',
                           text_enc=crypto.encrypt_text(n.text), redacted_text=result.redacted[n.loc]))
        for item in result.items:
            db.add(RedactionItem(task_id=tid, **vars(item)))
        engine.write_full_map(db, tid, result.mapping)
        db.flush()
        assert engine.load_local_values(db, tid, 'F1-P1') == {'[电话]': '13812345678'}
        assert engine.load_local_values(db, tid, 'F1-P2') == {}
        task = db.get(Task, tid)
        ctx = ReviewContext(task, 'sales', 'outgoing', None, 'contract', [], [], '', '')
        raw = json.dumps({'doc_summary': '虚构测试', 'requirement_results': [], 'findings': [
            dict(title='电话联系范围', category='商务', severity='低', req_ids=[], locs=['F1-P1'],
                 missing=False, quote='联系电话：[电话]', problem='请限定用途',
                 suggested_text='联系电话：[电话]，仅供履约联系'),
            dict(title='双电话不可猜测', category='商务', severity='低', req_ids=[], locs=['F1-P2'],
                 missing=False, quote='备用联系电话：[电话]', problem='请明确', suggested_text='请联系[电话]'),
            dict(title='字面金额不写入', category='商务', severity='低', req_ids=[], locs=['F1-P2'],
                 missing=True, quote=None, problem='请明确', suggested_text='应支付人民币 1000 元。'),
        ]}, ensure_ascii=False)
        review = validate_output(raw, ctx, load_node_views(db, tid))
        assert review.findings[0].quote_original == '联系电话：13812345678'
        db.commit()
        _save(db, rid, ctx, review)
        for row in db.scalars(select(Finding).where(Finding.run_id == rid)):
            row.handling_status = 'adopted'
        db.commit()
        first = db.scalar(select(Finding).where(Finding.run_id == rid, Finding.seq == 1))
        assert crypto.decrypt_text(first.quote_orig_enc) == '联系电话：13812345678'
        assert '13812345678' not in first.quote_model
    client, _ = login(user.username)
    detail = client.get(f'/api/v1/runs/{rid}').json()
    assert detail['findings'][0]['quote_excerpt'] == '联系电话：13812345678'
    assert detail['findings'][0]['suggested_text'] == '联系电话：13812345678，仅供履约联系'
    response = client.get(f'/api/v1/runs/{rid}/revised?file_seq=1')
    assert response.status_code == 200, response.text
    assert response.headers['X-SI-Revised'] == '1' and response.headers['X-SI-Commented'] == '2'
    revised_docx.validate(original, response.content, ['联系电话：13812345678，仅供履约联系。', lines[1]])


def test_unknown_shifted_date_becomes_comment(case, login, db_sessionmaker):
    from app.redaction.engine import _load_full_map, write_full_map
    user, tid, rid, _, _ = case
    with db_sessionmaker() as db:
        mapping = _load_full_map(db, tid)
        mapping.update({'@date_shift_days': '70', '2026年5月14日#D1': '2026年3月5日'})
        write_full_map(db, tid, mapping)
        row = db.scalar(select(Finding).where(Finding.run_id == rid, Finding.seq == 1))
        row.handling_status = 'adopted'
        row.suggested_text = '[客户_A]应于2026年6月15日交付。'
        valid = db.scalar(select(Finding).where(Finding.run_id == rid, Finding.seq == 2))
        valid.handling_status, valid.missing = 'adopted', True
        valid.suggested_text = '双方应友好协商解决争议。'
        db.commit()
    client, _ = login(user.username)
    response = client.get(f'/api/v1/runs/{rid}/revised?file_seq=1')
    assert response.status_code == 200, response.text
    assert response.headers['X-SI-Revised'] == '1' and response.headers['X-SI-Commented'] == '1'
    document = Document(io.BytesIO(response.content))
    assert '2026年6月15日' in '\n'.join(comment.text for comment in document.comments)
    assert '2026年6月15日' not in ''.join(p.text for p in document.paragraphs)


def test_revision_integrity_gate_and_ratio_removal(case, login, db_sessionmaker):
    user, _, rid, _, original = case
    prepare(db_sessionmaker, rid)
    client, _ = login(user.username)
    path = f'/api/v1/runs/{rid}/revised?file_seq=1'
    with db_sessionmaker() as db:
        row = db.scalar(select(Finding).where(Finding.run_id == rid, Finding.seq == 1))
        row.suggested_text = '客户A应付款'
        db.commit()
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers['X-SI-Revised'] == '1' and response.headers['X-SI-Commented'] == '3'
    assert any('未能还原' in c.text and '客户A' in c.text for c in Document(io.BytesIO(response.content)).comments)
    with db_sessionmaker() as db:
        row = db.scalar(select(Finding).where(Finding.run_id == rid, Finding.seq == 1))
        row.suggested_text = '[客户_A]应付款（=合同总价的 30.0%）'
        db.commit()
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers['X-SI-Revised'] == '2'
    revised_docx.validate(original, response.content, ['星河虚构公司应付款。', '未约定争议解决方式。', '双方协商解决争议。'])


def test_revisions_comments_views_and_audit(case, login, db_sessionmaker):
    user, _, rid, fid, original = case
    prepare(db_sessionmaker, rid)
    client, _ = login(user.username)
    response = client.get(f"/api/v1/runs/{rid}/revised?file_seq=1")
    assert response.status_code == 200, response.text
    assert response.headers["X-SI-Revised"] == "2"
    assert response.headers["X-SI-Commented"] == "2"
    expected = ["星河虚构公司应在十日内交货。", "未约定争议解决方式。", "双方协商解决争议。"]
    revised_docx.validate(original, response.content, expected)
    doc = Document(io.BytesIO(response.content))
    assert len(doc.comments) == 2
    assert all(c.text.startswith("【未能写成修订，请人工修改】") for c in doc.comments)
    changes = list(doc.element.iter(qn("w:ins"))) + list(doc.element.iter(qn("w:del")))
    assert changes and all(e.get(qn("w:author")) == "商衡SI" for e in changes)
    assert all(e.get(qn("w:date")).endswith("Z") for e in changes)
    inserted_p = [p for p in doc.element.iter(qn("w:p")) if revised_docx._inserted_paragraph(p)]
    assert len(inserted_p) == 1
    assert inserted_p[0].find(qn("w:pPr")).find(qn("w:pStyle")).get(qn("w:val")) == "ListNumber"
    ids = [e.get(qn("w:id")) for e in changes] + [str(c.comment_id) for c in doc.comments]
    assert len(ids) == len(set(ids))
    assert "星河虚构公司应在十日内交货" in revised_docx._views(response.content)[1][0]
    with db_sessionmaker() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.action == "export_revised"))
        assert row.detail == dict(run_no=1, file_seq=1, revised=2, commented=2)
    # 正文被改动时，即使包仍可打开也不得输出。
    output = io.BytesIO()
    with ZipFile(io.BytesIO(response.content)) as source, ZipFile(output, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "word/document.xml":
                data = data.replace("未约定".encode(), "已约定".encode())
            target.writestr(name, data)
    with pytest.raises(revised_docx.RevisionValidationError):
        revised_docx.validate(original, output.getvalue(), expected)


def test_overlap_and_refusals(case, login, make_user, db_sessionmaker):
    user, _, rid, fid, _ = case
    prepare(db_sessionmaker, rid)
    client, _ = login(user.username)
    path = f"/api/v1/runs/{rid}/revised?file_seq=1"
    make_user("revised-other", role="admin")
    other, _ = login("revised-other")
    assert other.get(path).status_code == 404
    with db_sessionmaker() as db:
        second = db.scalar(select(Finding).where(Finding.run_id == rid, Finding.seq == 2))
        second.missing, second.locs, second.quote_model = False, ["F1-P1"], "按期交货"
        db.commit()
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["X-SI-Revised"] == "1"
    assert response.headers["X-SI-Commented"] == "3"
    with db_sessionmaker() as db:
        db.get(TaskFile, fid).has_revisions = True
        db.commit()
    assert client.get(path).json()["message"] == "原件含修订记录，请在 Word 中接受或拒绝全部修订后重新上传"
    with db_sessionmaker() as db:
        db.get(TaskFile, fid).has_revisions = False
        for f in db.scalars(select(Finding).where(Finding.run_id == rid)):
            f.handling_status = "pending"
        db.commit()
    assert client.get(path).json()["message"] == "本轮没有已采纳且有建议条文的问题"


def test_ordered_changes_ignore_inserted_quote(case, login, db_sessionmaker):
    user, _, rid, _, original = case
    prepare(db_sessionmaker, rid)
    with db_sessionmaker() as db:
        rows = {f.seq: f for f in db.scalars(select(Finding).where(Finding.run_id == rid))}
        rows[1].quote_model = "[客户_A]"
        rows[1].suggested_text = "[客户_A]按期交货"
        rows[4].locs, rows[4].quote_model, rows[4].suggested_text = ["F1-P1"], "按期交货", "及时交货"
        rows[5].missing, rows[5].suggested_text = True, "新增第一行。\n新增第二行。"
        db.commit()
    client, _ = login(user.username)
    response = client.get(f"/api/v1/runs/{rid}/revised?file_seq=1")
    assert response.status_code == 200, response.text
    assert response.headers["X-SI-Revised"] == "4"
    assert response.headers["X-SI-Commented"] == "0"
    revised_docx.validate(original, response.content, [
        "星河虚构公司按期交货应及时交货。", "未约定争议解决方式。", "双方协商解决争议。", "新增第一行。", "新增第二行。",
    ])
    with db_sessionmaker() as db:
        for f in db.scalars(select(Finding).where(Finding.run_id == rid)):
            f.missing, f.quote_model, f.suggested_text = False, "不存在的引用", "无法定位的建议"
        db.commit()
    assert client.get(f"/api/v1/runs/{rid}/revised?file_seq=1").json()["message"] == "已采纳的问题都无法写成修订，请改用批注版"
