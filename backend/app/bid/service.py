"""标书项目材料管理。所有调用方先校验 use.bid 与 owner；日志只用 id 和数量。"""
import json
import uuid

from sqlalchemy import exists, func, select, update

from app.db import jobs
from app.db.base import utcnow
from app.db.models import BidBidder, BidProject, BidRequirement, Job, Task, TaskFile
from app.parsing.pdf import MSG_PASSWORD
from app.security import crypto
from app.security.errors import AppError, NotFoundError
from app.tasks import service as tasks, storage

# 独立于审核任务的限制；不改变现有合同与会话的上限。
LIMITS = {"bid_files": 20, "tender_files": 10, "file_mb": 100, "pdf_pages": 600, "ocr_pages": 1000}


def busy(db, task):
    return bool(db.scalar(select(Job.id).where(Job.type == "parse", Job.payload["task_id"].astext == str(task.id),
                                             Job.status.in_(("queued", "running"))).limit(1)))


MODEL_JOB_STAGES = ("redact", "extract", "snippets", "model")


def has_pending_model_job(db, task_id):
    return bool(db.scalar(select(Job.id).where(
        Job.type == "bid", Job.payload["task_id"].astext == str(task_id),
        Job.payload["stage"].astext.in_(MODEL_JOB_STAGES), Job.status.in_(("queued", "running")),
    ).limit(1)))


def lock_task(db, task_id):
    """锁任务行并刷新策略；FOR NO KEY UPDATE 与解析节点的外键检查兼容。"""
    return db.get(Task, task_id, populate_existing=True, with_for_update={"key_share": True})


def require_editable(project):
    if project.stage not in ("preparing", "ready"):
        raise AppError(409, "PROJECT_LOCKED", "项目已进入初审，不能再修改材料")


def editable(db, task):
    """只允许在开始初审前修改材料。"""
    task = lock_task(db, task.id)
    if task is None:
        raise NotFoundError("TASK_NOT_FOUND", "项目不存在")
    require_editable(db.get(BidProject, task.id, populate_existing=True))
    return task


def clear_model_work(db, project):
    """失效外发确认和待发送数据，并将模型要求转为人工核对；调用方持任务行锁。"""
    project.extract_state, project.extract_error = None, None
    project.model_state, project.model_error, project.model_enc = None, None, None
    changed = db.execute(update(BidRequirement).where(BidRequirement.task_id == project.task_id,
                                                      BidRequirement.method == "model").values(method="manual"))
    if changed.rowcount:
        project.requirements_version += 1


ORIGINAL_EXPIRED_MSG = "部分或全部原件已按保留期删除，已有初审结果仍可查看"


def originals_expired(db, task_id):
    return db.scalar(select(exists().where(TaskFile.task_id == task_id, TaskFile.original_deleted_at.is_not(None))))


def require_originals(db, task_id):
    if originals_expired(db, task_id):
        raise AppError(409, "ORIGINAL_EXPIRED", ORIGINAL_EXPIRED_MSG)


def bidder(db, task, raw):
    try:
        row = db.get(BidBidder, uuid.UUID(str(raw)))
    except ValueError:
        row = None
    if row is None or row.task_id != task.id:
        raise NotFoundError("BIDDER_NOT_FOUND", "投标人不存在")
    return row


def opening_price(row):
    """唱标价 {amount, upper, note}；未录入为 None。"""
    return json.loads(crypto.decrypt_text(row.opening_price_enc)) if row.opening_price_enc else None


def _opening_view(row):
    from decimal import Decimal
    from app.checks.numbers import money
    value = opening_price(row)
    return value and value | {"text": money(Decimal(value["amount"]))}


def file(db, task, raw):
    try:
        row = db.get(TaskFile, uuid.UUID(str(raw)))
    except ValueError:
        row = None
    if row is None or row.task_id != task.id:
        raise NotFoundError("FILE_NOT_FOUND", "文件不存在")
    return row


