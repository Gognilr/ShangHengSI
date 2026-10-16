"""parse 处理函数（SPEC 7.1、7.5，设计 docs/design/M2.md 第一、六节）。

解析全部文件 → 写 doc_nodes（原文加密）和 task_files 的解析信息 → 识别文件类型 →
外发策略为 ai 时脱敏并置 awaiting_confirm；local_only 时不做任何脱敏，直接 completed_local。
全部在一个事务中完成，中途失败不会留下一半的数据。
失败时 status=parse_failed，error_message 写明文件名和原因，不含原文；日志不写文件名和任何文本。
"""
import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import jobs
from app.db.models import DocNode, Task, TaskFile
from app.parsing.docx import PARSER_VERSION, parse_docx
from app.parsing.doctype import detect_doc_type
from app.parsing.model import ParseError
from app.redaction.engine import redact_task
from app.security import crypto
from app.tasks import storage
from app.worker import job_handler

logger = logging.getLogger("app.tasks.pipeline")

MSG_UNSUPPORTED = "该格式的解析将在后续版本提供"
MSG_INTERNAL = jobs.PARSE_INTERNAL_MSG
_HEAD_CHARS = 1000  # 文件类型识别只看正文前 1000 字（SPEC 8.6）


def _parse_file(db: Session, task: Task, f: TaskFile) -> None:
    if f.ext != "docx":  # M2 只支持 DOCX；PDF、XLSX、图片在 M3 实现
        raise ParseError(MSG_UNSUPPORTED)
    parsed = parse_docx(storage.load_original(f.storage_key), f.seq)
    db.add_all(
        DocNode(
            task_id=task.id, file_id=f.id, loc=n.loc, ord=n.ord, kind=n.kind, part=n.part,
            parent_loc=n.parent_loc, clause_no=n.clause_no, text_enc=crypto.encrypt_text(n.text),
            page=n.page, bbox=n.bbox, sheet=n.sheet, cell_ref=n.cell_ref, ocr_conf=n.ocr_conf,
            has_revision=n.has_revision,
        )
        for n in parsed.nodes
    )
    f.parser_version = PARSER_VERSION
    f.page_count = parsed.page_count
    f.has_revisions = parsed.has_revisions
    f.warnings = parsed.warnings
    head = "\n".join(n.text for n in parsed.nodes if n.part == "body")[:_HEAD_CHARS]
    f.doc_type = detect_doc_type(f.original_name, head)
    f.doc_type_source = "auto"
    db.flush()


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
    try:
        db.execute(delete(DocNode).where(DocNode.task_id == task.id))
        for f in files:
            current = f.original_name
            _parse_file(db, task, f)
        current = None
        if task.policy == "local_only":
            task.status = "completed_local"  # 不外发：不产生任何脱敏数据（M6 起在此之前做本地校验）
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
