"""旧格式（DOC、WPS、RTF、XLS、ET）转换：经共享内存盘 /spool 交给 converter 容器（设计 docs/design/R2.md 3.3、3.4）。

worker 写入 in/{id}.{kind}，等待 out/{id}.docx|.xlsx 或 out/{id}.err；结果按上传时同样的规则复检后才返回。
明文只出现在 worker 内存和共享盘上，用完即删；日志只写事件，不写文件名和内容。
"""
import io
import logging
import os
import time
import uuid
from pathlib import Path

from app.config import get_settings
from app.parsing.model import ParseError
from app.security.errors import AppError
from app.tasks.filecheck import check_ooxml

logger = logging.getLogger("app.parsing.legacy")

CONVERTER_TAG = "lo25.2"  # 写入 parser_version，如 docx-1+lo25.2
ALIVE = "converter.alive"
ALIVE_MAX_AGE = 30  # 心跳期限（秒）
WAIT_SECONDS = 120.0  # 含排队的最长等待
POLL_SECONDS = 0.2

# 上传扩展名 → (交给 converter 的类型, 结果扩展名)；wps 按 doc、et 按 xls
KINDS = {"doc": ("doc", "docx"), "wps": ("doc", "docx"), "rtf": ("rtf", "docx"),
         "xls": ("xls", "xlsx"), "et": ("xls", "xlsx")}
LEGACY_EXTS = frozenset(KINDS)

MSG_UNAVAILABLE = "文件转换服务不可用，请联系管理员"
MSG_TIMEOUT = "旧格式文件转换超时"
MSG_FAILED = "无法转换该文件（可能已加密或已损坏），请另存为 DOCX / XLSX 后重新上传"


def result_ext(ext: str) -> str:
    """转换后按哪种格式解析：docx 或 xlsx。"""
    return KINDS[ext][1]


def converted_warning(ext: str) -> str:
    return (f"已由 {ext.upper()} 格式转换为 {result_ext(ext).upper()} 后解析，"
            "个别格式（如自动编号、表格列）可能与原文显示不同")


def _alive(spool: Path) -> bool:
    try:
        return time.time() - (spool / ALIVE).stat().st_mtime <= ALIVE_MAX_AGE
    except OSError:
        return False


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def convert(data: bytes, ext: str) -> bytes:
    """把旧格式文件转换为 DOCX / XLSX 的字节；失败抛出 ParseError（消息不含原文）。"""
    kind, out_ext = KINDS[ext]
    spool = Path(get_settings().convert_spool_dir)
    if not _alive(spool):
        raise ParseError(MSG_UNAVAILABLE)
    file_id = uuid.uuid4().hex
    src = spool / "in" / f"{file_id}.{kind}"
    part = spool / "in" / f"{file_id}.{kind}.part"
    result, err = spool / "out" / f"{file_id}.{out_ext}", spool / "out" / f"{file_id}.err"
    try:
        try:
            with open(part, "wb") as fh:
                fh.write(data)
            os.replace(part, src)  # 先写 .part 再改名，converter 只会看到完整文件
        except OSError:
            logger.warning("旧格式转换：写入共享盘失败")
            raise ParseError(MSG_UNAVAILABLE) from None
        deadline = time.monotonic() + WAIT_SECONDS
        while not (result.exists() or err.exists()):
            if time.monotonic() >= deadline:
                logger.warning("旧格式转换：等待超时")
                raise ParseError(MSG_TIMEOUT)
            time.sleep(POLL_SECONDS)
        if err.exists():
            logger.info("旧格式转换：converter 返回失败")
            raise ParseError(MSG_FAILED)
        try:
            converted = result.read_bytes()
        except OSError:
            raise ParseError(MSG_FAILED) from None
    finally:
        for p in (part, src, result, err):  # 超时放弃时 in/ 中可能仍有文件
            _unlink(p)
    try:
        check_ooxml(io.BytesIO(converted), out_ext, "converted")
    except AppError:
        logger.warning("旧格式转换：结果复检不通过")
        raise ParseError(MSG_FAILED) from None
    return converted
