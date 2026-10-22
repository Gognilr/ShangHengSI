"""旧格式转换（app/parsing/legacy.py，设计 docs/design/R2.md 3.4）：用线程模拟 converter 读写 /spool。"""
import io
import logging
import os
import time
import zipfile

import pytest

from app.parsing import legacy
from app.parsing.model import ParseError
from tests.legacy_support import LEGACY, beat, fake_converter, replay_handler, spool  # noqa: F401

DOC = LEGACY / "contract_basic.doc"
DOCX = LEGACY / "contract_basic_doc.docx"


def listing(spool, *dirs):
    return [p.name for d in dirs for p in (spool / d).iterdir()]


def test_convert_success_and_spool_cleaned(spool):
    with fake_converter(spool, replay_handler()) as seen:
        out = legacy.convert(DOC.read_bytes(), "doc")
    assert out == DOCX.read_bytes()
    assert seen == [("doc", DOC.read_bytes())]
    assert listing(spool, "in", "out") == []


@pytest.mark.parametrize("ext,sample,kind", [
    ("doc", "contract_basic.doc", "doc"), ("wps", "contract_basic.wps", "doc"), ("rtf", "contract_basic.rtf", "rtf"),
    ("xls", "quotation.xls", "xls"), ("et", "quotation.et", "xls")])
def test_kind_mapping(spool, ext, sample, kind):
    with fake_converter(spool, replay_handler()) as seen:
        out = legacy.convert((LEGACY / sample).read_bytes(), ext)
    assert seen[0][0] == kind
    assert out[:2] == b"PK" and legacy.result_ext(ext) == ("xlsx" if kind == "xls" else "docx")


def test_heartbeat_missing_or_stale(spool):
    with pytest.raises(ParseError, match="转换服务不可用"):
        legacy.convert(b"x", "doc")
    beat(spool)
    old = time.time() - 31
    os.utime(spool / "converter.alive", (old, old))
    with pytest.raises(ParseError, match="转换服务不可用"):
        legacy.convert(b"x", "doc")
    assert listing(spool, "in") == []  # 心跳不可用时不写入


def test_err_file_is_failure(spool):
    with fake_converter(spool, lambda kind, data: ("err", "BAD_OUTPUT")):
        with pytest.raises(ParseError, match="另存为 DOCX / XLSX"):
            legacy.convert(DOC.read_bytes(), "doc")
    assert listing(spool, "out") == []


def test_timeout_removes_pending_input(spool, monkeypatch):
    monkeypatch.setattr(legacy, "WAIT_SECONDS", 0.3)
    beat(spool)  # 没有线程取走文件，只有静态心跳
    with pytest.raises(ParseError, match="转换超时"):
        legacy.convert(DOC.read_bytes(), "doc")
    assert listing(spool, "in") == []


@pytest.mark.parametrize("body", [b"not a zip", b"PK\x03\x04broken", DOCX.read_bytes()[:200]])
def test_result_recheck_failure(spool, body):
    with fake_converter(spool, lambda kind, data: ("docx", body)):
        with pytest.raises(ParseError, match="无法转换该文件"):
            legacy.convert(DOC.read_bytes(), "doc")
    assert listing(spool, "out") == []


def test_result_with_macro_rejected(spool):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w/>")
        zf.writestr("word/vbaProject.bin", b"x")
    with fake_converter(spool, lambda kind, data: ("docx", buf.getvalue())):
        with pytest.raises(ParseError, match="无法转换该文件"):
            legacy.convert(DOC.read_bytes(), "doc")


def test_wrong_result_kind_not_picked_up(spool, monkeypatch):
    """xls 只等 .xlsx：converter 返回 .docx 不算结果，最终超时。"""
    monkeypatch.setattr(legacy, "WAIT_SECONDS", 0.3)
    with fake_converter(spool, lambda kind, data: ("docx", DOCX.read_bytes())):
        with pytest.raises(ParseError, match="转换超时"):
            legacy.convert((LEGACY / "quotation.xls").read_bytes(), "xls")


def test_logs_have_no_content_or_names(spool, monkeypatch, caplog):
    monkeypatch.setattr(legacy, "WAIT_SECONDS", 0.2)
    caplog.set_level(logging.DEBUG)
    beat(spool)
    secret = "机密合同".encode()
    with pytest.raises(ParseError):
        legacy.convert(secret, "doc")
    with fake_converter(spool, lambda kind, data: ("err", "FAILED")):
        with pytest.raises(ParseError):
            legacy.convert(secret, "doc")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "机密" not in text and ".doc" not in text


def test_warning_and_exts():
    assert legacy.LEGACY_EXTS == {"doc", "wps", "rtf", "xls", "et"}
    assert legacy.converted_warning("wps") == (
        "已由 WPS 格式转换为 DOCX 后解析，个别格式（如自动编号、表格列）可能与原文显示不同")
    assert "XLSX" in legacy.converted_warning("et")
