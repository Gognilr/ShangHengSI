"""标书解析：逐个解析待处理文件，每个文件单独提交，不对整份投标文件脱敏或外发。

待处理 = 尚未解析且没有错误提示的文件。解析期间不锁任务行，用户仍可往项目里加文件、改投标人名称；
新加的文件由同一循环接着解析。全局事务锁保证同一时间只解析一个标书文件（多进程、多线程均适用）。

投标文件 PDF 的扫描页延后识别（V34-B-04）：先解析文字页，扫描页放进 meta 的 ocr_pending，由 ocr_project
逐页补上；本地检查不必等 OCR，已完成初审的项目每隔一分钟及识别完成时重跑本地检查（做完一页补一页）。
"""
import json
import logging
import time
import uuid

from sqlalchemy import delete, select, text

from app.bid import fileprops
from app.bid.service import LIMITS, lock_task, originals_expired
from app.db.base import utcnow
from app.db.models import BidImage, BidProject, DocNode, Task, TaskClauseVector, TaskFile
from app.parsing import docx, docx_images, pdf
from app.parsing.model import ParseError
from app.security import crypto
from app.tasks import storage

logger = logging.getLogger(__name__)
_LOCK = 3401
_OCR_LOCK = 3402
_PAGE_GAP = 10000  # 延后识别的文件按页留出 ord 区间，OCR 节点插回所在页之后
RECHECK_SECONDS = 60
_OCR_OVER = f"项目扫描页和图片合计超过 {LIMITS['ocr_pages']} 页"


def _pending(task_id):
    return select(TaskFile).where(TaskFile.task_id == task_id, TaskFile.parser_version.is_(None),
                                  TaskFile.parse_error.is_(None)).order_by(TaskFile.seq)


def _ocr_used(db, task_id):
    rows = db.scalars(select(TaskFile.meta_enc).where(TaskFile.task_id == task_id, TaskFile.parser_version.is_not(None),
                                                      TaskFile.meta_enc.is_not(None)))
    total = 0
    for m in rows:
        meta = json.loads(crypto.decrypt_text(m))
        total += meta.get("ocr_pages", 0) + len(meta.get("ocr_pending") or [])
    return total


def _parse_one(db, file, used):
    """解析一个文件，返回 (节点, parser_version, meta)；meta 含文件属性与图片哈希。meta 与密码一样加密，不进日志或对外提示。"""
    data = storage.load_original(file.storage_key)
    if file.ext == "pdf":
        password = crypto.decrypt_text(file.password_enc) if file.password_enc is not None else ""
        options = {"password": password, "max_pages": LIMITS["pdf_pages"]}
        info = pdf.inspect_pdf(data, **options)
        need = len(info.scanned_pages)
        hard = need - len(info.region_pages)  # 只识别图片区域的页用剩余名额，不让文件失败（V35-C-01）
        if used + hard > LIMITS["ocr_pages"]:
            raise ParseError(_OCR_OVER)
        options["region_budget"] = LIMITS["ocr_pages"] - used - hard
        if need and file.bidder_id is not None:
            parsed = pdf.parse_pdf(data, file.seq, defer_ocr=True, **options)
            counts = {}
            for n in parsed.nodes:  # 按页留出 ord 区间
                counts[n.page] = counts.get(n.page, 0) + 1
                n.ord = (n.page or 0) * _PAGE_GAP + counts[n.page]
        elif need:
            from app.worker import ocr_slot
            with ocr_slot():
                parsed = pdf.parse_pdf(data, file.seq, **options)
        else:
            parsed = pdf.parse_pdf(data, file.seq, **options)
        return parsed, pdf.PARSER_VERSION, _meta(file, data, parsed, password)
    if file.ext in ("jpg", "png") and used + 1 > LIMITS["ocr_pages"]:
        raise ParseError(_OCR_OVER)
    if file.ext == "docx" and file.bidder_id is not None:
        # 投标文件的内嵌图片与扫描页一样延后识别（V35-C-01）
        parsed = docx_images.defer(data, docx.parse_docx(data, file.seq), LIMITS["ocr_pages"] - used)
        return parsed, docx.PARSER_VERSION, _meta(file, data, parsed)
    from app.tasks.pipeline import _parse_bytes
    parsed, version = _parse_bytes(file.ext, data, file.seq, used, ocr_limit=LIMITS["ocr_pages"])
    return parsed, version, _meta(file, data, parsed)


def _meta(file, data, parsed, password=None):
    """文件属性与图片哈希（B-03 比对用）：属性随 meta 加密，图片只写哈希行。"""
    props, images = fileprops.collect(file.ext, data, parsed.nodes, password)
    return {"ocr_pages": parsed.ocr_pages, "ocr_pending": parsed.ocr_pending, "props": props, "images": images}


def _auto_extract(db, task):
    """招标文件首次解析完成后自动做规则抽取；之后招标文件有变化只提示“重新抽取”，不覆盖用户的修改。"""
    from app.bid import requirements
    project = db.get(BidProject, task.id)
    if project.parameters_enc is None and requirements.parsed_tender_ids(db, task.id):
        requirements.extract(db, task)