def new_bidder(db, task, name):
    seq = (db.scalar(select(func.max(BidBidder.seq)).where(BidBidder.task_id == task.id)) or 0) + 1
    # 按录入顺序分配字母；移除卡片不会重排仍保留的投标人代号。
    code, n = "", seq
    while n:
        n, r = divmod(n - 1, 26)
        code = chr(65 + r) + code
    row = BidBidder(task_id=task.id, seq=seq, code=code, name_enc=crypto.encrypt_text(name))
    db.add(row)
    db.flush()
    return row


def detail(db, task):
    from app.bid.results import sent_count
    project = db.get(BidProject, task.id)
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
    return {"id": task.id, "title": task.title, "opening_date": project.opening_date, "policy": task.policy,
            "stage": project.stage, "status": task.status, "category_id": project.category_id, "folder_id": project.folder_id, "error_message": task.error_message,
            "last_activity_at": task.last_activity_at, "busy": busy(db, task), "limits": LIMITS, "sent_count": sent_count(db, task.id),
            "requirements": {"enabled": db.scalar(select(func.count()).select_from(BidRequirement).where(
                                 BidRequirement.task_id == task.id, BidRequirement.enabled.is_(True))) or 0,
                             "extract_state": project.extract_state},
            "check": {"state": project.check_state, "error": project.check_error, "ocr": _ocr(files)},
            "model": {"state": project.model_state, "error": project.model_error, **_model_progress(db, task)},
            "bidders": [{"id": b.id, "name": crypto.decrypt_text(b.name_enc), "code": b.code, "seq": b.seq,
                         "opening_price": _opening_view(b)}
                        for b in db.scalars(select(BidBidder).where(BidBidder.task_id == task.id).order_by(BidBidder.seq))],
            "files": [{"id": f.id, "bidder_id": f.bidder_id, "name": f.original_name, "ext": f.ext,
                       "size_bytes": f.size_bytes, "page_count": f.page_count,
                       "has_password": f.original_deleted_at is None and f.password_enc is not None,
                       "needs_password": f.parse_error == MSG_PASSWORD,
                       "state": "failed" if f.parse_error else "done" if f.parser_version else "waiting",
                       "error": f.parse_error, "warnings": f.warnings,
                       "original_deleted": f.original_deleted_at is not None} for f in files]}


def _model_progress(db, task):
    from app.bid.model import progress
    return progress(db, task)


def _ocr(files):
    """投标文件扫描页的识别进度 {done, total}（延后识别，V34-B-04）。"""
    from app.bid.checks import ocr_progress
    done, total = ocr_progress({f.id: f for f in files})
    return {"done": done, "total": total}


def queue_parse(db, task):
    """已有排队中的解析时不重复入队：正在运行的解析会接着处理新加入的文件。"""
    task.status, task.error_message, task.last_activity_at = "parsing", None, utcnow()
    queued = db.scalar(select(Job.id).where(Job.type == "parse", Job.payload["task_id"].astext == str(task.id),
                                            Job.status == "queued").limit(1))
    if not queued:
        jobs.enqueue(db, "parse", {"task_id": str(task.id)})


