"""标书解析：逐个解析待处理文件，每个文件单独提交，不对整份投标文件脱敏或外发。

待处理 = 尚未解析且没有错误提示的文件。解析期间不锁任务行，用户仍可往项目里加文件、改投标人名称；
新加的文件由同一循环接着解析。全局事务锁保证同一时间只解析一个标书文件（多进程、多线程均适用）。
"""
import json

from sqlalchemy import delete, select, text

from app.bid import fileprops
from app.bid.service import LIMITS
from app.db.base import utcnow
from app.db.models import BidImage, BidProject, DocNode, Task, TaskFile
from app.parsing import pdf
from app.parsing.model import ParseError
from app.security import crypto
from app.tasks import storage

_LOCK = 3401
_OCR_OVER = f"项目扫描页和图片合计超过 {LIMITS['ocr_pages']} 页"


def _pending(task_id):
    return select(TaskFile).where(TaskFile.task_id == task_id, TaskFile.parser_version.is_(None),
                                  TaskFile.parse_error.is_(None)).order_by(TaskFile.seq)


def _ocr_used(db, task_id):
    rows = db.scalars(select(TaskFile.meta_enc).where(TaskFile.task_id == task_id, TaskFile.parser_version.is_not(None),
                                                      TaskFile.meta_enc.is_not(None)))
    return sum(json.loads(crypto.decrypt_text(m)).get("ocr_pages", 0) for m in rows)


def _parse_one(db, file, used):
    """解析一个文件，返回 (节点, parser_version, meta)；meta 含文件属性与图片哈希。meta 与密码一样加密，不进日志或对外提示。"""
    data = storage.load_original(file.storage_key)
    if file.ext == "pdf":
        password = crypto.decrypt_text(file.password_enc) if file.password_enc is not None else ""
        options = {"password": password, "max_pages": LIMITS["pdf_pages"]}
        need = len(pdf.inspect_pdf(data, **options).scanned_pages)
        if used + need > LIMITS["ocr_pages"]:
            raise ParseError(_OCR_OVER)
        if need:
            from app.worker import ocr_slot
            with ocr_slot():
                parsed = pdf.parse_pdf(data, file.seq, **options)
        else:
            parsed = pdf.parse_pdf(data, file.seq, **options)
        return parsed, pdf.PARSER_VERSION, _meta(file, data, parsed, password)
    if file.ext in ("jpg", "png") and used + 1 > LIMITS["ocr_pages"]:
        raise ParseError(_OCR_OVER)
    from app.tasks.pipeline import _parse_bytes
    parsed, version = _parse_bytes(file.ext, data, file.seq, 0)
    return parsed, version, _meta(file, data, parsed)


def _meta(file, data, parsed, password=None):
    """文件属性与图片哈希（B-03 比对用）：属性随 meta 加密，图片只写哈希行。"""
    props, images = fileprops.collect(file.ext, data, parsed.nodes, password)
    return {"ocr_pages": parsed.ocr_pages, "props": props, "images": images}


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
            task.status = "parse_failed" if failed else "draft"
            task.error_message = "部分文件尚未解析，请处理文件行中的提示" if failed else None
            task.last_activity_at = utcnow()
            db.commit()
            return
        db.execute(delete(DocNode).where(DocNode.file_id == file.id))
        db.execute(delete(BidImage).where(BidImage.file_id == file.id))
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
            db.add_all(DocNode(task_id=task_id, file_id=file.id, **{k: v for k, v in vars(n).items() if k not in ("text", "formula")},
                               text_enc=crypto.encrypt_text(n.text), formula_enc=crypto.encrypt_text(n.formula) if n.formula else None)
                       for n in parsed.nodes)
            file.parser_version, file.page_count = version, parsed.page_count
            file.warnings, file.has_revisions = parsed.warnings, parsed.has_revisions
            images = meta.pop("images")
            file.meta_enc = crypto.encrypt_text(json.dumps(meta, ensure_ascii=False, default=str))
            db.add_all(BidImage(file_id=file.id, **image) for image in images)
        db.commit()
