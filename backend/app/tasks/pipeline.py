"""parse 处理函数（SPEC 7.1、7.5，设计 docs/design/M2.md 第一、六节，docs/design/M3.md 第一、六节）。

按扩展名分派解析器；需要 OCR 的文件（有扫描页的 PDF、图片）先检查任务内的 OCR 页数预算，
再整份在 worker.ocr_slot() 内解析（同一时间最多 1 个 OCR 任务，SPEC 21）。
解析全部文件 → 写 doc_nodes（原文加密）和 task_files 的解析信息 → 识别文件类型 →
外发策略为 ai 时脱敏并置 awaiting_confirm；local_only 时不做任何脱敏，直接 completed_local。
全部在一个事务中完成，中途失败不会留下一半的数据。
失败时 status=parse_failed，error_message 写明文件名和原因，不含原文；日志不写文件名和任何文本。
"""
import logging
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import worker
from app.config import get_settings
from app.checks.service import run_checks
from app.db import jobs
from app.db.base import utcnow
from app.db.models import DocNode, Job, Task, TaskFile, TaskClauseVector
from app.parsing import docx, docx_images, image, legacy, pdf, progress, xlsx
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


def _check_ocr_budget(used: int, need: int, limit: int | None = None) -> None:
    """在开始 OCR 之前判断任务合计 OCR 页数（SPEC 8.7，open_questions Q-61 ⑤）。limit 为空时按文件审核上限。"""
    if used + need > (MAX_OCR_PAGES_PER_TASK if limit is None else limit):
        raise ParseError(MSG_OCR_BUDGET if limit is None else f"扫描页和图片合计超过 {limit} 页")


def _docx_with_images(data: bytes, seq: int, budget: int, say, images: bool) -> ParsedFile:
    """DOCX 正文内嵌的证照尺寸图片当场 OCR（V35-C-01），每张占 1 页名额，超出名额的只提示。"""
    parsed = docx.parse_docx(data, seq)
    if not images or not docx_images.select(data, parsed):
        return parsed
    total = min(len(docx_images.select(data, parsed)), max(0, budget))
    with worker.ocr_slot():
        say('ocr', 0, total)
        return docx_images.ocr_inline(data, parsed, seq, budget, on_ocr=lambda done: say('ocr', done, max(total, done)))


def _parse_bytes(ext: str, data: bytes, seq: int, ocr_used: int, report=None, *,
                 ocr_limit: int | None = None, images: bool = True) -> tuple[ParsedFile, str]:
    """按扩展名分派解析器，返回 (解析结果, parser_version)。ocr_used 为本任务此前已 OCR 的页数，ocr_limit 为上限。
    report(stage, done, total) 上报解析阶段（V321-E-06），不传则不上报。images=False 时不识别 DOCX 内嵌图片。"""
    say = report or (lambda *_: None)
    ocr_limit = MAX_OCR_PAGES_PER_TASK if ocr_limit is None else ocr_limit
    if ext == "docx":
        return _docx_with_images(data, seq, ocr_limit - ocr_used, say, images), docx.PARSER_VERSION
    if ext == "xlsx":
        return xlsx.parse_xlsx(data, seq), xlsx.PARSER_VERSION
    if ext in legacy.LEGACY_EXTS:  # 不占 OCR 名额；转换结果只在内存中解析，不保存
        say('converting')
        converted = legacy.convert(data, ext)
        say('reading')
        if legacy.result_ext(ext) == "docx":
            parsed, version = _docx_with_images(converted, seq, ocr_limit - ocr_used, say, images), docx.PARSER_VERSION
        else:
            parsed, version = xlsx.parse_xlsx(converted, seq), xlsx.PARSER_VERSION
        parsed.warnings.append(legacy.converted_warning(ext))
        return parsed, f"{version}+{legacy.CONVERTER_TAG}"
    if ext == "pdf":
        info = pdf.inspect_pdf(data)  # 页数超限也在这里失败
        if not info.scanned_pages:
            return pdf.parse_pdf(data, seq), pdf.PARSER_VERSION
        # 扫描页、混排页超出名额整份失败；只识别图片区域的页用剩余名额（V35-C-01）
        hard = len(info.scanned_pages) - len(info.region_pages)
        _check_ocr_budget(ocr_used, hard, None if ocr_limit == MAX_OCR_PAGES_PER_TASK else ocr_limit)
        budget = ocr_limit - ocr_used - hard
        need = hard + min(len(info.region_pages), budget)
        with worker.ocr_slot():
            say('ocr', 0, need)
            return pdf.parse_pdf(data, seq, on_ocr=lambda done: say('ocr', done, max(need, done)),
                                 region_budget=budget), pdf.PARSER_VERSION
    if ext in ("jpg", "png"):
        _check_ocr_budget(ocr_used, 1, None if ocr_limit == MAX_OCR_PAGES_PER_TASK else ocr_limit)
        with worker.ocr_slot():
            say('ocr', 0, 1)
            return image.parse_image(data, seq), image.PARSER_VERSION
    raise ValueError("unexpected ext")  # 上传检查只放行以上各种（tasks.service.EXT_MAP），走内部错误


def _old_ocr_pages(db: Session, old: TaskFile) -> int:
    """已解析文件占用的 OCR 页数：不重新解析、不运行 OCR、不改旧节点。"""
    if old.ext in ("png", "jpg"):
        return 1
    if old.ext == "pdf":
        # 不能按 OCR 节点计页：空白扫描页可能没有节点，却仍消耗页数预算。
        return len(pdf.inspect_pdf(storage.load_original(old.storage_key)).scanned_pages)
    # DOCX 内嵌图片（V35-C-01）按识别出文字的图片张数计
    locs = db.scalars(select(DocNode.loc).where(DocNode.file_id == old.id, DocNode.loc.like("%-IM%")))
    return len({m[1] for loc in locs if (m := re.match(r"^F\d+-IM(\d+)-", loc))})


