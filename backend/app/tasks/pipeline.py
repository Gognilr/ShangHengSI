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
from app.db.models import DocNode, Task, TaskFile, TaskClauseVector
from app.parsing import docx, image, legacy, pdf, xlsx
from app.parsing.doctype import detect_doc_type
from app.parsing.model import MAX_OCR_PAGES_PER_TASK, ParsedFile, ParseError
from app.redaction.engine import redact_task
from app.redaction import stats
from app.security import crypto
from app.tasks import storage, filecheck
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
    if ext in legacy.LEGACY_EXTS:  # 不占 OCR 名额；转换结果只在内存中解析，不保存
        converted = legacy.convert(data, ext)
        if legacy.result_ext(ext) == "docx":
            parsed, version = docx.parse_docx(converted, seq), docx.PARSER_VERSION
        else:
            parsed, version = xlsx.parse_xlsx(converted, seq), xlsx.PARSER_VERSION
        parsed.warnings.append(legacy.converted_warning(ext))
        return parsed, f"{version}+{legacy.CONVERTER_TAG}"
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
    raise ValueError("unexpected ext")  # 上传检查只放行以上各种（tasks.service.EXT_MAP），走内部错误


def _parse_file(db: Session, task: Task, f: TaskFile, ocr_used: int, saved: list[str] | None = None) -> int:
    """解析一个文件并写入节点和解析信息，返回本文件 OCR 的页数。"""
    data = storage.load_original(f.storage_key)
    if f.ext in ("doc", "wps", "rtf") and task.kind == "review":
        converted = (storage.load_original(f.converted_storage_key) if f.converted_storage_key
                     else legacy.convert(data, f.ext))
        parsed, version = _parse_bytes("docx", converted, f.seq, ocr_used)
        parsed.warnings.append(legacy.converted_warning(f.ext))
        version += f"+{legacy.CONVERTER_TAG}"
        # 成功解析后才保存；重试复用已有结果，事务失败时由调用处清理新文件。
        if not f.converted_storage_key:
            f.converted_storage_key, _ = storage.save_original(converted)
            if saved is not None:
                saved.append(f.converted_storage_key)
    else:
        parsed, version = _parse_bytes(f.ext, data, f.seq, ocr_used)
    db.add_all(
        DocNode(
            task_id=task.id, file_id=f.id, loc=n.loc, ord=n.ord, kind=n.kind, part=n.part,
            parent_loc=n.parent_loc, clause_no=n.clause_no, text_enc=crypto.encrypt_text(n.text),
            page=n.page, bbox=n.bbox, sheet=n.sheet, cell_ref=n.cell_ref, ocr_conf=n.ocr_conf,
            has_revision=n.has_revision, hidden_spans=n.hidden_spans,
            formula_enc=crypto.encrypt_text(n.formula) if n.formula else None,
        )
        for n in parsed.nodes
    )
    f.parser_version = version
    f.page_count = parsed.page_count
    f.has_revisions = parsed.has_revisions
    f.warnings = parsed.warnings
    head = "\n".join(n.text for n in parsed.nodes if n.part == "body")[:_HEAD_CHARS]
    if f.doc_type_source != 'user':
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


def parse_task(db: Session, task_id: uuid.UUID, file_ids: list[str] | None = None) -> None:
    task = db.get(Task, task_id)
    if task is not None and task.kind == 'review' and task.status in ('draft', 'awaiting_confirm') and file_ids is not None:
        if task.latest_run_id is None:
            _parse_draft(db, task, file_ids)
        return
    if task is None or task.status != "parsing":  # 已删除，或重复投递
        return
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
    if task.kind == "assistant":
        # 会话只接受显式的新文件集合，避免旧 job 或重复投递覆盖已确认原文。
        selected = {uuid.UUID(str(fid)) for fid in (file_ids or [])}
        new_files = [f for f in files if f.id in selected and f.confirmed_at is None]
        if not selected or {f.id for f in new_files} != selected:
            # 已移除或已确认的批次属于过期投递，不能覆盖较新批次的状态。
            return
        _parse_assistant(db, task, new_files, [f for f in files if f.id not in selected])
        return
    current: str | None = None
    ocr_used = 0
    saved: list[str] = []
    try:
        db.execute(delete(DocNode).where(DocNode.task_id == task.id))
        for f in files:
            current = f.original_name
            ocr_used += _parse_file(db, task, f, ocr_used, saved)
        current = None
        run_checks(db, task, datetime.now(ZoneInfo("Asia/Shanghai")).date())
        if task.policy == "local_only":
            task.status = "completed_local"  # 不外发：不产生任何脱敏数据（M6 起在此之前做本地校验）
            task.last_completed_at = utcnow()  # 原件保留期清理据此计算到期时间（M7-06、Q-109）
        else:
            redact_task(db, task)
            stats.enter(task)
        task.error_message = None
        # 保存点隔离向量失败；清掉旧缓存，避免重解析失败后沿用旧原文向量。
        db.execute(delete(TaskClauseVector).where(TaskClauseVector.task_id == task.id))
        try:
            with db.begin_nested():
                from app.review.clauses import store_vectors
                store_vectors(db, task)
        except Exception:
            logger.warning('任务条款向量计算失败 task=%s', task.id)
        db.commit()
    except ParseError as exc:
        db.rollback()
        for key in saved:
            storage.delete_original(key)
        _fail(db, task_id, current, str(exc))
        logger.info("task %s 解析失败", task_id)
    except Exception:
        db.rollback()
        for key in saved:
            storage.delete_original(key)
        _fail(db, task_id, current, MSG_INTERNAL)
        raise  # job 记为 failed，last_error 只有异常类名


