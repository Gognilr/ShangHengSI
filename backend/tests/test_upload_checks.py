"""上传检查（SPEC 17.6、设计 docs/design/M2.md 第三节）：直接测试检查函数，不需要数据库。

接口层对每个错误码的返回见 test_tasks_api.py。
"""
import io
import zipfile

import pytest

from app.security.errors import AppError
from app.tasks import service
from app.tasks.service import check_uploads, clean_filename, inspect_upload

MB = 1024 * 1024
CONTENT_TYPES = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def docx_bytes(extra: dict[str, bytes] | None = None, content_types: bytes = CONTENT_TYPES) -> bytes:
    entries = {"[Content_Types].xml": content_types, "word/document.xml": b"<w:document/>"}
    entries.update(extra or {})
    return make_zip(entries)


def xlsx_bytes() -> bytes:
    return make_zip({"[Content_Types].xml": CONTENT_TYPES, "xl/workbook.xml": b"<workbook/>"})


PDF = b"%PDF-1.7\n%fake\n"
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def check(name, data, max_bytes=30 * MB):
    return inspect_upload(name, io.BytesIO(data), max_bytes)


def code_of(name, data, max_bytes=30 * MB) -> str:
    with pytest.raises(AppError) as ei:
        check(name, data, max_bytes)
    assert ei.value.status == 422
    return ei.value.code


# ---------------------------------------------------------------- 文件名


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("合同.docx", "合同.docx"),
        ("C:\\Users\\张三\\合同.docx", "合同.docx"),
        ("../../etc/合同.docx", "合同.docx"),
        ("a/b/c.pdf", "c.pdf"),
        ("合\x00同\n.docx", "合同.docx"),
        ("  报价.xlsx  ", "报价.xlsx"),
        (None, ""),
    ],
)
def test_clean_filename(raw, expected):
    assert clean_filename(raw) == expected


def test_clean_filename_truncates_to_255_keeping_extension():
    name = clean_filename("甲" * 400 + ".docx")
    assert len(name) == 255 and name.endswith(".docx")
    assert len(clean_filename("乙" * 400)) == 255


# ---------------------------------------------------------------- 通过的情形


@pytest.mark.parametrize(
    "name,data,ext",
    [
        ("a.docx", docx_bytes(), "docx"),
        ("a.XLSX", xlsx_bytes(), "xlsx"),
        ("a.pdf", PDF, "pdf"),
        ("a.jpg", JPG, "jpg"),
        ("a.JPEG", JPG, "jpg"),  # jpeg 存为 jpg
        ("a.png", PNG, "png"),
    ],
)
def test_valid_files_pass(name, data, ext):
    up = check(name, data)
    assert up.ext == ext and up.size == len(data) and up.name == name
    assert up.file.tell() == 0  # 检查后指针复位，后续可直接读取
    assert up.file.read() == data


# ---------------------------------------------------------------- 扩展名与文件头


@pytest.mark.parametrize("name", ["a.doc", "a.docm", "a.xlsm", "a.xls", "a.txt", "a.exe", "noext", "a.", ".gif"])
def test_unsupported_extension(name):
    assert code_of(name, docx_bytes()) == "UNSUPPORTED_FILE_TYPE"


@pytest.mark.parametrize(
    "name,data",
    [
        ("a.pdf", b"not a pdf"),
        ("a.pdf", b" %PDF-1.4"),
        ("a.jpg", PNG),
        ("a.png", JPG),
        ("a.jpg", b"\xff\xd8"),
        ("a.docx", b"plain text"),
        ("a.docx", b""),
        ("a.pdf", b""),
        ("a.docx", PDF),
        ("a.docx", xlsx_bytes()),  # xlsx 内容改名为 docx
        ("a.xlsx", docx_bytes()),  # docx 内容改名为 xlsx
        ("a.docx", make_zip({"word/document.xml": b"<w:document/>"})),  # 缺 [Content_Types].xml
        ("a.docx", make_zip({"[Content_Types].xml": CONTENT_TYPES})),  # 缺 word/document.xml
        ("a.docx", make_zip({})),
        ("a.docx", b"PK\x03\x04" + b"\x00" * 40),  # 文件头像 ZIP 但已损坏
    ],
)
def test_content_mismatch(name, data):
    assert code_of(name, data) == "FILE_CONTENT_MISMATCH"


