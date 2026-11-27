"""发票管家（V36-I-01，设计第八节）：每种格式一条、四项核对、合并同票、Excel 格式、owner 与 use.export、清除。

样本由 tests/fixtures/invoice/make_invoice_fixtures.py 生成，全部虚构，票面带“虚构样本”水印。
"""
import io
import zipfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select, update

from app.config import get_settings
from app.db.models import AuditLog, Job, LlmCall, Role
from app.invoice import checks, excel, recognize, service, store
from app.invoice.model import Invoice, Qr, Unrecognized
from tests.test_tasks_api import alice, files_dir  # noqa: F401

F = Path(__file__).parent / "fixtures" / "invoice"
URL = "/api/v1/invoice/batches"


def _read(name: str):
    ext = name.rsplit(".", 1)[1]
    res = recognize.recognize(name, ext, (F / name).read_bytes(), ocr_budget=100)
    assert not res.failures
    (inv,) = res.invoices
    return inv


@pytest.mark.parametrize("name, source, number, date, total, extra", [
    ("vat_special.pdf", "text", "26999000000000000001", "2026-11-03", "1060.00",
     {"kind": "数电票（增值税专用发票）", "seller_name": "虚构示例科技有限公司", "buyer_tax": "91999999MA00000002",
      "amount": Decimal("1000.00"), "tax": Decimal("60.00"), "remark": "虚构项目编号 XG-001",
      "item_text": "*信息技术服务*软件维护费 等 2 项", "rates": ["6%"]}),
    ("vat_special.xml", "xml", "26999000000000000001", "2026-11-03", "1060.00",
     {"kind": "数电票（增值税专用发票）", "seller_tax": "91999999MA00000001", "rates": ["6%"]}),
    ("vat_ordinary.ofd", "ofd", "00012345", "2026-10-15", "226.00",
     {"kind": "增值税电子普通发票", "code": "011999000111", "buyer_name": "虚构丁方咨询有限公司"}),
    ("scan.png", "photo", "26999000000000000002", "2026-11-05", "300.00",
     {"seller_name": "虚构乙方餐饮管理有限公司", "seller_tax": "91999999MA00000003", "tax": Decimal("16.98")}),
    ("photo.jpg", "photo", "26999000000000000003", "2026-11-08", "200.01",
     {"seller_tax": "91999999MA00000004", "amount": Decimal("177.00"), "rates": ["13%"]}),
])
def test_each_format(name, source, number, date, total, extra):
    inv = _read(name)
    assert (inv.source, inv.number, inv.date, inv.total) == (source, number, date, Decimal(total))
    for key, value in extra.items():
        assert getattr(inv, key) == value, key
    # 票面二维码只核对：PDF 与图片都解出，金额等于不含税金额的那张也不报不一致
    if name.endswith((".pdf", ".png", ".jpg")):
        assert inv.qr is not None and inv.qr.number == number
    assert checks.check(inv) == []


def _clean() -> Invoice:
    return Invoice(number="26999000000000000009", date="2026-11-01", seller_name="虚构示例科技有限公司",
                   seller_tax="91999999MA00000001", amount=Decimal("100.00"), tax=Decimal("13.00"),
                   total=Decimal("113.00"), total_cn="壹佰壹拾叁元整",
                   qr=Qr("31", "", "26999000000000000009", Decimal("113.00"), "2026-11-01"))