@job_handler("parse")
def handle_parse(db: Session, payload: dict) -> None:
    parse_task(db, uuid.UUID(str(payload["task_id"])), payload.get("file_ids"))


def _parse_assistant(db: Session, task: Task, files: list[TaskFile], old_files: list[TaskFile]) -> None:
    """与审核解析隔离：保留旧节点，不运行合同校验或合同向量。"""
    task_id, current = task.id, None
    try:
        ocr_used = 0
        for old in old_files:
            if old.ext in ("png", "jpg"):
                ocr_used += 1
            elif old.ext == "pdf":
                # 只检查旧 PDF 的扫描页数，不重新解析、不运行 OCR、不改旧节点。
                # 不能按 OCR 节点计页：空白扫描页可能没有节点，却仍消耗页数预算。
                ocr_used += len(pdf.inspect_pdf(storage.load_original(old.storage_key)).scanned_pages)
        db.execute(delete(DocNode).where(DocNode.task_id == task.id, DocNode.file_id.in_([f.id for f in files])))
        for file in files:
            current = file.original_name
            if file.ext in filecheck.AUDIO_EXTS:
                from app.parsing.audio import decode
                if not file.media_storage_key:
                    decode(db, task, file)
            else:
                ocr_used += _parse_file(db, task, file, ocr_used)
        if any(f.media and f.media.get('stage') != 'done' for f in files + old_files):
            db.commit()
            return  # M-04 接手 queued PCM 后才可进入确认脱敏。
        current = None
        from app.checks.service import run_table_checks
        run_table_checks(db, task)
        redact_task(db, task, style="neutral", pin=True)
        task.status, task.error_message = "awaiting_confirm", None
        db.commit()
    except ParseError as exc:
        db.rollback()
        for file in files:
            if file.ext in filecheck.AUDIO_EXTS and file.original_name == current:
                file.media = {**(file.media or {}), 'stage': 'failed', 'error': 'decode_failed'}
                file.parse_error = str(exc)
        _fail(db, task_id, current, str(exc))
        logger.info("task %s 解析失败", task_id)
    except Exception:
        db.rollback()
        _fail(db, task_id, current, MSG_INTERNAL)
        raise


def recompute_task(db: Session, task: Task):
    """草稿与未提交任务共用：保留用户决定，按当前主文件统一重算。"""
    from app.redaction import engine
    run_checks(db, task, datetime.now(ZoneInfo('Asia/Shanghai')).date())
    existing = engine._existing_parties(db, task)
    overrides = engine._current_overrides(existing) if any(p.confirmed for p in existing.values()) else None
    result = engine._run(db, task, overrides, existing, style='review', pin=False)
    try:
        with db.begin_nested():
            from app.review.clauses import store_vectors
            store_vectors(db, task)
    except Exception:
        logger.warning('任务条款向量计算失败 task=%s', task.id)
    return result


def _parse_draft(db: Session, task: Task, file_ids: list[str]) -> None:
    task_id, saved = task.id, []
    selected = {uuid.UUID(str(fid)) for fid in file_ids}
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
    new = [f for f in files if f.id in selected and f.parser_version is None]
    try:
        ocr_used = 0
        for old in files:
            if old in new or not old.parser_version:
                continue
            if old.ext in ('png', 'jpg'):
                ocr_used += 1
            elif old.ext == 'pdf':
                ocr_used += len(pdf.inspect_pdf(storage.load_original(old.storage_key)).scanned_pages)
        for file in new:
            count = len(saved)
            try:
                with db.begin_nested():
                    db.execute(delete(DocNode).where(DocNode.file_id == file.id))
                    used = _parse_file(db, task, file, ocr_used, saved)
                    file.parse_error = None
                ocr_used += used
            except ParseError as exc:
                for key in saved[count:]:
                    storage.delete_original(key)
                del saved[count:]
                file.parse_error = str(exc)
        # 解析各自进行，写入统一脱敏结果前锁任务并重读；后提交者能看到先提交的节点。
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True))
        if task is None or task.status not in ('draft', 'awaiting_confirm') or task.latest_run_id is not None:
            db.rollback()
            for key in saved:
                storage.delete_original(key)
            return
        result = recompute_task(db, task)
        from app.redaction import engine, keep_terms
        engine._count_hits(db, result.matched_terms)
        keep_terms.count_hits(db, task.owner_id, result.kept_memory)
        task.error_message = None
        db.commit()
    except Exception:
        db.rollback()
        for key in saved:
            storage.delete_original(key)
        if db.get(Task, task_id) is None:
            return
        for file in db.scalars(select(TaskFile).where(TaskFile.task_id == task_id, TaskFile.id.in_(selected),
                                                     TaskFile.parser_version.is_(None))):
            file.parse_error = MSG_INTERNAL
        db.commit()
        raise
