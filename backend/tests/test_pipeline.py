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
from app.db.models import DocNode, Party, RedactionItem, RedactionMap, RedactionTerm, Task, TaskFile
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
