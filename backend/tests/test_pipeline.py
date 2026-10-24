"""parse 处理函数与脱敏的数据库部分（M2-08）。需要 TEST_DATABASE_URL。"""
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select

from app import worker
from app.config import get_settings
from app.admin.settings_store import set_setting
from app.db.models import DocNode, Party, RedactionItem, RedactionMap, RedactionSuspect, RedactionTerm, Task, TaskFile
from app.parsing import ocr
from app.parsing.ocr import OcrLine
from app.redaction import engine
from app.redaction.engine import PartyChange
from app.security import crypto
from app.tasks import pipeline, storage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"
ROOT = FIXTURES.parent


@pytest.fixture
def files_dir(tmp_path, monkeypatch):
    """隔离的主密钥和原件目录。"""
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    monkeypatch.setenv("FILES_DIR", str(tmp_path / "files"))
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    yield
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


@pytest.fixture
def make_task(db_sessionmaker, make_user, files_dir):
    owner = make_user("alice")

    def _make(files: list[tuple[str, bytes]], *, policy: str = "ai", stance: str = "sales") -> Task:
        with db_sessionmaker() as db:
            task = Task(owner_id=owner.id, title=files[0][0], stance=stance, source="outgoing", policy=policy,
                        status="parsing")
            db.add(task)
            db.flush()
            for seq, (name, data) in enumerate(files, start=1):
                key, sha = storage.save_original(data)
                ext = name.rsplit(".", 1)[-1]
                db.add(TaskFile(task_id=task.id, seq=seq, is_main=seq == 1, original_name=name, ext=ext,
                                size_bytes=len(data), sha256=sha, storage_key=key))
            db.commit()
            return task

    return _make


def sample(name: str) -> bytes:
    return (FIXTURES / f"{name}.docx").read_bytes()


def fixture(path: str) -> bytes:
    return (ROOT / path).read_bytes()


def count(db, model, task_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.task_id == task_id))


