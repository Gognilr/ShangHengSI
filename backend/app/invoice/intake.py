"""收票与嗅探（设计第二节）：扩展名与文件头双重校验，压缩包只在内存中读。

超出上限、加密的压缩包、顶层的不支持格式一律不收（422，提示原因）；压缩包里不支持或内容不符的文件
记入“未识别”，压缩包里的压缩包不打开。
"""
import io
import posixpath
import zipfile
from dataclasses import dataclass

from app.invoice.model import Unrecognized
from app.security.errors import AppError
from app.tasks.service import clean_filename

MAX_FILES = 100
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_ZIP_ENTRIES = 200
MAX_OCR_PAGES = 100
EXTS = {"pdf": "pdf", "jpg": "jpg", "jpeg": "jpg", "png": "png", "xml": "xml", "ofd": "ofd", "zip": "zip"}
SUPPORTED_TEXT = "PDF、JPG、PNG、XML、OFD、ZIP"


@dataclass
class Item:
    name: str
    ext: str
    data: bytes


def _bad(code: str, message: str) -> AppError:
    return AppError(422, code, message)


def _sniff(ext: str, data: bytes) -> bool:
    head = data[:16]
    if ext == "pdf":
        return head.startswith(b"%PDF-")
    if ext == "jpg":
        return head.startswith(b"\xff\xd8\xff")
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "xml":
        return data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"<"
    if ext in ("zip", "ofd"):
        return head.startswith(b"PK\x03\x04")
    return False


def _zip(data: bytes, name: str) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        raise _bad("FILE_CONTENT_MISMATCH", f"压缩包无法读取：{name}") from None
    if len(infos) > MAX_ZIP_ENTRIES:
        raise _bad("TOO_MANY_FILES", f"压缩包内的条目超过 {MAX_ZIP_ENTRIES} 个：{name}")
    if any(i.flag_bits & 0x1 for i in infos):
        raise _bad("FILE_ENCRYPTED", f"压缩包已加密，请解压后再传：{name}")
    return zf


def _entry_name(info: zipfile.ZipInfo) -> str:
    """路径只取文件名；没有 UTF-8 标记的条目名按 GBK 还原（Windows 自带压缩常见）。"""
    raw = info.filename
    if not info.flag_bits & 0x800:
        try:
            raw = raw.encode("cp437").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return clean_filename(posixpath.basename(raw.replace("\\", "/")))


def _check_ofd(data: bytes, name: str) -> None:
    zf = _zip(data, name)
    if not any(posixpath.basename(i.filename).upper() == "OFD.XML" for i in zf.infolist()):
        raise _bad("FILE_CONTENT_MISMATCH", f"文件内容与扩展名不符：{name}")


def collect(raw: list[tuple[str | None, bytes]]) -> tuple[list[Item], list[Unrecognized]]:
    """上传的文件 → (待识别的文件, 未识别)。超限时抛出 422。"""
    if not raw:
        raise _bad("NO_FILES", "请至少选择一个文件")
    items: list[Item] = []
    skipped: list[Unrecognized] = []
    for raw_name, data in raw:
        name = clean_filename(raw_name) or "未命名"
        if len(data) > MAX_FILE_BYTES:
            raise _bad("FILE_TOO_LARGE", f"单个文件不超过 20 MB：{name}")
        ext = EXTS.get(name.rsplit(".", 1)[-1].lower() if "." in name else "")
        if ext is None:
            raise _bad("UNSUPPORTED_FILE_TYPE", f"不支持的文件格式：{name}（支持 {SUPPORTED_TEXT}）")
        if not _sniff(ext, data):
            raise _bad("FILE_CONTENT_MISMATCH", f"文件内容与扩展名不符：{name}")
        if ext == "ofd":
            _check_ofd(data, name)
        if ext != "zip":
            items.append(Item(name, ext, data))
            continue
        zf = _zip(data, name)
        for info in zf.infolist():
            if info.is_dir():
                continue
            inner = _entry_name(info)
            parts = info.filename.replace("\\", "/").split("/")
            if not inner or parts[0] == "__MACOSX" or inner.startswith("."):
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise _bad("FILE_TOO_LARGE", f"单个文件不超过 20 MB：{inner}")
            inner_ext = EXTS.get(inner.rsplit(".", 1)[-1].lower() if "." in inner else "")
            if inner_ext == "zip":
                skipped.append(Unrecognized(inner, "压缩包里的压缩包未打开"))
                continue
            if inner_ext is None:
                skipped.append(Unrecognized(inner, "不支持的文件格式"))
                continue
            with zf.open(info) as f:
                body = f.read(MAX_FILE_BYTES + 1)  # 不信任条目头里的大小
            if len(body) > MAX_FILE_BYTES:
                raise _bad("FILE_TOO_LARGE", f"单个文件不超过 20 MB：{inner}")
            if not _sniff(inner_ext, body):
                skipped.append(Unrecognized(inner, "文件内容与扩展名不符"))
                continue
            if inner_ext == "ofd":
                try:
                    _check_ofd(body, inner)
                except AppError:
                    skipped.append(Unrecognized(inner, "文件内容与扩展名不符"))
                    continue
            items.append(Item(inner, inner_ext, body))
            if len(items) > MAX_FILES or sum(len(i.data) for i in items) > MAX_TOTAL_BYTES:
                break
    if len(items) + len(skipped) > MAX_FILES:
        raise _bad("TOO_MANY_FILES", f"每批不超过 {MAX_FILES} 个文件（压缩包按解开后计算）")
    if sum(len(i.data) for i in items) > MAX_TOTAL_BYTES:
        raise _bad("FILE_TOO_LARGE", "每批合计不超过 200 MB（压缩包按解开后计算）")
    if sum(1 for i in items if i.ext in ("jpg", "png")) > MAX_OCR_PAGES:
        raise _bad("TOO_MANY_FILES", f"扫描件与照片合计不超过 {MAX_OCR_PAGES} 页")
    if not items and not skipped:
        raise _bad("NO_FILES", "请至少选择一个文件")
    return items, skipped
