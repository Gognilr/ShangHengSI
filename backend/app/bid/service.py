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


OCR_RECHECK_FAILED = "扫描页识别后的结论更新失败，请重试更新"

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


def require_unique_name(db, task, name, skip=None):
    """规范化后（去空白、全半角括号、繁简）同名的投标人拒绝，避免同一家建出两张卡（V34-B-01b）。"""
    from app.bid.invitees import key
    k = key(name)
    for row in db.scalars(select(BidBidder).where(BidBidder.task_id == task.id)):
        if row.id != skip and key(crypto.decrypt_text(row.name_enc)) == k:
            raise AppError(422, "BIDDER_DUPLICATE", "已有同名的投标人")


def new_bidder(db, task, name):
    require_unique_name(db, task, name)
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
    from app.bid import invitees
    from app.bid.requirements import load_state
    project = db.get(BidProject, task.id)
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
    state = load_state(project)
    ocr = _ocr(files)
    return {"id": task.id, "title": task.title, "opening_date": project.opening_date, "policy": task.policy,
            "stage": project.stage, "status": task.status, "category_id": project.category_id, "folder_id": project.folder_id, "error_message": task.error_message,
            "last_activity_at": task.last_activity_at, "busy": busy(db, task), "limits": LIMITS,
            "invitees": [e["name"] for e in invitees.view(state)], "sent_count": sent_count(db, task.id),
            "quote_mode": state.get("quote_mode", "fixed"),
            "requirements": {"enabled": db.scalar(select(func.count()).select_from(BidRequirement).where(
                                 BidRequirement.task_id == task.id, BidRequirement.enabled.is_(True))) or 0,
                             "key": db.scalar(select(func.count()).select_from(BidRequirement).where(
                                 BidRequirement.task_id == task.id, BidRequirement.enabled.is_(True),
                                 BidRequirement.group.in_(("veto", "substantive")))) or 0,
                             "extract_state": project.extract_state},
            "check": {"state": project.check_state, "error": project.check_error, "ocr": ocr,
                      "ocr_result": ocr_result(project, ocr, any(f.original_deleted_at is not None for f in files)),
                      "progress": check_progress(project)},
            "model": {"state": project.model_state, "error": project.model_error, **_model_progress(db, task)},
            "bidders": _bidder_views(db, task, files),
            "files": [{"id": f.id, "bidder_id": f.bidder_id, "name": f.original_name, "ext": f.ext,
                       "size_bytes": f.size_bytes, "page_count": f.page_count,
                       "has_password": f.original_deleted_at is None and f.password_enc is not None,
                       "needs_password": f.parse_error == MSG_PASSWORD,
                       "state": "failed" if f.parse_error else "done" if f.parser_version else "waiting",
                       "error": f.parse_error, "warnings": f.warnings,
                       "original_deleted": f.original_deleted_at is not None} for f in files]}


def _bidder_views(db, task, files=()):
    from app.bid.checks import display_short_names, ocr_progress
    rows = list(db.scalars(select(BidBidder).where(BidBidder.task_id == task.id).order_by(BidBidder.seq)))
    names = {b.id: crypto.decrypt_text(b.name_enc) for b in rows}
    shorts = display_short_names(names)

    def ocr(bid):
        done, total = ocr_progress({f.id: f for f in files if f.bidder_id == bid})
        return {"done": done, "total": total}
    # 卡片显示文件数、页数与这一家的扫描页识别进度（V35-F-05）
    return [{"id": b.id, "name": names[b.id], "short": shorts[b.id], "code": b.code, "seq": b.seq,
             "opening_price": _opening_view(b), "pages": sum(f.page_count or 0 for f in files if f.bidder_id == b.id),
             "ocr": ocr(b.id)} for b in rows]