def test_parse_ai_task(db_sessionmaker, make_task):
    task = make_task([("销售合同.docx", sample("contract_basic")), ("报价单.docx", sample("quotation_table"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert (t.status, t.error_message) == ("awaiting_confirm", None)
        f1, f2 = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)).all()
        assert (f1.doc_type, f1.doc_type_source, f1.parser_version, f1.has_revisions) == ("contract", "auto", "docx-1", False)
        assert f2.doc_type == "quotation"
        nodes = db.scalars(select(DocNode).where(DocNode.task_id == task.id)).all()
        assert {n.loc[:3] for n in nodes} == {"F1-", "F2-"}
        p2 = next(n for n in nodes if n.loc == "F1-P2")
        assert crypto.decrypt_text(p2.text_enc) == "合同编号：HT-2026-0088"
        assert p2.redacted_text == "合同编号：编号_1"
        # 两个文件中的同一公司同一代号
        assert next(n for n in nodes if n.loc == "F2-P2").redacted_text == "Seller: 我方"
        assert next(n for n in nodes if n.loc == "F2-P3").redacted_text == "Buyer: 客户_A"
        parties = db.scalars(select(Party).where(Party.task_id == task.id)).all()
        assert {(p.code, p.role_label, p.is_our_party, p.confirmed) for p in parties} == {
            ("我方", "卖方", True, False), ("客户_A", "买方", False, False)}
        assert count(db, RedactionItem, task.id) > 20
        # 对照表加密存储，密文中没有明文
        row = db.get(RedactionMap, task.id)
        assert "星河".encode() not in row.map_enc and b"FICTAEAD" not in row.map_enc
        mapping = engine.load_redaction_map(db, task.id)
        assert mapping["客户_A"] == "Fictional Metals Trading FZE" and "[电话]" not in mapping
        assert all("#" not in k for k in mapping)
        assert {"FMT", "13800138000"} <= set(engine.load_real_values(db, task.id))

        out = engine.build_outbound_text(db, t)
        assert out.splitlines()[0] == "【主体说明】我方=卖方；客户_A：买方，阿联酋，贸易商"
        assert "[F1-P2] 合同编号：编号_1" in out and "[F2-T1-R2-C4] USD [单价_" in out
        for value in ("星河", "Fictional Metals", "FMT", "13800138000", "FICTAEAD", "400,000.00", "HT-2026-0088"):
            assert value not in out
        summary = engine.redaction_summary(db, t)
        assert summary["OUR"] >= 3 and summary["PHONE"] == 3 and summary["SWIFT"] == 1


def test_parse_local_only_has_no_redaction_data(db_sessionmaker, make_task):
    task = make_task([("合同.docx", sample("contract_basic"))], policy="local_only")
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "completed_local"
        assert count(db, DocNode, task.id) == 26
        assert db.scalar(select(func.count()).select_from(DocNode).where(
            DocNode.task_id == task.id, DocNode.redacted_text.is_not(None))) == 0
        for model in (Party, RedactionItem, RedactionMap):
            assert count(db, model, task.id) == 0


def test_parse_failures(db_sessionmaker, make_task):
    secret = "机密原文星河虚构123"
    pdf = make_task([("合同.docx", sample("contract_basic")), ("附件.pdf", b"%PDF-1.7\n" + secret.encode())])
    broken = make_task([("坏文件.docx", b"PK\x03\x04" + secret.encode() * 20)])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, pdf.id)
        pipeline.parse_task(db, broken.id)
    with db_sessionmaker() as db:
        t = db.get(Task, pdf.id)
        assert (t.status, t.error_message) == ("parse_failed", "附件.pdf：PDF 文件无法读取，可能已损坏或已加密")
        assert count(db, DocNode, pdf.id) == 0 and count(db, Party, pdf.id) == 0  # 不留一半的数据
        t = db.get(Task, broken.id)
        assert t.status == "parse_failed" and t.error_message.startswith("坏文件.docx：")
        assert secret not in t.error_message


def test_parse_internal_error_marks_failed_and_reraises(db_sessionmaker, make_task, monkeypatch):
    task = make_task([("合同.docx", sample("contract_basic"))])
    monkeypatch.setattr(pipeline, "redact_task", lambda db, t: (_ for _ in ()).throw(RuntimeError("内部")))
    with db_sessionmaker() as db, pytest.raises(RuntimeError):
        pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert (t.status, t.error_message) == ("parse_failed", pipeline.MSG_INTERNAL)
        assert count(db, DocNode, task.id) == 0


def test_parse_skips_deleted_or_already_parsed(db_sessionmaker, make_task):
    task = make_task([("合同.docx", sample("contract_basic"))])
    with db_sessionmaker() as db:
        db.get(Task, task.id).status = "awaiting_confirm"
        db.commit()
        pipeline.parse_task(db, task.id)
        assert count(db, DocNode, task.id) == 0
        db.delete(db.get(Task, task.id))
        db.commit()
        pipeline.parse_task(db, task.id)  # 已删除：直接结束，不报错


def test_parse_handler_registered(db_sessionmaker, make_task):
    assert worker.HANDLERS["parse"] is pipeline.handle_parse
    task = make_task([("合同.docx", sample("revisions"))])
    with db_sessionmaker() as db:
        pipeline.handle_parse(db, {"task_id": str(task.id)})
    with db_sessionmaker() as db:
        f = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
        assert f.has_revisions and f.warnings == ["检测到修订痕迹，按接受修订后的文本审核"]


def test_apply_party_changes(db_sessionmaker, make_task):
    task = make_task([("合同.docx", sample("parties_labels"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        before = {p.role_label: p for p in db.scalars(select(Party).where(Party.task_id == task.id))}
        ids = {k: p.id for k, p in before.items()}
        assert before["卖方"].code == "我方"
        engine.apply_party_changes(db, t, [
            PartyChange(ids["买方"], is_our_party=True),
            PartyChange(ids["乙方"], country="德国", org_type="贸易商"),
        ])
        db.commit()
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        after = {p.role_label: p for p in db.scalars(select(Party).where(Party.task_id == task.id))}
        assert {k: p.id for k, p in after.items()} == ids  # 行不变，只改代号和属性
        assert after["买方"].code == "我方" and not after["卖方"].is_our_party
        assert after["卖方"].code == "卖方_A"  # 字母按首次出现重新分配
        assert (after["乙方"].country, after["乙方"].org_type) == ("德国", "贸易商")
        assert all(p.confirmed for p in after.values())
        out = engine.build_outbound_text(db, t)
        assert "[F1-P3] Buyer: 我方 (hereinafter referred to as \"我方\")" in out
        assert "[F1-P2] Seller: 卖方_A (the \"Seller\")" in out
        mapping = engine.load_redaction_map(db, task.id)
        assert mapping["我方"] == "Example Industrial Trading LLC"
        items = db.scalars(select(RedactionItem).where(RedactionItem.task_id == task.id, RedactionItem.loc == "F1-P3"))
        assert {(i.category, i.placeholder) for i in items} == {("OUR", "我方")}
        # 恰好一方为我方；不存在的签约方报错
        with pytest.raises(ValueError):
            engine.apply_party_changes(db, t, [PartyChange(ids["买方"], is_our_party=False)])
        db.rollback()
        with pytest.raises(ValueError):
            engine.apply_party_changes(db, t, [PartyChange(ids["买方"].__class__(int=1), country="X")])


def test_custom_terms_from_table(db_sessionmaker, make_task):
    with db_sessionmaker() as db:
        db.add(RedactionTerm(kind="custom", term_enc=crypto.encrypt_text("煅后石油焦"), term_hash="0" * 64,
                             category="CUSTOM"))
        db.add(RedactionTerm(kind="custom", term_enc=crypto.encrypt_text("针状焦"), term_hash="1" * 64,
                             category="CUSTOM", enabled=False))
        db.commit()
    task = make_task([("报价单.docx", sample("quotation_table"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
        out = engine.build_outbound_text(db, db.get(Task, task.id))
    assert "煅后石油焦" not in out and "[敏感词_1]" in out
    assert "针状焦" in out  # 已停用的词条不生效
    with db_sessionmaker() as db:
        assert json.loads(crypto.decrypt_text(db.get(RedactionMap, task.id).map_enc))["[敏感词_1]"] == "煅后石油焦"


# ---------------------------------------------------------------- M3：五种格式、OCR 预算与 ocr_slot


XLSX_MANIFEST = yaml.safe_load((ROOT / "xlsx" / "manifest.yaml").read_text(encoding="utf-8"))["samples"]
PDF_MANIFEST = yaml.safe_load((ROOT / "pdf" / "manifest.yaml").read_text(encoding="utf-8"))["samples"]


def test_parse_all_five_formats(db_sessionmaker, make_task):
    """五种格式在同一任务中解析、识别类型并脱敏，走到 awaiting_confirm（真实 OCR）。"""
    task = make_task([
        ("销售合同.docx", sample("contract_basic")),
        ("报价单.xlsx", fixture("xlsx/quotation.xlsx")),
        ("合同.pdf", fixture("pdf/text_contract.pdf")),
        ("报价函.png", fixture("images/quotation_clear.png")),
        ("扫描件.jpg", fixture("images/quotation_lowq.jpg")),
    ])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert (t.status, t.error_message) == ("awaiting_confirm", None)
        files = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)).all()
        assert [f.parser_version for f in files] == ["docx-1", "xlsx-1", "pdf-1", "image-1", "image-1"]
        assert [f.page_count for f in files][1:] == [None, 2, 1, 1]
        assert files[1].doc_type == "quotation" and files[2].doc_type == "contract"
        assert all(f.doc_type_source == "auto" for f in files)
        assert "图片 OCR 识别置信度低，请核对原件" in files[4].warnings
        nodes = db.scalars(select(DocNode).where(DocNode.task_id == task.id)).all()
        kinds = {(n.loc.split("-")[0], n.kind) for n in nodes}
        assert {("F2", "sheet_cell"), ("F3", "paragraph"), ("F3", "table_cell"), ("F4", "ocr_block"),
                ("F5", "ocr_block")} <= kinds
        ocr_node = next(n for n in nodes if n.loc.startswith("F4-"))
        assert ocr_node.page == 1 and len(ocr_node.bbox) == 4 and ocr_node.ocr_conf is not None
        cell = next(n for n in nodes if n.loc == "F2-S1-E8")
        assert (cell.sheet, cell.cell_ref) == ("报价", "E8") and cell.redacted_text.startswith("USD [单价_")
        assert all(n.redacted_text is not None for n in nodes if n.part == "body")

        # XLSX 报价单：表头在第 7 行，单价和金额全部替换，数量、百分比、日期保留
        out = engine.build_outbound_text(db, t)
        for item in XLSX_MANIFEST["quotation"]["must_replace"] + PDF_MANIFEST["text_contract"]["must_replace"]:
            if item["category"] in ("PERSON", "ADDR"):  # 人名、地址在 M4
                continue
            value = item["text"].split(" ", 1)[-1] if item["category"] in ("PRICE", "AMOUNT") else item["text"]
            assert value not in out, item["text"]
        red = {n.loc: n.redacted_text for n in nodes if n.loc.startswith("F2-")}
        assert red["F2-S1-E9"].startswith("USD [单价_") and red["F2-S1-F8"].startswith("USD [金额_")
        assert red["F2-S1-F10"].startswith("USD [金额_") and red["F2-S1-F11"].startswith("¥ [金额_")
        assert (red["F2-S1-D8"], red["F2-S1-G8"], red["F2-S1-B5"]) == ("320", "30%", "2026-03-10")


def test_parse_scanned_pdf_local_only(db_sessionmaker, make_task):
    """扫描版 PDF（真实 OCR），不外发：直接 completed_local，低置信页写入警告和 ocr_conf。"""
    task = make_task([("扫描合同.pdf", fixture("pdf/scanned_contract.pdf"))], policy="local_only")
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        assert db.get(Task, task.id).status == "completed_local"
        f = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
        assert (f.parser_version, f.page_count) == ("pdf-1", 2)
        assert f.warnings[0] == "第 1、2 页为扫描页，已用 OCR 识别"
        assert any("OCR 识别置信度低" in w for w in f.warnings)
        nodes = db.scalars(select(DocNode).where(DocNode.task_id == task.id)).all()
        assert nodes and all(n.kind == "ocr_block" and n.ocr_conf is not None for n in nodes)
        assert count(db, RedactionItem, task.id) == 0


@pytest.fixture
def fake_ocr(monkeypatch):
    """替换 OCR 引擎：记录调用次数，并检查调用时持有 ocr_slot。"""
    held = threading.local()
    calls = {"recognize": 0, "slot": 0}

    @contextmanager
    def slot():
        calls["slot"] += 1
        held.on = True
        try:
            yield
        finally:
            held.on = False

    def recognize(image):
        assert getattr(held, "on", False), "OCR 必须在 ocr_slot 内执行"
        calls["recognize"] += 1
        return [OcrLine(text="报价函：示例产品单价 USD 100/MT", bbox=[10.0, 10.0, 300.0, 30.0], conf=0.95)]

    monkeypatch.setattr(worker, "ocr_slot", slot)
    monkeypatch.setattr(ocr, "recognize", recognize)
    return calls


def test_ocr_slot_only_for_files_needing_ocr(db_sessionmaker, make_task, fake_ocr):
    task = make_task([
        ("合同.docx", sample("contract_basic")),
        ("报价单.xlsx", fixture("xlsx/quotation.xlsx")),
        ("文字版.pdf", fixture("pdf/text_contract.pdf")),
    ])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
        assert db.get(Task, task.id).status == "awaiting_confirm"
    assert fake_ocr == {"recognize": 0, "slot": 0}

    task = make_task([("混合.pdf", fixture("pdf/mixed.pdf")), ("报价函.png", fixture("images/quotation_clear.png"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
        assert db.get(Task, task.id).status == "awaiting_confirm"
    assert fake_ocr == {"recognize": 2, "slot": 2}  # 混合 PDF 的 1 个扫描页 + 1 张图片，各占一次锁


def test_ocr_budget_checked_before_ocr(db_sessionmaker, make_task, fake_ocr, monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_OCR_PAGES_PER_TASK", 2)
    # 图片 1 页 + 扫描版 PDF 2 页 = 3 页：图片照常 OCR，PDF 在 OCR 之前失败
    task = make_task([("报价函.png", fixture("images/quotation_clear.png")),
                      ("扫描合同.pdf", fixture("pdf/scanned_contract.pdf"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert (t.status, t.error_message) == ("parse_failed", "扫描合同.pdf：扫描页和图片合计超过 100 页")
        assert count(db, DocNode, task.id) == 0
    assert fake_ocr == {"recognize": 1, "slot": 1}

    # 恰好等于上限：通过
    task = make_task([("扫描合同.pdf", fixture("pdf/scanned_contract.pdf"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
        assert db.get(Task, task.id).status == "awaiting_confirm"
    assert fake_ocr == {"recognize": 3, "slot": 2}

    # 图片按 1 页计：两张图片超过上限 1 时，第二张不 OCR
    monkeypatch.setattr(pipeline, "MAX_OCR_PAGES_PER_TASK", 1)
    task = make_task([("a.png", fixture("images/quotation_clear.png")), ("b.jpg", fixture("images/quotation_lowq.jpg"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
        assert db.get(Task, task.id).error_message == "b.jpg：扫描页和图片合计超过 100 页"
    assert fake_ocr == {"recognize": 4, "slot": 3}


def test_pdf_page_limit_fails_before_ocr(db_sessionmaker, make_task, fake_ocr, monkeypatch):
    from app.parsing import pdf

    monkeypatch.setattr(pdf, "MAX_PDF_PAGES", 1)
    task = make_task([("扫描合同.pdf", fixture("pdf/scanned_contract.pdf"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, task.id)
        assert db.get(Task, task.id).error_message == "扫描合同.pdf：PDF 超过 200 页"
    assert fake_ocr == {"recognize": 0, "slot": 0}


def test_ocr_slot_is_global_lock():
    assert worker.ocr_slot() is worker.ocr_slot()
    with worker.ocr_slot():
        assert not worker.ocr_slot().acquire(blocking=False)
    assert worker.ocr_slot().acquire(blocking=False)
    worker.ocr_slot().release()


# ---------------------------------------------------------------- M4-04：疑似遗漏、用户词条、已记住的名称、命中计数


@pytest.fixture
def text_task(db_sessionmaker, make_task):
    """直接写入正文节点（原文加密，每行一个段落 F1-P1…）并脱敏的任务，状态 awaiting_confirm。"""

    def _make(*lines: str | tuple[str, str], stance: str = "sales") -> Task:
        task = make_task([("文件.docx", b"placeholder")], stance=stance)
        with db_sessionmaker() as db:
            f = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
            for k, line in enumerate(lines, start=1):
                loc, text = line if isinstance(line, tuple) else (f"F1-P{k}", line)
                db.add(DocNode(task_id=task.id, file_id=f.id, loc=loc, ord=k, kind="paragraph", part="body",
                               text_enc=crypto.encrypt_text(text)))
            db.add(DocNode(task_id=task.id, file_id=f.id, loc="F1-H1", ord=0, kind="paragraph", part="header",
                           text_enc=crypto.encrypt_text("页眉 Header Text")))
            t = db.get(Task, task.id)
            engine.redact_task(db, t)
            t.status = "awaiting_confirm"
            db.commit()
        return task

    return _make


def suspects_of(db, task_id):
    return list(db.scalars(select(RedactionSuspect).where(RedactionSuspect.task_id == task_id)
                           .order_by(RedactionSuspect.loc, RedactionSuspect.char_start)))


def outbound(db, task_id):
    return engine.build_outbound_text(db, db.get(Task, task_id))


LINES = (
    "卖方：星河虚构进出口有限公司",
    "货物由 Northwind Fictive Holdings 承运，收款 6222 0000 1111 2222 333。",
    "Northwind Fictive Holdings 另行通知。",
    "备注：请联系蓝海示例科技公司办理。",
)


def test_parse_writes_suspects_and_views(db_sessionmaker, text_task):
    task = text_task(*LINES)
    with db_sessionmaker() as db:
        rows = suspects_of(db, task.id)
        assert {(r.loc, r.reason, r.decision) for r in rows} == {
            ("F1-P2", "capitalized", "pending"), ("F1-P2", "long_digits", "pending"),
            ("F1-P3", "capitalized", "pending"), ("F1-P4", "before_company", "pending")}
        views = engine.suspect_views(db, db.get(Task, task.id))
        assert [v["loc"] for v in views] == ["F1-P2", "F1-P2", "F1-P3", "F1-P4"]  # 文档顺序
        v = views[0]
        assert set(v) == {"id", "loc", "reason", "decision", "category", "text", "before", "after"}
        assert (v["text"], v["before"], v["after"][:4]) == ("Northwind Fictive Holdings", "货物由 ", " 承运，")
        assert views[3]["text"] == "蓝海示例科技公司"


def test_ignore_cascades_and_survives_recompute(db_sessionmaker, text_task, make_user):
    task = text_task(*LINES)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        first = next(r for r in suspects_of(db, task.id) if r.reason == "capitalized")
        uid = t.owner_id
        engine.decide_suspect(db, t, first.id, decision="ignore", category=None, user_id=uid)
        db.commit()
    with db_sessionmaker() as db:
        rows = suspects_of(db, task.id)
        # 文字相同、原因相同的其他待处理条目一并忽略（Q-70 ④）；其他原因的不受影响
        assert {(r.loc, r.reason, r.decision) for r in rows if r.reason == "capitalized"} == {
            ("F1-P2", "capitalized", "ignore"), ("F1-P3", "capitalized", "ignore")}
        assert all(r.decided_by == uid and r.decided_at is not None for r in rows if r.decision == "ignore")
        assert {r.decision for r in rows if r.reason != "capitalized"} == {"pending"}
        ids = {r.id for r in rows}
        # 重算（PUT /parties 同一路径）后保留原 id 和决定
        engine.apply_party_changes(db, db.get(Task, task.id), [])
        db.commit()
    with db_sessionmaker() as db:
        rows = suspects_of(db, task.id)
        assert {r.id for r in rows} == ids
        assert sum(r.decision == "ignore" for r in rows) == 2


def test_replace_suspect_becomes_user_term(db_sessionmaker, text_task):
    task = text_task(*LINES)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        rows = suspects_of(db, task.id)
        cap = next(r for r in rows if r.reason == "capitalized")
        digits = next(r for r in rows if r.reason == "long_digits")
        engine.decide_suspect(db, t, cap.id, decision="replace", category="ORG", user_id=t.owner_id)
        engine.decide_suspect(db, t, digits.id, decision="replace", category="CUSTOM", user_id=t.owner_id)
        db.commit()
    with db_sessionmaker() as db:
        out = outbound(db, task.id)
        # 全文替换：两处 Northwind 都换成同一代号，被替换的疑似遗漏不再列出
        assert "[F1-P2] 货物由 公司_A 承运，收款 [敏感词_1]。" in out and "[F1-P3] 公司_A 另行通知。" in out
        assert {r.reason for r in suspects_of(db, task.id)} == {"before_company"}
        parties = db.scalars(select(Party).where(Party.task_id == task.id)).all()
        assert {(p.code, p.role_label) for p in parties} == {("我方", "卖方"), ("公司_A", "其他")}
        items = db.scalars(select(RedactionItem).where(RedactionItem.task_id == task.id,
                                                       RedactionItem.detected_by == "user")).all()
        assert {(i.loc, i.category) for i in items} == {("F1-P2", "ORG"), ("F1-P3", "ORG"), ("F1-P2", "CUSTOM")}
        # 用户词条在重算后保留（设为我方也不丢）
        a = next(p for p in parties if p.code == "公司_A")
        engine.apply_party_changes(db, db.get(Task, task.id), [PartyChange(a.id, is_our_party=True)])
        db.commit()
    with db_sessionmaker() as db:
        out = outbound(db, task.id)
        assert "[F1-P2] 货物由 我方 承运，收款 [敏感词_1]。" in out and "Northwind" not in out
        assert engine.load_redaction_map(db, task.id)["我方"] == "Northwind Fictive Holdings"


def test_decide_suspect_errors(db_sessionmaker, text_task):
    task = text_task(*LINES)
    other = text_task(*LINES)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        theirs = suspects_of(db, other.id)[0]
        mine = suspects_of(db, task.id)[0]
        for sid in (theirs.id, 10 ** 12):
            with pytest.raises(engine.RedactionInputError) as e:
                engine.decide_suspect(db, t, sid, decision="ignore", category=None, user_id=None)
            assert (e.value.status, e.value.code) == (404, "SUSPECT_NOT_FOUND")
        for decision, category in (("replace", None), ("replace", "OUR"), ("delete", None)):
            with pytest.raises(engine.RedactionInputError) as e:
                engine.decide_suspect(db, t, mine.id, decision=decision, category=category, user_id=None)
            assert (e.value.status, e.value.code) == (422, "VALIDATION_ERROR")


def test_add_user_term_validation(db_sessionmaker, text_task):
    task = text_task("卖方：星河虚构进出口有限公司", "请联系 Bluefin Example 确认  。")
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)

        def err(**kw):
            args = {"loc": "F1-P2", "char_start": 4, "char_end": 19, "category": "ORG", **kw}
            with pytest.raises(engine.RedactionInputError) as e:
                engine.add_user_term(db, t, **args)
            return e.value.status, e.value.code

        assert err(loc="F1-P9") == (422, "INVALID_SELECTION")
        assert err(loc="F1-H1", char_start=0, char_end=2) == (422, "INVALID_SELECTION")  # 不是正文节点
        assert err(char_start=5, char_end=5) == (422, "INVALID_SELECTION")
        assert err(char_end=999) == (422, "INVALID_SELECTION")
        assert err(char_start=-1) == (422, "INVALID_SELECTION")
        assert err(category="OUR") == (422, "VALIDATION_ERROR")
        assert err(char_start=4, char_end=5) == (422, "SELECTION_LENGTH")
        assert err(char_start=22, char_end=25) == (422, "SELECTION_LENGTH")  # 去掉空白后不足 2 个字符
        assert err(loc="F1-P1", char_start=2, char_end=8) == (409, "ALREADY_REDACTED")
        assert err(char_start=5, char_end=12) == (422, "INVALID_SELECTION")  # 选在单词中间
        long_task = text_task("x" * 150)
        # 成功：去掉首尾空白，全文替换，类别为人名
        engine.add_user_term(db, t, loc="F1-P2", char_start=3, char_end=20, category="PERSON")
        db.commit()
    with db_sessionmaker() as db:
        assert "[F1-P2] 请联系 人员_1 确认  。" in outbound(db, task.id)
        item = db.scalar(select(RedactionItem).where(RedactionItem.task_id == task.id, RedactionItem.detected_by == "user"))
        assert (item.char_start, item.char_end, item.category) == (4, 19, "PERSON")
    with db_sessionmaker() as db:
        lt = db.get(Task, long_task.id)
        with pytest.raises(engine.RedactionInputError) as e:
            engine.add_user_term(db, lt, loc="F1-P1", char_start=0, char_end=101, category="CUSTOM")
        assert e.value.code == "SELECTION_LENGTH"


def test_manual_org_without_parties_can_become_our_party(db_sessionmaker, text_task):
    """Q-54：没有识别出签约方的文件，手动补充一个公司名后可设为我方。"""
    task = text_task("本协议由蓝海示例科技与对方共同签署。", stance="sales")
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert db.scalars(select(Party).where(Party.task_id == task.id)).all() == []
        engine.add_user_term(db, t, loc="F1-P1", char_start=4, char_end=10, category="ORG")
        db.commit()
    with db_sessionmaker() as db:
        (p,) = db.scalars(select(Party).where(Party.task_id == task.id)).all()
        assert (p.code, p.role_label, p.is_our_party, p.country) == ("公司_A", "其他", False, "国家未知")
        engine.apply_party_changes(db, db.get(Task, task.id), [PartyChange(p.id, is_our_party=True)])
        db.commit()
    with db_sessionmaker() as db:
        assert "[F1-P1] 本协议由我方与对方共同签署。" in outbound(db, task.id)


def test_learn_confirmed_names_and_second_task(db_sessionmaker, text_task):
    """9.10、Q-73、T-08 的 engine 部分：确认发送时写入，第二个任务自动替换。"""
    task = text_task("卖方：星河虚构进出口有限公司", "买方：Orion Fictive Trading LLC", "请联系 Bluefin Example 与 王示例。")
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        engine.add_user_term(db, t, loc="F1-P3", char_start=4, char_end=19, category="ORG")
        engine.add_user_term(db, t, loc="F1-P3", char_start=22, char_end=25, category="PERSON")
        engine.learn_confirmed_names(db, t, t.owner_id)
        engine.learn_confirmed_names(db, t, t.owner_id)  # 重复调用不重复插入
        db.commit()
    with db_sessionmaker() as db:
        rows = db.scalars(select(RedactionTerm).where(RedactionTerm.kind == "learned")).all()
        got = {(crypto.decrypt_text(r.term_enc), r.category, r.is_our_party) for r in rows}
        assert got == {("星河虚构进出口有限公司", "ORG", True), ("Orion Fictive Trading LLC", "ORG", False),
                       ("Bluefin Example", "ORG", False), ("王示例", "PERSON", False)}
        assert all(r.term_hash == engine.term_hash(crypto.decrypt_text(r.term_enc)) for r in rows)
        assert all(b"Bluefin" not in r.term_enc for r in rows)
        assert {r.hit_count for r in rows} == {0}
    # 第二个任务：已记住的名称自动替换，命中计数只在解析时增加，第二步重算不计数（Q-73 ④）
    second = text_task("BLUEFIN EXAMPLE 与王示例来函，抄送 Orion Fictive Trading LLC。")
    with db_sessionmaker() as db:
        out = outbound(db, second.id)
        assert "bluefin" not in out.casefold() and "王示例" not in out and "Orion" not in out
        items = db.scalars(select(RedactionItem).where(RedactionItem.task_id == second.id)).all()
        assert {i.detected_by for i in items} == {"term"}
        hits = {crypto.decrypt_text(r.term_enc): (r.hit_count, r.last_hit_at is not None)
                for r in db.scalars(select(RedactionTerm))}
        assert hits["Bluefin Example"] == (1, True) and hits["王示例"] == (1, True)
        assert hits["星河虚构进出口有限公司"] == (0, False)
        engine.apply_party_changes(db, db.get(Task, second.id), [])
        db.commit()
    with db_sessionmaker() as db:
        assert db.scalar(select(RedactionTerm.hit_count).where(
            RedactionTerm.term_hash == engine.term_hash("Bluefin Example"))) == 1
    # 我方只升不降：另一个任务把 Orion 设为我方后 is_our_party=true；之后不是我方也不改回
    third = text_task("买方：Orion Fictive Trading LLC", stance="purchase")
    with db_sessionmaker() as db:
        t3 = db.get(Task, third.id)
        (p,) = db.scalars(select(Party).where(Party.task_id == third.id)).all()
        assert p.is_our_party  # 立场为采购，买方预选为我方
        engine.learn_confirmed_names(db, t3, t3.owner_id)
        engine.learn_confirmed_names(db, db.get(Task, task.id), None)
        db.commit()
        assert db.scalar(select(RedactionTerm.is_our_party).where(
            RedactionTerm.term_hash == engine.term_hash("orion fictive trading llc"))) is True


def test_mask_quantity_setting(db_sessionmaker, text_task):
    with db_sessionmaker() as db:
        set_setting(db, "redaction.mask_quantity", True, None)
        db.commit()
    task = text_task("数量 500 MT，单价 USD 100/MT")
    with db_sessionmaker() as db:
        assert "[F1-P1] 数量 [数量_1] MT，单价 USD [单价_1]/MT" in outbound(db, task.id)


def test_outbound_quote_table_summary_xlsx(db_sessionmaker, text_task):
    cells = [("F1-S1-A1", "报价单"), ("F1-S1-A2", "品名"), ("F1-S1-B2", "单价(USD/MT)"), ("F1-S1-C2", "金额(USD)")]
    for row, (name, price, amount) in enumerate([("针状焦", "1250", "400000"), ("石油焦", "900", "90000"),
                                                  ("针状焦", "1100", "110000")], start=3):
        cells += [(f"F1-S1-A{row}", name), (f"F1-S1-B{row}", price), (f"F1-S1-C{row}", amount)]
    cells += [("F1-S1-A6", "合计"), ("F1-S1-C6", "USD 600000")]
    task = text_task(*cells)
    with db_sessionmaker() as db:
        lines = outbound(db, task.id).splitlines()
    assert lines[1:3] == ["[F1-S1-A1] 报价单", "[F1-S1] 【报价明细表：共 3 行，币种 USD，本地校验：未识别报价表结构，未做金额核验；明细逐格列在下方】"]
    assert len(lines) == len(cells) + 2
    assert lines[-2:] == ["[F1-S1-A6] 合计", "[F1-S1-C6] USD [金额_4]"]


# ---- R2-03：旧格式（模拟 converter，不需要 LibreOffice） ----

def test_v2_detail_table_note_and_cell_citations(db_sessionmaker, text_task):
    from app.redaction.engine import build_outbound
    from app.review.validate import load_node_views, validate_output
    from tests.test_review_validate import sample as review_sample, make, finding
    from tests.test_v2_quote_details import table_nodes

    task = text_task(*[(n.loc, n.text) for n in table_nodes()])
    with db_sessionmaker() as db:
        data = build_outbound(db, db.get(Task, task.id))
        views = load_node_views(db, task.id, data.quote_tables)
        ctx, _ = review_sample('contract_basic')
        note = views['F2-S1']
        result = validate_output(make(findings=[finding(locs=['F2-S1'], quote='明细逐格列在下方')]), ctx, views)
        assert result.findings[0].cite_status == 'verified'
        assert result.findings[0].locs == ['F2-S1-A1']
        assert '明细逐格列在下方' in note.redacted_text
        cell = views['F2-S1-C3']
        result = validate_output(make(findings=[finding(locs=['F2-S1-C3'], quote=cell.redacted_text)]), ctx, views)
        assert result.findings[0].cite_status == 'verified'
        assert result.findings[0].quote_snapshot == '50,000.00'

from tests.legacy_support import LEGACY, fake_converter, manifest, replay_handler, spool  # noqa: E402,F401


def _node_view(db, task_id, seq):
    rows = db.scalars(select(DocNode).where(DocNode.task_id == task_id, DocNode.loc.like(f"F{seq}-%"))
                      .order_by(DocNode.ord)).all()
    return [(n.loc, n.kind, n.part, crypto.decrypt_text(n.text_enc)) for n in rows]


def _legacy_warning(ext, base):
    return (f"已由 {ext.upper()} 格式转换为 {base.upper()} 后解析，"
            "个别格式（如自动编号、表格列）可能与原文显示不同")


def test_parse_legacy_formats_match_manifest(db_sessionmaker, make_task, spool):
    """五种旧格式经 pipeline 解析出与 manifest 一致的节点和警告，并附转换警告与 parser_version。"""
    names = [("合同.doc", "contract_basic.doc", "contract_basic_doc"),
             ("合同.wps", "contract_basic.wps", "contract_basic_wps"),
             ("合同.rtf", "contract_basic.rtf", "contract_basic_rtf"),
             ("报价.xls", "quotation.xls", "quotation_xls"),
             ("报价.et", "quotation.et", "quotation_et")]
    task = make_task([(n, (LEGACY / f).read_bytes()) for n, f, _ in names], policy="local_only")
    with fake_converter(spool, replay_handler()):
        with db_sessionmaker() as db:
            pipeline.parse_task(db, task.id)
    samples = manifest()
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert (t.status, t.error_message) == ("completed_local", None)
        files = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)).all()
        for seq, (f, (_, _, key)) in enumerate(zip(files, names), start=1):
            want = samples[key]
            base = "docx" if want["parse_as"] == "docx" else "xlsx"
            assert f.parser_version == f"{base}-1+lo25.2"
            assert f.warnings == want["warnings"] + [_legacy_warning(f.ext, base)]
            assert f.has_revisions == want["has_revisions"] and f.page_count == want["page_count"]
            got = _node_view(db, task.id, seq)
            assert len(got) == want["node_count"]
            assert [(n["loc"].replace("F1-", f"F{seq}-", 1), n["kind"], n["part"], n["text"])
                    for n in want["nodes"]] == got


def test_parse_legacy_ai_task_redacts_converted_text(db_sessionmaker, make_task, spool):
    task = make_task([("合同.doc", (LEGACY / "contract_basic.doc").read_bytes())])
    with fake_converter(spool, replay_handler()):
        with db_sessionmaker() as db:
            pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert t.status == "awaiting_confirm"
        assert "星河" not in engine.build_outbound_text(db, t)


def test_parse_legacy_failures_mark_parse_failed(db_sessionmaker, make_task, spool):
    task = make_task([("坏.doc", bytes.fromhex("d0cf11e0a1b11ae1") + b"garbage")])
    with fake_converter(spool, lambda kind, data: ("err", "FAILED")):
        with db_sessionmaker() as db:
            pipeline.parse_task(db, task.id)
    with db_sessionmaker() as db:
        t = db.get(Task, task.id)
        assert t.status == "parse_failed"
        assert t.error_message == "坏.doc：无法转换该文件（可能已加密或已损坏），请另存为 DOCX / XLSX 后重新上传"
        assert count(db, DocNode, task.id) == 0


def test_parse_legacy_converter_down_does_not_affect_docx(db_sessionmaker, make_task, spool):
    """没有心跳时旧格式提示服务不可用；DOCX 不经过 converter。"""
    bad = make_task([("合同.doc", (LEGACY / "contract_basic.doc").read_bytes())])
    ok = make_task([("合同.docx", sample("contract_basic"))])
    with db_sessionmaker() as db:
        pipeline.parse_task(db, bad.id)
        pipeline.parse_task(db, ok.id)
    with db_sessionmaker() as db:
        assert db.get(Task, bad.id).error_message == "合同.doc：文件转换服务不可用，请联系管理员"
        assert db.get(Task, ok.id).status == "awaiting_confirm"