# ---------------------------------------------------------------- 宏


def test_macro_vba_project_rejected():
    assert code_of("a.docx", docx_bytes({"word/vbaProject.bin": b"\x00"})) == "MACRO_NOT_ALLOWED"
    assert code_of("a.docx", docx_bytes({"word/VBAPROJECT.BIN": b"\x00"})) == "MACRO_NOT_ALLOWED"


def test_macro_enabled_content_type_rejected():
    ct = CONTENT_TYPES.replace(
        b"/>", b'><Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>'
    )
    assert b"macroEnabled" in ct
    assert code_of("a.docx", docx_bytes(content_types=ct)) == "MACRO_NOT_ALLOWED"
    xl = make_zip({"[Content_Types].xml": ct, "xl/workbook.xml": b"<workbook/>"})
    assert code_of("a.xlsx", xl) == "MACRO_NOT_ALLOWED"


# ---------------------------------------------------------------- 压缩炸弹


def test_too_many_archive_entries_rejected(monkeypatch):
    monkeypatch.setattr(service, "MAX_ARCHIVE_ENTRIES", 50)  # 缩小上限以加快测试
    extra = {f"word/media/{i}.txt": b"x" for i in range(60)}
    assert code_of("a.docx", docx_bytes(extra)) == "ARCHIVE_TOO_LARGE"
    assert check("a.docx", docx_bytes({f"word/m/{i}.txt": b"x" for i in range(20)}))  # 未超限仍通过


def test_entry_count_limit_is_10000():
    assert service.MAX_ARCHIVE_ENTRIES == 10000
    extra = {f"word/media/{i}.txt": b"" for i in range(10001)}
    assert code_of("a.docx", docx_bytes(extra)) == "ARCHIVE_TOO_LARGE"


def test_uncompressed_size_over_200mb_rejected():
    assert service.MAX_ARCHIVE_BYTES == 200 * MB
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("word/document.xml", b"<w:document/>")
        with zf.open("word/media/big.bin", "w", force_zip64=True) as w:
            chunk = b"\x00" * MB
            for _ in range(201):
                w.write(chunk)
    data = buf.getvalue()
    assert len(data) < 5 * MB  # 压缩后很小，只靠声明的解压大小判断
    assert code_of("a.docx", data) == "ARCHIVE_TOO_LARGE"


# ---------------------------------------------------------------- 大小与数量


def test_file_too_large():
    assert code_of("a.pdf", PDF + b"x" * 100, max_bytes=50) == "FILE_TOO_LARGE"
    assert check("a.pdf", PDF, max_bytes=len(PDF)).size == len(PDF)  # 恰好等于上限通过


def test_size_checked_before_extension():
    assert code_of("a.exe", b"x" * 100, max_bytes=10) == "FILE_TOO_LARGE"


def raw(n):
    return [(f"f{i}.pdf", io.BytesIO(PDF)) for i in range(n)]


def test_no_files():
    with pytest.raises(AppError) as ei:
        check_uploads([], 0, MB)
    assert (ei.value.status, ei.value.code) == (422, "NO_FILES")


def test_too_many_files():
    assert len(check_uploads(raw(10), 0, MB)) == 10
    with pytest.raises(AppError) as ei:
        check_uploads(raw(11), 0, MB)
    assert (ei.value.status, ei.value.code) == (422, "TOO_MANY_FILES")


@pytest.mark.parametrize("idx", [-1, 3, 99])
def test_main_index_out_of_range(idx):
    with pytest.raises(AppError) as ei:
        check_uploads(raw(3), idx, MB)
    assert (ei.value.status, ei.value.code) == (422, "VALIDATION_ERROR")


def test_one_bad_file_fails_whole_batch():
    files = raw(2) + [("x.docx", io.BytesIO(b"nope"))]
    with pytest.raises(AppError) as ei:
        check_uploads(files, 0, MB)
    assert ei.value.code == "FILE_CONTENT_MISMATCH"