def test_four_checks():
    assert checks.check(_clean()) == []
    inv = _clean()
    inv.tax = Decimal("13.01")
    assert checks.check(inv) == ["价税合计与金额加税额不等"]
    inv = _clean()
    inv.total_cn = "壹佰壹拾肆元整"
    assert checks.check(inv) == ["大写与小写金额不一致"]
    inv = _clean()
    inv.qr.date, inv.qr.amount = "2026-11-02", Decimal("100.00")  # 金额等于不含税金额：不算不一致
    assert checks.check(inv) == ["二维码与票面不一致：日期"]
    inv = _clean()
    inv.source, inv.low = "photo", ["number", "total"]
    inv.buyer_name, inv.buyer_tax = "虚构甲方贸易有限公司", "91999999MA00000002"
    assert checks.check(inv) == ["照片识别，请核对：号码、价税合计"]
    inv.tax = None  # 没读到的关键字段同样提示
    assert checks.check(inv)[-1] == "未读到：税额"
    inv.buyer_tax = ""  # 照片里单位抬头（或抬头也没读到）的购买方税号没读到也提示；个人抬头不提示
    assert checks.check(inv)[-1] == "未读到：购买方税号、税额"
    inv.buyer_name = "张三"
    assert checks.check(inv)[-1] == "未读到：税额"
    inv.qr = None  # 没解出二维码：号码、日期没有第二来源，一并请核对
    assert checks.check(inv)[0] == "照片识别，请核对：号码、日期、价税合计"


def test_merge_same_invoice():
    pdf, xml = _read("vat_special.pdf"), _read("vat_special.xml")
    pdf.buyer_name = "OCR 读错的名称"  # 结构化数据优先
    (row,) = checks.finalize([pdf, xml])
    assert row.files == ["vat_special.pdf", "vat_special.xml"] and row.source == "xml"
    assert row.buyer_name == "虚构甲方贸易有限公司" and row.qr is not None and row.hints == []


def test_excel_format():
    rows = checks.finalize([_read("vat_ordinary.ofd"), _read("vat_special.pdf")])
    red = _clean()
    red.number, red.amount, red.tax, red.total, red.total_cn, red.qr = "26999000000000000010", Decimal("-100.00"), \
        Decimal("-13.00"), Decimal("-113.00"), "负壹佰壹拾叁元整", None
    rows.append(red)
    rows[0].hints = ["大写与小写金额不一致"]
    wb = load_workbook(io.BytesIO(excel.build(rows, [Unrecognized("说明.txt", "不支持的文件格式")])))
    ws = wb["发票清单"]
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref
    assert [c.value for c in ws[1]][:5] == ["序号", "票种", "发票代码", "发票号码", "开票日期"]
    code, number = ws["C2"], ws["D2"]
    assert (code.value, code.data_type, number.value, number.data_type) == ("011999000111", "s", "00012345", "s")
    assert ws["G3"].value == "91999999MA00000002" and ws["G3"].data_type == "s"
    assert ws["M3"].value == Decimal("1060.00") or ws["M3"].value == 1060
    assert ws["M3"].number_format == "#,##0.00"
    assert ws["Q2"].fill.fgColor.rgb.endswith("FFF3D6")
    total = [c.value for c in ws[5]]
    assert total[:2] == ["合计", "3 张"] and [Decimal(str(v)) for v in total[10:13]] == [
        Decimal("1100.00"), Decimal("73.00"), Decimal("1173.00")]  # 红字票按负数计入
    assert [c.value for c in wb["未识别"][2]] == ["说明.txt", "不支持的文件格式"]


# ---------------------------------------------------------------- 接口