def parse_project(db, task_id):
    while True:
        db.execute(text(f"SELECT pg_advisory_xact_lock({_LOCK})"))
        file = db.scalar(_pending(task_id).limit(1).with_for_update())
        if file is None:
            # 收尾时锁任务行：与上传互斥，避免把刚加入的文件漏在“已完成”状态之外
            task = db.scalar(select(Task).where(Task.id == task_id, Task.kind == "bid").with_for_update(key_share=True)
                             .execution_options(populate_existing=True))
            if task is None:
                db.rollback()
                return
            if db.scalar(_pending(task_id).limit(1)) is not None:
                db.commit()
                continue
            failed = db.scalar(select(TaskFile.id).where(TaskFile.task_id == task_id, TaskFile.parse_error.is_not(None)).limit(1))
            _auto_extract(db, task)
            queue_ocr(db, task_id)
            task.status = "parse_failed" if failed else "draft"
            task.error_message = "部分文件尚未解析，请处理文件行中的提示" if failed else None
            task.last_activity_at = utcnow()
            db.commit()
            return
        db.execute(delete(DocNode).where(DocNode.file_id == file.id))
        db.execute(delete(BidImage).where(BidImage.file_id == file.id))
        db.execute(delete(TaskClauseVector).where(TaskClauseVector.task_id == task_id, TaskClauseVector.file_seq == file.seq))
        file.page_count, file.warnings = None, []
        try:
            if file.original_deleted_at is not None:
                raise ParseError("原件已按保留期删除，无法重新解析")
            parsed, version, meta = _parse_one(db, file, _ocr_used(db, task_id))
        except ParseError as exc:
            file.parse_error = str(exc)
        except Exception:
            # 单个文件意外失败不拖垮其余文件；不带出异常内容
            db.rollback()
            db.execute(text(f"SELECT pg_advisory_xact_lock({_LOCK})"))
            file = db.get(TaskFile, file.id, populate_existing=True)
            if file is None:
                continue
            file.parse_error = "解析中断，请重新解析"
        else:
            # 标书只使用当前正文，不保存文件审核用于多稿对比的修订前文字。
            db.add_all(DocNode(task_id=task_id, file_id=file.id,
                               **{k: v for k, v in vars(n).items() if k not in ("text", "formula", "text_before")},
                               text_enc=crypto.encrypt_text(n.text), formula_enc=crypto.encrypt_text(n.formula) if n.formula else None)
                       for n in parsed.nodes)
            file.parser_version, file.page_count = version, parsed.page_count
            file.warnings, file.has_revisions = parsed.warnings, parsed.has_revisions
            images = meta.pop("images")
            file.meta_enc = crypto.encrypt_text(json.dumps(meta, ensure_ascii=False, default=str))
            db.add_all(BidImage(file_id=file.id, **image) for image in images)
        db.commit()


# ---------------------------------------------------------------- 延后识别的扫描页（V34-B-04）

def _metas(db, task_id):
    rows = db.scalars(select(TaskFile).where(TaskFile.task_id == task_id, TaskFile.bidder_id.is_not(None),
                                             TaskFile.parser_version.is_not(None), TaskFile.meta_enc.is_not(None),
                                             TaskFile.original_deleted_at.is_(None))
                      .order_by(TaskFile.seq))
    return [(f, json.loads(crypto.decrypt_text(f.meta_enc))) for f in rows]


def has_pending(db, task_id):
    return any(meta.get("ocr_pending") for _f, meta in _metas(db, task_id))


def queue_ocr(db, task_id):
    """有待识别的页且没有排队中的识别任务时入队；正在运行的识别任务会接着处理新加入的文件。"""
    from app.db import jobs
    from app.db.models import Job
    if not has_pending(db, task_id):
        return
    queued = db.scalar(select(Job.id).where(Job.type == "bid", Job.payload["task_id"].astext == str(task_id),
                                            Job.payload["stage"].astext == "ocr", Job.status == "queued").limit(1))
    if not queued:
        jobs.enqueue(db, "bid", {"task_id": str(task_id), "stage": "ocr"})


def _recheck(db, task_id):
    """已完成初审的项目重跑本地检查。检查任务正在运行时不等：它结束时发现识别有进展会自己再跑一轮（checks.run）。"""
    from app.bid import checks
    task = lock_task(db, task_id)
    project = db.get(BidProject, task_id, populate_existing=True) if task is not None else None
    if project is None or project.stage != "checked" or project.check_state != "done" or originals_expired(db, task_id):
        db.commit()
        return
    try:
        checks._run_once(db, task, progressive=False)
        db.commit()
    except Exception:
        logger.warning("标书扫描页识别后重跑本地检查失败 task=%s", task_id)
        db.rollback()
        return
    from app.bid import model
    try:
        task = lock_task(db, task_id)
        if task is not None and not has_pending(db, task_id):
            model.refresh_after_ocr(db, task)
        db.commit()
    except Exception:
        logger.warning("标书扫描页识别后准备模型核对失败 task=%s", task_id)
        db.rollback()