def add_files(db, task, uploads, bidder_id=None, groups=None):
    """同一批文件与自动分组一起提交；失败时清理本批写入的加密原件。"""
    task = editable(db, task)
    if not uploads:
        raise AppError(422, "NO_FILES", "请至少选择一个文件")
    if groups is not None and (len(groups) != len(uploads) or bidder_id is not None):
        raise AppError(422, "INVALID_GROUPS", "文件分组与文件数量不一致")
    tender = bidder_id is None and groups is None
    current = db.scalar(select(func.count()).select_from(TaskFile).where(TaskFile.task_id == task.id,
                        TaskFile.bidder_id.is_(None) if tender else TaskFile.bidder_id.is_not(None))) or 0
    cap = LIMITS["tender_files" if tender else "bid_files"]
    if current + len(uploads) > cap:
        raise AppError(422, "TOO_MANY_FILES", f"招标文件最多 {cap} 份" if tender else f"投标文件合计最多 {cap} 份")
    target = bidder(db, task, bidder_id) if bidder_id is not None else None
    checked = [tasks.inspect_upload(f.filename, f.file, LIMITS["file_mb"] * 1024 * 1024) for f in uploads]
    if tender and any(u.ext not in ("docx", "doc", "wps", "rtf", "pdf") for u in checked):
        raise AppError(422, "TENDER_FORMAT", "招标文件请上传 Word 或 PDF")
    # 文件夹名只用作建卡建议；重名的现有卡片明确拒绝歧义，避免混装。
    mapping = {}
    saved = []
    try:
        if groups is not None:
            existing = list(db.scalars(select(BidBidder).where(BidBidder.task_id == task.id)))
            for name in dict.fromkeys(groups):
                matches = [b for b in existing if crypto.decrypt_text(b.name_enc) == name]
                if len(matches) > 1:
                    raise AppError(422, "AMBIGUOUS_GROUP", "同名投标人有多张卡片，请逐卡上传")
                mapping[name] = matches[0] if matches else new_bidder(db, task, name)
        for index, up in enumerate(checked):
            up.file.seek(0)
            key, digest = storage.save_original(up.file.read())
            saved.append(key)
            task.last_file_seq += 1
            group = mapping[groups[index]] if groups is not None else target
            db.add(TaskFile(task_id=task.id, seq=task.last_file_seq, is_main=False, original_name=up.name,
                            ext=up.ext, size_bytes=up.size, sha256=digest, storage_key=key,
                            bidder_id=group.id if group else None))
        queue_parse(db, task)
        db.commit()
    except BaseException:
        db.rollback()
        for key in saved:
            storage.delete_original(key)
        raise
    return detail(db, task)


# ---------------------------------------------------------------- 历史记录（V34-B-07）

def history_stats(db, task_ids):
    """{任务: (阶段, 疑似否决项数, 待核对的问题与线索数, 分类名)}；已排除的不计，正常重复不计。"""
    from app.db.models import BidCategory, BidItem
    stages = dict(db.execute(select(BidProject.task_id, BidProject.stage).where(BidProject.task_id.in_(task_ids))).all())
    names = dict(db.execute(select(BidProject.task_id, BidCategory.name).join(BidCategory, BidCategory.id == BidProject.category_id)
                            .where(BidProject.task_id.in_(task_ids))).all())
    base = (BidItem.task_id.in_(task_ids), BidItem.review_status != "excluded")
    veto = dict(db.execute(select(BidItem.task_id, func.count()).where(*base, BidItem.kind == "issue", BidItem.level == "veto")
                           .group_by(BidItem.task_id)).all())
    todo = dict(db.execute(select(BidItem.task_id, func.count()).where(
        *base, BidItem.kind != "normal_dup", BidItem.review_status == "pending").group_by(BidItem.task_id)).all())
    return {i: (stages.get(i, "preparing"), veto.get(i, 0), todo.get(i, 0), names.get(i)) for i in task_ids}


def expiry(db, task, stage):
    """保留期显示：准备中 / 待开标按未完成层，已完成初审按结果层与原件层，初审中不删除（与 backup/retention.py 的分层一致）。"""
    from datetime import timedelta
    from app.admin.settings_store import get_setting
    original = result = None
    if stage == "checked":
        result_days = int(get_setting(db, "retention.result_days") or 0)
        original_days = int(get_setting(db, "retention.original_days") or 0)
        if result_days:
            result = task.last_activity_at + timedelta(days=result_days)
        if original_days and db.scalar(select(func.count()).select_from(TaskFile).where(
                TaskFile.task_id == task.id, TaskFile.original_deleted_at.is_(None))):
            original = task.last_activity_at + timedelta(days=original_days)
    elif stage in ("preparing", "ready") and task.status != "parsing":
        days = int(get_setting(db, "retention.unfinished_days") or 0)
        if days:
            result = task.last_activity_at + timedelta(days=days)
    return {"original_expires_at": original, "task_expires_at": result}