def check_progress(project):
    """本地检查的进度 {done, total}：三步（串标线索、报价与符合项、相似段落）完成几步；检查中才有意义。"""
    from app.bid.checks import STEPS
    done = 0
    if project.check_state == "running" and project.check_enc:
        done = len(json.loads(crypto.decrypt_text(project.check_enc)).get("steps", []))
    elif project.check_state == "done":
        done = len(STEPS)
    return {"done": min(done, len(STEPS)), "total": len(STEPS)}


def _model_progress(db, task):
    from app.bid.model import progress
    return progress(db, task)


def _ocr(files):
    """投标文件扫描页的识别进度 {done, total}（延后识别，V34-B-04）。"""
    from app.bid.checks import ocr_progress
    done, total = ocr_progress({f.id: f for f in files})
    return {"done": done, "total": total}


def ocr_result(project, live, expired, *, summary=None):
    """结果以该次实际纳入的页数为准；实时识别进度只用来判断是否还有工作。"""
    summary = summary if summary is not None else (
        json.loads(crypto.decrypt_text(project.check_enc)) if project.check_enc else {})
    included, version = summary.get("ocr"), summary.get("finished_at")
    state, note = "final", None
    if project.stage != "checked":
        state = None
    elif project.check_error == OCR_RECHECK_FAILED and not expired:
        state, note = "failed", "扫描页识别后的结论更新失败，以下为上一次检查结果。"
    elif included is None:
        state, note = "unknown", "历史结果未记录本次检查纳入的扫描页数，请结合已有说明核对原件。"
    elif expired and included["done"] < included["total"]:
        state, note = "expired", "这是初步结果，部分扫描页未纳入检查，原件已按保留期清理。"
    elif live["done"] < live["total"]:
        state, note = "scanning", f"初步结果已纳入 {included['done']} / {included['total']} 页扫描内容，识别完后自动更新。"
    elif live["done"] > included["done"]:
        state, note = "updating", f"扫描页已识别，正在更新结论。初步结果已纳入 {included['done']} / {included['total']} 页扫描内容。"
    return {"state": state, "included": included, "version": version, "note": note}


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
    if tender and any(u.ext not in ("docx", "doc", "wps", "rtf", "pdf", "xlsx", "xls", "et", "jpg", "png") for u in checked):
        raise AppError(422, "TENDER_FORMAT", "招标文件请上传 Word、PDF、Excel 或图片")
    # 文件夹名只用作建卡建议；重名的现有卡片明确拒绝歧义，避免混装。
    mapping = {}
    saved = []
    try:
        if groups is not None:
            from app.bid.invitees import key
            existing = list(db.scalars(select(BidBidder).where(BidBidder.task_id == task.id)))
            names = list(dict.fromkeys(groups))
            if len({key(n) for n in names}) != len(names):
                raise AppError(422, "BIDDER_DUPLICATE", "分组中有规范化后同名的投标人，请改为不同名称")
            for name in names:
                # 规范化后同名的已有卡片：文件并入该卡（拖入名单中的待交卡片、再次导入同一家文件夹）
                matches = [b for b in existing if key(crypto.decrypt_text(b.name_enc)) == key(name)]
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
    """{任务: (阶段, 本机否决类问题数, 待处理数（V35-E-01 起不再逐条核对，恒为 0）, 分类名)}；列表只做粗略计数。"""
    from app.db.models import BidCategory, BidItem
    stages = dict(db.execute(select(BidProject.task_id, BidProject.stage).where(BidProject.task_id.in_(task_ids))).all())
    names = dict(db.execute(select(BidProject.task_id, BidCategory.name).join(BidCategory, BidCategory.id == BidProject.category_id)
                            .where(BidProject.task_id.in_(task_ids))).all())
    veto = dict(db.execute(select(BidItem.task_id, func.count()).where(
        BidItem.task_id.in_(task_ids), BidItem.kind == "issue", BidItem.level == "veto").group_by(BidItem.task_id)).all())
    return {i: (stages.get(i, "preparing"), veto.get(i, 0), 0, names.get(i)) for i in task_ids}


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