def _ocr_file(db, task_id, file_id, pages):
    """逐页识别一个文件，每页单独提交；文件被移除或重新解析时停下。返回是否写入了新页。"""
    from app.parsing.model import LOW_OCR_CONF, ParsedNode
    from app.worker import ocr_slot
    file = db.get(TaskFile, file_id, populate_existing=True)
    if file is None or file.original_deleted_at is not None:
        return False
    data = storage.load_original(file.storage_key)
    password = crypto.decrypt_text(file.password_enc) if file.password_enc is not None else None
    existing = [ParsedNode(loc=loc, ord=0, kind="paragraph", part="body", text="", page=page, bbox=bbox)
                for loc, page, bbox in db.execute(select(DocNode.loc, DocNode.page, DocNode.bbox).where(DocNode.file_id == file_id))]
    seq, is_docx = file.seq, file.ext == "docx"
    db.commit()
    wrote, last = False, time.monotonic()
    with ocr_slot():
        found = (docx_images.ocr_pending(data, seq, pages) if is_docx
                 else pdf.ocr_pages(data, seq, pages, existing, password=password))
        for page_no, nodes, confs in found:
            db.execute(text(f"SELECT pg_advisory_xact_lock({_LOCK})"))
            file = db.get(TaskFile, file_id, populate_existing=True)
            if file is None or file.original_deleted_at is not None or file.parser_version is None:
                db.rollback()
                return wrote
            meta = json.loads(crypto.decrypt_text(file.meta_enc)) if file.meta_enc is not None else {}
            if page_no not in (meta.get("ocr_pending") or []):
                db.rollback()
                return wrote
            for i, n in enumerate(nodes, 1):
                db.add(DocNode(task_id=task_id, file_id=file_id, loc=n.loc,
                               ord=n.ord if is_docx else page_no * _PAGE_GAP + _PAGE_GAP // 2 + i,
                               kind=n.kind, part=n.part, text_enc=crypto.encrypt_text(n.text), clause_no=n.clause_no,
                               parent_loc=n.parent_loc, page=n.page, bbox=n.bbox, ocr_conf=n.ocr_conf))
            meta["ocr_pending"] = [p for p in meta["ocr_pending"] if p != page_no]
            meta["ocr_pages"] = meta.get("ocr_pages", 0) + 1
            done = meta.setdefault("ocr_done", [])
            done.append(page_no)
            if confs is None:  # 只识别图片区域、图片中没有文字的页
                pass
            elif not confs:
                meta.setdefault("ocr_empty", []).append(page_no)
            elif any(c < LOW_OCR_CONF for c in confs):
                meta.setdefault("ocr_low", []).append(page_no)
            if not meta["ocr_pending"]:
                shown = "、".join(str(p) for p in sorted(done)[:30]) + (" 等" if len(done) > 30 else "")
                warnings = [w for w in (file.warnings or []) if "正在后台识别" not in w]
                if is_docx:  # 内嵌图片多为照片、徽标，没有文字不提示
                    warnings.append(docx_images.WARN_DONE.format(count=len(done)))
                    warnings += [docx_images.WARN_LOW] if meta.get("ocr_low") else []
                else:
                    warnings.append(f"第 {shown} 页为扫描页或图文混排，已用 OCR 识别")
                    warnings += [f"第 {p} 页 OCR 识别置信度低，请核对原件" for p in meta.get("ocr_low", [])[:5]]
                    warnings += [f"第 {p} 页未识别到文字" for p in meta.get("ocr_empty", [])]
                file.warnings = warnings
            file.meta_enc = crypto.encrypt_text(json.dumps(meta, ensure_ascii=False, default=str))
            db.commit()
            wrote = True
            if time.monotonic() - last >= RECHECK_SECONDS:
                _recheck(db, task_id)
                last = time.monotonic()
    return wrote


def ocr_project(db, task_id):
    """逐个文件补识别扫描页；同一项目同一时间只有一个识别循环（会话级咨询锁，用独立连接持有）。"""
    task_id = uuid.UUID(str(task_id))
    key = task_id.int % 2_000_000_000
    while True:
        conn = db.get_bind().connect()
        try:
            got = conn.execute(text("SELECT pg_try_advisory_lock(:a, :b)"), {"a": _OCR_LOCK, "b": key}).scalar()
            conn.commit()
            if not got:
                return
            try:
                wrote = False
                while True:
                    target = next(((f.id, list(m["ocr_pending"])) for f, m in _metas(db, task_id) if m.get("ocr_pending")), None)
                    db.commit()
                    if target is None:
                        break
                    wrote = _ocr_file(db, task_id, *target) or wrote
                if wrote:
                    _recheck(db, task_id)
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:a, :b)"), {"a": _OCR_LOCK, "b": key})
                conn.commit()
        finally:
            conn.close()
        # 释放锁与另一次解析写入新页之间的空档：再看一眼，有就接着做
        pending = has_pending(db, task_id)
        db.commit()
        if not pending:
            return