@pytest.fixture
def mem(files_dir, tmp_path, monkeypatch):  # noqa: F811
    d = tmp_path / "invoice_mem"
    monkeypatch.setenv("INVOICE_MEM_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in ("vat_special.pdf", "vat_special.xml", "vat_ordinary.ofd"):
            zf.writestr(f"发票/{name}", (F / name).read_bytes())
        zf.writestr("发票/说明.txt", "虚构")
    return buf.getvalue()


def _run(db_sessionmaker):
    from app.invoice import job

    with db_sessionmaker() as db:
        payload = db.scalars(select(Job.payload).where(Job.type == "invoice")).one()
        job.handle(db, payload)
        db.execute(update(Job).where(Job.type == "invoice").values(status="done", finished_at=func.now()))
        db.commit()


def test_batch_api_owner_export_and_clear(mem, alice, make_user, login, db_sessionmaker):  # noqa: F811
    client, csrf = alice
    h = {"X-CSRF-Token": csrf}
    r = client.post(URL, headers=h, files=[("files", ("一批.zip", _zip(), "application/zip"))])
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert client.get(f"{URL}/{bid}").json()["status"] == "queued"
    # 内存盘里只有密文，jobs 行只有批次 id 与 owner_id
    assert all(b"%PDF" not in p.read_bytes() for p in (mem / bid).iterdir())
    with db_sessionmaker() as db:
        assert set(db.scalars(select(Job.payload)).one()) == {"batch_id", "owner_id"}
    _run(db_sessionmaker)

    body = client.get(f"{URL}/{bid}").json()
    assert body["status"] == "done" and body["progress"] == {"done": 3, "total": 3}
    assert [(x["number"], x["files"]) for x in body["rows"]] == [
        ("00012345", ["vat_ordinary.ofd"]), ("26999000000000000001", ["vat_special.pdf", "vat_special.xml"])]
    assert body["unrecognized"] == [{"name": "说明.txt", "reason": "不支持的文件格式"}]
    assert body["summary"]["total"] == "1286.00"
    assert not list((mem / bid).glob("f*.bin"))  # 识别完原件即删，只留结果

    # 非本人 404；没有 use.export 不能下载
    make_user("bob")
    bob, bob_csrf = login("bob")
    assert bob.get(f"{URL}/{bid}").status_code == 404
    assert bob.get(f"{URL}/{bid}/excel").status_code == 404
    assert bob.delete(f"{URL}/{bid}", headers={"X-CSRF-Token": bob_csrf}).status_code == 404
    r = client.get(f"{URL}/{bid}/excel")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert load_workbook(io.BytesIO(r.content))["发票清单"]["D3"].value == "26999000000000000001"
    with db_sessionmaker() as db:
        audit = db.scalars(select(AuditLog).where(AuditLog.action == "invoice_export")).one()
        assert audit.detail == {"count": 2}
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 0
        role = db.scalar(select(Role).where(Role.builtin == "user"))
        role.permissions = [p for p in role.permissions if p != "use.export"]
        db.commit()
    assert client.get(f"{URL}/{bid}/excel").status_code == 403
    assert client.post(URL, headers=h, files=[("files", ("a.xml", b"<a/>", "text/xml"))]).status_code == 403

    # 清除：目录与 jobs 行都不留
    assert client.delete(f"{URL}/{bid}", headers=h).status_code == 204
    assert not (mem / bid).exists() and client.get(f"{URL}/{bid}").status_code == 404
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_sweep_and_new_batch_replaces_old(mem, alice, db_sessionmaker):  # noqa: F811
    client, csrf = alice
    h = {"X-CSRF-Token": csrf}
    xml = [("files", ("a.xml", (F / "vat_special.xml").read_bytes(), "text/xml"))]
    first = client.post(URL, headers=h, files=xml).json()["id"]
    second = client.post(URL, headers=h, files=xml).json()["id"]  # 开始新的一批即清除上一批
    assert not (mem / first).exists() and (mem / second).is_dir()
    assert client.get(f"{URL}/{first}").status_code == 404
    with db_sessionmaker() as db:
        db.execute(update(Job).values(created_at=Job.created_at - timedelta(hours=2, minutes=1)))
        db.commit()
        service.sweep(db)
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert not (mem / second).exists()
    # 加密的压缩包与超限不收
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.pdf", b"%PDF-1.4")
    data = bytearray(buf.getvalue())
    data[6] |= 1  # 本地文件头的加密标志
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 8] |= 1  # 中央目录的加密标志
    r = client.post(URL, headers=h, files=[("files", ("加密.zip", bytes(data), "application/zip"))])
    assert r.status_code == 422 and r.json()["code"] == "FILE_ENCRYPTED"
    assert store.list_dirs() == []
