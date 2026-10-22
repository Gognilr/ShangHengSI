"""parse 处理函数（SPEC 7.1、7.5，设计 docs/design/M2.md 第一、六节，docs/design/M3.md 第一、六节）。

按扩展名分派解析器；需要 OCR 的文件（有扫描页的 PDF、图片）先检查任务内的 OCR 页数预算，
再整份在 worker.ocr_slot() 内解析（同一时间最多 1 个 OCR 任务，SPEC 21）。
解析全部文件 → 写 doc_nodes（原文加密）和 task_files 的解析信息 → 识别文件类型 →
外发策略为 ai 时脱敏并置 awaiting_confirm；local_only 时不做任何脱敏，直接 completed_local。
全部在一个事务中完成，中途失败不会留下一半的数据。
失败时 status=parse_failed，error_message 写明文件名和原因，不含原文；日志不写文件名和任何文本。
"""
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import worker
from app.checks.service import run_checks
from app.db import jobs
from app.db.base import utcnow
from app.db.models import DocNode, Task, TaskFile
from app.parsing import docx, image, pdf, xlsx
from app.parsing.doctype import detect_doc_type
from app.parsing.model import MAX_OCR_PAGES_PER_TASK, ParsedFile, ParseError
from app.redaction.engine import redact_task
from app.security import crypto
from app.tasks import storage
from app.worker import job_handler

logger = logging.getLogger("app.tasks.pipeline")

MSG_INTERNAL = jobs.PARSE_INTERNAL_MSG
MSG_OCR_BUDGET = "扫描页和图片合计超过 100 页"
_HEAD_CHARS = 1000  # 文件类型识别只看正文前 1000 字（SPEC 8.6）


def _check_ocr_budget(used: int, need: int) -> None:
    """在开始 OCR 之前判断任务合计 OCR 页数（SPEC 8.7，open_questions Q-61 ⑤）。"""
    if used + need > MAX_OCR_PAGES_PER_TASK:
        raise ParseError(MSG_OCR_BUDGET)


def _parse_bytes(ext: str, data: bytes, seq: int, ocr_used: int) -> tuple[ParsedFile, str]:
    """按扩展名分派解析器，返回 (解析结果, parser_version)。ocr_used 为本任务此前已 OCR 的页数。"""
    if ext == "docx":
        return docx.parse_docx(data, seq), docx.PARSER_VERSION
    if ext == "xlsx":
        return xlsx.parse_xlsx(data, seq), xlsx.PARSER_VERSION
    if ext == "pdf":
        need = len(pdf.inspect_pdf(data).scanned_pages)  # 页数超限也在这里失败
        if not need:
            return pdf.parse_pdf(data, seq), pdf.PARSER_VERSION
        _check_ocr_budget(ocr_used, need)
        with worker.ocr_slot():
            return pdf.parse_pdf(data, seq), pdf.PARSER_VERSION
    if ext in ("jpg", "png"):
        _check_ocr_budget(ocr_used, 1)
        with worker.ocr_slot():
            return image.parse_image(data, seq), image.PARSER_VERSION
    raise ValueError("unexpected ext")  # 上传检查只放行以上五种（tasks.service.EXT_MAP），走内部错误


def _parse_file(db: Session, task: Task, f: TaskFile, ocr_used: int) -> int:
    """解析一个文件并写入节点和解析信息，返回本文件 OCR 的页数。"""
    parsed, version = _parse_bytes(f.ext, storage.load_original(f.storage_key), f.seq, ocr_used)
    db.add_all(
        DocNode(
            task_id=task.id, file_id=f.id, loc=n.loc, ord=n.ord, kind=n.kind, part=n.part,
            parent_loc=n.parent_loc, clause_no=n.clause_no, text_enc=crypto.encrypt_text(n.text),
            page=n.page, bbox=n.bbox, sheet=n.sheet, cell_ref=n.cell_ref, ocr_conf=n.ocr_conf,
            has_revision=n.has_revision,
        )
        for n in parsed.nodes
    )
    f.parser_version = version
    f.page_count = parsed.page_count
    f.has_revisions = parsed.has_revisions
    f.warnings = parsed.warnings
    head = "\n".join(n.text for n in parsed.nodes if n.part == "body")[:_HEAD_CHARS]
    f.doc_type = detect_doc_type(f.original_name, head)
    f.doc_type_source = "auto"
    db.flush()
    return parsed.ocr_pages


def _fail(db: Session, task_id: uuid.UUID, file_name: str | None, reason: str) -> None:
    task = db.get(Task, task_id)
    if task is None:  # 解析期间任务被删除
        return
    task.status = "parse_failed"
    task.error_message = f"{file_name}：{reason}" if file_name else reason
    db.commit()


def parse_task(db: Session, task_id: uuid.UUID) -> None:
    task = db.get(Task, task_id)
    if task is None or task.status != "parsing":  # 已删除，或重复投递
        return
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
    current: str | None = None
    ocr_used = 0
    try:
        db.execute(delete(DocNode).where(DocNode.task_id == task.id))
        for f in files:
            current = f.original_name
            ocr_used += _parse_file(db, task, f, ocr_used)
        current = None
        run_checks(db, task, datetime.now(ZoneInfo("Asia/Shanghai")).date())
        if task.policy == "local_only":
            task.status = "completed_local"  # 不外发：不产生任何脱敏数据（M6 起在此之前做本地校验）
            task.last_completed_at = utcnow()  # 原件保留期清理据此计算到期时间（M7-06、Q-109）
        else:
            redact_task(db, task)
            task.status = "awaiting_confirm"
        task.error_message = None
        db.commit()
    except ParseError as exc:
        db.rollback()
        _fail(db, task_id, current, str(exc))
        logger.info("task %s 解析失败", task_id)
    except Exception:
        db.rollback()
        _fail(db, task_id, current, MSG_INTERNAL)
        raise  # job 记为 failed，last_error 只有异常类名


@job_handler("parse")
def handle_parse(db: Session, payload: dict) -> None:
    parse_task(db, uuid.UUID(str(payload["task_id"])))
