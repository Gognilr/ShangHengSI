"""文件结构检查（SPEC 17.6，设计 docs/design/R2.md 第二节、3.4）。

上传检查（tasks/service.py）与旧格式转换结果的复检（R2-03 的 parsing/legacy.py）共用本模块，
两处对 DOCX / XLSX 执行同一个 check_ooxml。不通过时抛出 422 的 AppError，消息只含文件名，不含内容。
"""
import zipfile
from typing import BinaryIO

import olefile

from app.security.errors import AppError

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024  # DOCX、XLSX 解压后总大小上限（17.6）
MAX_ARCHIVE_ENTRIES = 10000
MAX_CONTENT_TYPES_BYTES = 5 * 1024 * 1024

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
RTF_MAGIC = b"{\\rtf"
# OLE 中出现即拒绝的存储 / 流（名称不分大小写）：宏（Word 的 Macros、Excel 的 _VBA_PROJECT_CUR、任何 VBA 存储）
OLE_MACRO_NAMES = {"macros", "_vba_project_cur", "_vba_project", "vba"}
# 加密的 OOXML（Office 把加密后的 DOCX / XLSX 包在 OLE 中）
OLE_ENCRYPTED_NAMES = {"encryptioninfo", "encryptedpackage"}
# 主数据流：doc / wps 须有 WordDocument；xls / et 须有 Workbook（BIFF8）或 Book（BIFF5）
OLE_MAIN_STREAMS = {"doc": {"worddocument"}, "wps": {"worddocument"}, "xls": {"workbook", "book"}, "et": {"workbook", "book"}}


def bad(code: str, message: str) -> AppError:
    return AppError(422, code, message)


def _mismatch(name: str) -> AppError:
    return bad("FILE_CONTENT_MISMATCH", f"文件内容与扩展名不符：{name}")


def _macro(name: str) -> AppError:
    return bad("MACRO_NOT_ALLOWED", f"不允许上传含宏的文件：{name}")


def _encrypted(name: str) -> AppError:
    return bad("FILE_ENCRYPTED", f"文件已加密，请取消密码后重新上传：{name}")


def check_ooxml(
    f: BinaryIO,
    ext: str,
    name: str,
    *,
    max_entries: int = MAX_ARCHIVE_ENTRIES,
    max_bytes: int = MAX_ARCHIVE_BYTES,
) -> None:
    """DOCX / XLSX：合法 ZIP、条目数与解压总大小、必需部件、无 vbaProject、Content_Types 无 macroEnabled。"""
    try:
        zf = zipfile.ZipFile(f)
    except Exception:  # noqa: BLE001  不是合法 ZIP（含各种损坏情形）
        raise _mismatch(name) from None
    with zf:
        infos = zf.infolist()
        # 声明大小取自 ZIP 目录；zipfile 读取时不会超出声明大小，后续解析仍须按声明大小限制读取
        if len(infos) > max_entries or sum(i.file_size for i in infos) > max_bytes:
            raise bad("ARCHIVE_TOO_LARGE", f"文件解压后过大：{name}")
        names = {i.filename for i in infos}
        required = "word/document.xml" if ext == "docx" else "xl/workbook.xml"
        if "[Content_Types].xml" not in names or required not in names:
            raise _mismatch(name)
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise _macro(name)
        try:
            with zf.open("[Content_Types].xml") as c:
                content_types = c.read(MAX_CONTENT_TYPES_BYTES + 1)
        except Exception:  # noqa: BLE001  条目损坏、加密或压缩方式不支持
            raise _mismatch(name) from None
        if len(content_types) > MAX_CONTENT_TYPES_BYTES:
            raise _mismatch(name)
        if b"macroenabled" in content_types.lower():
            raise _macro(name)


def _ole_names(f: BinaryIO) -> tuple[set[str], set[str]] | None:
    """读取 OLE 目录：返回（全部存储与流的名称, 根下的流名称），均为小写；不是合法 OLE 时返回 None。

    只读目录，不读取流的内容。olefile 的任何异常都按“不是合法 OLE”处理。
    """
    try:
        with olefile.OleFileIO(f) as ole:
            entries = ole.listdir(streams=True, storages=True)
            root_streams = {e[0].lower() for e in entries if len(e) == 1 and ole.get_type(e) == olefile.STGTY_STREAM}
    except Exception:  # noqa: BLE001
        return None
    return {part.lower() for e in entries for part in e}, root_streams


def is_encrypted_ooxml(f: BinaryIO) -> bool:
    """OLE 文件头且含 EncryptionInfo / EncryptedPackage：加了打开密码的 DOCX / XLSX。读完后指针复位。"""
    f.seek(0)
    if f.read(8) != OLE_MAGIC:
        f.seek(0)
        return False
    f.seek(0)
    found = _ole_names(f)
    f.seek(0)
    return found is not None and bool(found[0] & OLE_ENCRYPTED_NAMES)


def check_ole(f: BinaryIO, ext: str, name: str) -> None:
    """DOC、WPS、XLS、ET：OLE 文件头与目录合法；加密的 OOXML、含宏的拒绝；须含对应的主数据流。

    二进制格式自身的密码保护（Word 的 fEncrypted、Excel 的 FILEPASS）在这里不判断，
    LibreOffice 打不开时按转换失败处理（设计第二节）。
    """
    f.seek(0)
    if f.read(8) != OLE_MAGIC:
        raise _mismatch(name)
    f.seek(0)
    found = _ole_names(f)
    if found is None:
        raise _mismatch(name)
    names, root_streams = found
    if names & OLE_ENCRYPTED_NAMES:
        raise _encrypted(name)
    if names & OLE_MACRO_NAMES:
        raise _macro(name)
    if not root_streams & OLE_MAIN_STREAMS[ext]:
        raise _mismatch(name)


def check_rtf(f: BinaryIO, name: str) -> None:
    f.seek(0)
    if f.read(len(RTF_MAGIC)) != RTF_MAGIC:
        raise _mismatch(name)