def _parse_file(db: Session, task: Task, f: TaskFile, ocr_used: int, saved: list[str] | None = None) -> int:
    """解析一个文件并写入节点和解析信息，返回本文件 OCR 的页数。"""
    data = storage.load_original(f.storage_key)

    def report(stage: str, done: int | None = None, total: int | None = None) -> None:
        progress.file(db, task.id, f.id, stage, done, total)

    report('reading')
    if f.ext in ("doc", "wps", "rtf") and task.kind == "review":
        if not f.converted_storage_key:
            report('converting')
        converted = (storage.load_original(f.converted_storage_key) if f.converted_storage_key
                     else legacy.convert(data, f.ext))
        report('reading')
        parsed, version = _parse_bytes("docx", converted, f.seq, ocr_used)
        parsed.warnings.append(legacy.converted_warning(f.ext))
        version += f"+{legacy.CONVERTER_TAG}"
        # 成功解析后才保存；重试复用已有结果，事务失败时由调用处清理新文件。
        if not f.converted_storage_key:
            f.converted_storage_key, _ = storage.save_original(converted)
            if saved is not None:
                saved.append(f.converted_storage_key)
    else:
        parsed, version = _parse_bytes(f.ext, data, f.seq, ocr_used, report)
    db.add_all(
        DocNode(
            task_id=task.id, file_id=f.id, loc=n.loc, ord=n.ord, kind=n.kind, part=n.part,
            parent_loc=n.parent_loc, clause_no=n.clause_no, text_enc=crypto.encrypt_text(n.text),
            page=n.page, bbox=n.bbox, sheet=n.sheet, cell_ref=n.cell_ref, ocr_conf=n.ocr_conf,
            has_revision=n.has_revision, hidden_spans=n.hidden_spans,
            formula_enc=crypto.encrypt_text(n.formula) if n.formula else None,
            text_before_enc=crypto.encrypt_text(n.text_before) if n.text_before is not None and n.part == "body" else None,
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
    report('done')
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
    if task is not None and task.kind == 'bid':
        from app.bid.pipeline import parse_project
        parse_project(db, task_id)
        return
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
    task_id_ = task.id
    progress.start(db, task_id_, [f.id for f in files])
    try:
        db.execute(delete(DocNode).where(DocNode.task_id == task.id))
        for f in files:
            current = f.original_name
            ocr_used += _parse_file(db, task, f, ocr_used, saved)
        current = None
        progress.phase(db, task_id_, 'detect')
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
    finally:
        progress.finish(task_id_)


@job_handler("parse")
def handle_parse(db: Session, payload: dict) -> None:
    parse_task(db, uuid.UUID(str(payload["task_id"])), payload.get("file_ids"))


def _parse_assistant(db: Session, task: Task, files: list[TaskFile], old_files: list[TaskFile]) -> None:
    """与审核解析隔离：保留旧节点，不运行合同校验或合同向量。"""
    task_id, current = task.id, None
    try:
        # 与发送、加文件共用任务行锁；脱敏完成处还会重新锁定并读取全部批次。
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update()
                         .execution_options(populate_existing=True))
        if task is None:
            return
        ocr_used = 0
        progress.start(db, task_id, [f.id for f in files if not f.parser_version])
        for old in old_files:
            ocr_used += _old_ocr_pages(db, old)
        db.execute(delete(DocNode).where(DocNode.task_id == task.id,
                                        DocNode.file_id.in_([f.id for f in files if not f.parser_version])))
        for file in files:
            if file.parser_version:
                continue  # worker 重启后保留已完成文件，不覆盖先前的逐字稿。
            current = file.original_name
            if file.ext in filecheck.AUDIO_EXTS:
                from app.parsing.audio import decode
                if not file.media_storage_key:
                    # 前面已有转写在排队或进行时先不留 PCM，轮到时再解码（V31Q-114）
                    busy = db.scalar(select(func.count()).select_from(Job).where(
                        Job.type == 'transcribe', Job.status.in_(('queued', 'running')))) or 0
                    decode(db, task, file, pcm_now=busy < get_settings().worker_transcribe_concurrency)
                queued = db.scalar(select(Job.id).where(Job.type == 'transcribe',
                                   Job.payload['file_id'].astext == str(file.id), Job.status.in_(('queued', 'running'))).limit(1))
                if not queued and (file.media or {}).get('stage') != 'failed':
                    jobs.enqueue(db, 'transcribe', {'task_id': str(task.id), 'file_id': str(file.id)})
                db.commit()
            else:
                ocr_used += _parse_file(db, task, file, ocr_used)
                if task.purpose == 'chat':  # 认出是会话里某份文件的新一稿（V35-D-02）
                    from app.review.drafts import auto_link
                    auto_link(db, task, file)
        db.commit()
        from app.parsing.transcribe import finish
        progress.phase(db, task_id, 'detect')
        finish(db, task_id)
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
    finally:
        progress.finish(task_id)


def session_checks(db: Session, task: Task) -> None:
    """会话文件的本地校验：工作台会话与审核任务一致（合同校验 + 条款向量，V32-A-02），会议纪要、翻译只核验表格。"""
    if task.purpose != 'chat':
        from app.checks.service import run_table_checks
        run_table_checks(db, task)
        return
    run_checks(db, task, datetime.now(ZoneInfo("Asia/Shanghai")).date())
    try:
        with db.begin_nested():
            from app.review.clauses import store_vectors
            store_vectors(db, task)
    except Exception:
        logger.warning('任务条款向量计算失败 task=%s', task.id)


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
            ocr_used += _old_ocr_pages(db, old)
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
