"""任务业务逻辑：上传检查、新建、列表、详情、删除（SPEC 6.2、6.4、7.1、17.6、17.8）。

日志与审计只写数量、策略等非敏感信息，不写文件名和内容。
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import BinaryIO
from zoneinfo import ZoneInfo

from sqlalchemy import delete, distinct, exists, func, select, update
from sqlalchemy.orm import Session as DbSession

from app.audit import service as audit
from app.admin.settings_store import get_setting
from app.config import get_settings
from app.db import jobs
from app.db.base import utcnow
from app.db.models import DocNode, Job, LlmCall, RedactionItem, RedactionSuspect, ReviewRun, Task, TaskFile, User
from app.redaction import engine
from app.security import crypto
from app.security.errors import AppError
from app.tasks import filecheck, storage
from app.tasks.schemas import (
    LatestRun,
    PreviewNode,
    PreviewNodes,
    TaskDetail,
    TaskFileOut,
    TaskListItem,
    TaskPage,
    TaskPatch,
)

MAX_FILES = 10
PAGE_SIZE = 20
MAX_ARCHIVE_BYTES = filecheck.MAX_ARCHIVE_BYTES  # DOCX、XLSX 解压后总大小上限（17.6）
MAX_ARCHIVE_ENTRIES = filecheck.MAX_ARCHIVE_ENTRIES
MAX_NAME_LENGTH = 255

# 扩展名 → 存储用扩展名（jpeg 存为 jpg）
EXT_MAP = {
    "docx": "docx", "pdf": "pdf", "xlsx": "xlsx", "jpg": "jpg", "jpeg": "jpg", "png": "png",
    # 旧格式（SPEC 8.8）：存真实扩展名，解析时由 converter 转为 DOCX / XLSX
    "doc": "doc", "wps": "wps", "rtf": "rtf", "xls": "xls", "et": "et",
}
SUPPORTED_TEXT = "docx、doc、wps、rtf、pdf、xlsx、xls、et、jpg、png"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class CheckedUpload:
    name: str  # 已清理的原文件名（只取最后一段，不超过 255 字符）
    ext: str  # 存储用扩展名
    size: int
    file: BinaryIO


_bad = filecheck.bad


def clean_filename(raw: str | None) -> str:
    """只取最后一段（去掉路径），去掉控制字符，截断到 255 字符（截断时保留扩展名）。"""
    name = _CONTROL_CHARS.sub("", (raw or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if len(name) <= MAX_NAME_LENGTH:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and 0 < len(ext) <= 10:
        return stem[: MAX_NAME_LENGTH - len(ext) - 1] + "." + ext
    return name[:MAX_NAME_LENGTH]


def _inspect_zip(f: BinaryIO, ext: str, name: str) -> None:
    # 上限取本模块的常量（测试中可单独调小）
    filecheck.check_ooxml(f, ext, name, max_entries=MAX_ARCHIVE_ENTRIES, max_bytes=MAX_ARCHIVE_BYTES)


def inspect_upload(raw_name: str | None, f: BinaryIO, max_bytes: int) -> CheckedUpload:
    """对单个文件做 17.6 的全部检查（顺序同设计文档第三节的表），不通过时抛出 422。"""
    name = clean_filename(raw_name)
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > max_bytes:
        raise _bad("FILE_TOO_LARGE", f"文件超过大小限制：{name}")
    ext_raw = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    ext = EXT_MAP.get(ext_raw)
    if ext is None:
        raise _bad("UNSUPPORTED_FILE_TYPE", f"不支持的文件格式：{name}（支持 {SUPPORTED_TEXT}）")
    head = f.read(8)
    f.seek(0)
    if ext == "pdf":
        ok = head.startswith(b"%PDF-")
    elif ext == "jpg":
        ok = head.startswith(b"\xff\xd8\xff")
    elif ext == "png":
        ok = head.startswith(b"\x89PNG")
    elif ext in ("doc", "wps", "xls", "et"):
        ok = True
        filecheck.check_ole(f, ext, name)
        f.seek(0)
    elif ext == "rtf":
        ok = True
        filecheck.check_rtf(f, name)
        f.seek(0)
    else:
        ok = True
        # 加了打开密码的 DOCX / XLSX 实际是 OLE 文件，提示取消密码，而不是“内容不符”（Q-146）
        if filecheck.is_encrypted_ooxml(f):
            raise _bad("FILE_ENCRYPTED", f"文件已加密，请取消密码后重新上传：{name}")
        _inspect_zip(f, ext, name)
        f.seek(0)
    if not ok:
        raise _bad("FILE_CONTENT_MISMATCH", f"文件内容与扩展名不符：{name}")
    return CheckedUpload(name=name, ext=ext, size=size, file=f)


def check_uploads(
    raw_files: list[tuple[str | None, BinaryIO]], main_index: int, max_bytes: int
) -> list[CheckedUpload]:
    """检查数量、主文件序号和每个文件。"""
    if not raw_files:
        raise _bad("NO_FILES", "请至少选择一个文件")
    if len(raw_files) > MAX_FILES:
        raise _bad("TOO_MANY_FILES", f"每个任务最多上传 {MAX_FILES} 个文件")
    if not 0 <= main_index < len(raw_files):
        raise _bad("VALIDATION_ERROR", "主文件序号不正确")
    return [inspect_upload(name, f, max_bytes) for name, f in raw_files]


def create_task(
    db: DbSession,
    user: User,
    uploads: list[CheckedUpload],
    *,
    main_index: int,
    stance: str,
    source: str,
    policy: str,
    user_note: str | None,
    ip: str | None,
) -> Task:
    """加密保存原件，建任务（parsing）和文件记录，入队 parse，记住本次立场与来源。"""
    saved: list[str] = []
    try:
        rows = []
        for seq, up in enumerate(uploads, start=1):
            data = up.file.read()  # 一次只在内存中保留一个文件
            key, sha = storage.save_original(data)
            saved.append(key)
            rows.append(
                TaskFile(
                    seq=seq,
                    is_main=(seq - 1 == main_index),
                    original_name=up.name,
                    ext=up.ext,
                    size_bytes=len(data),
                    sha256=sha,
                    storage_key=key,
                )
            )
        task = Task(
            owner_id=user.id,
            title=uploads[main_index].name,
            stance=stance,
            source=source,
            policy=policy,
            user_note_enc=crypto.encrypt_text(user_note) if user_note else None,
            status="parsing",
            last_activity_at=utcnow(),
        )
        db.add(task)
        db.flush()
        for r in rows:
            r.task_id = task.id
        db.add_all(rows)
        owner = db.merge(user)  # 保证 user 属于当前会话
        owner.last_stance = stance
        owner.last_source = source
        jobs.enqueue(db, "parse", {"task_id": str(task.id)})
        audit.record(
            db,
            "task_create",
            user_id=user.id,
            target_type="task",
            target_id=str(task.id),
            detail={"files": len(rows), "policy": policy},
            ip=ip,
        )
        db.commit()
        return task
    except BaseException:
        db.rollback()
        for key in saved:
            storage.delete_original(key)
        raise


# ---------------------------------------------------------------- 列表


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().tz or "Asia/Shanghai")


def _like_pattern(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_tasks(
    db: DbSession,
    user: User,
    *,
    page: int,
    q: str | None,
    date_from: date | None,
    date_to: date | None,
) -> TaskPage:
    conds = [Task.owner_id == user.id]
    if q and q.strip():
        pattern = _like_pattern(q.strip())
        conds.append(
            exists().where(TaskFile.task_id == Task.id, TaskFile.original_name.ilike(pattern, escape="\\"))
        )
    tz = _tz()
    if date_from is not None:
        conds.append(Task.created_at >= datetime.combine(date_from, time.min, tz))
    if date_to is not None:
        conds.append(Task.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tz))
    main_doc_type = (
        select(TaskFile.doc_type).where(TaskFile.task_id == Task.id, TaskFile.is_main).scalar_subquery()
    )
    total = db.scalar(select(func.count()).select_from(Task).where(*conds)) or 0
    rows = db.execute(
        select(Task, main_doc_type.label("doc_type"))
        .where(*conds)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    items = [
        TaskListItem(
            id=t.id,
            title=t.title,
            doc_type=doc_type,
            stance=t.stance,
            status=t.status,
            high_count=t.high_count,
            created_at=t.created_at,
            **_expiry_fields(db, t),
        )
        for t, doc_type in rows
    ]
    return TaskPage(items=items, total=total, page=page, page_size=PAGE_SIZE)


# ---------------------------------------------------------------- 详情


def _expiry_fields(db: DbSession, task: Task) -> dict:
    original = result = None
    if task.status in ("reviewed", "completed_local"):
        result_days = int(get_setting(db, "retention.result_days") or 0)
        original_days = int(get_setting(db, "retention.original_days") or 0)
        if result_days:
            result = task.last_activity_at + timedelta(days=result_days)
        if original_days and db.scalar(select(exists().where(
                TaskFile.task_id == task.id, TaskFile.original_deleted_at.is_(None)))):
            original = task.last_activity_at + timedelta(days=original_days)
    elif task.status in ("parse_failed", "awaiting_confirm"):
        # 未完成任务按专属保留层删除；解析中、审核中没有删除日期。
        days = int(get_setting(db, "retention.unfinished_days") or 0)
        if days:
            result = task.last_activity_at + timedelta(days=days)
    return {"original_expires_at": original, "task_expires_at": result}


def task_detail(db: DbSession, task: Task) -> TaskDetail:
    files = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)).all()
    paragraphs = dict(
        db.execute(
            select(DocNode.file_id, func.count())
            .where(DocNode.task_id == task.id, DocNode.part == "body", DocNode.kind != "table_cell")
            .group_by(DocNode.file_id)
        ).all()
    )
    # 表格单元格 loc 形如 F1-T2-R3-C4 / F3-PG2-T1-R2-C3：去掉末尾的 -R行-C列 即得表格标识
    table_id = func.regexp_replace(DocNode.loc, "-R[0-9]+-C[0-9]+$", "")
    tables = dict(
        db.execute(
            select(DocNode.file_id, func.count(distinct(table_id)))
            .where(DocNode.task_id == task.id, DocNode.part == "body", DocNode.kind == "table_cell")
            .group_by(DocNode.file_id)
        ).all()
    )
    latest_run = None
    queue_position = None
    if task.latest_run_id is not None:
        run = db.get(ReviewRun, task.latest_run_id)
        if run is not None:
            latest_run = LatestRun(id=run.id, run_no=run.run_no, status=run.status)
            job_id = db.scalar(
                select(Job.id).where(Job.type == "review", Job.payload["run_id"].astext == str(run.id))
            )
            if job_id is not None:
                queue_position = jobs.queue_position(db, job_id)
    return TaskDetail(
        id=task.id,
        title=task.title,
        status=task.status,
        error_message=task.error_message,
        stance=task.stance,
        source=task.source,
        policy=task.policy,
        user_note=crypto.decrypt_text(task.user_note_enc) if task.user_note_enc is not None else None,
        created_at=task.created_at,
        files=[
            TaskFileOut(
                id=f.id,
                seq=f.seq,
                is_main=f.is_main,
                original_name=f.original_name,
                ext=f.ext,
                size_bytes=f.size_bytes,
                doc_type=f.doc_type,
                doc_type_source=f.doc_type_source,
                page_count=f.page_count,
                paragraph_count=paragraphs.get(f.id, 0),
                table_count=tables.get(f.id, 0),
                has_revisions=f.has_revisions,
                warnings=[str(w) for w in (f.warnings or [])],
                original_deleted_at=f.original_deleted_at,
            )
            for f in files
        ],
        latest_run=latest_run,
        queue_position=queue_position,
        **_expiry_fields(db, task),
    )


# ---------------------------------------------------------------- 重新审核（M5-04）


def patch_task(db: DbSession, task: Task, patch: TaskPatch) -> TaskDetail:
    """PATCH /tasks/{id}：修改立场、来源、本次补充说明后整体重算，任务回到 awaiting_confirm（SPEC 7.4）。

    不重新解析，不删旧轮次，latest_run_id 与 high_count 不变；不写审计。我方选择、国家类型修改、用户词条、
    “忽略”决定在重算中保留。
    """
    fields = patch.model_fields_set
    if not fields & {"stance", "source", "user_note"} or ("stance" in fields and patch.stance is None) or (
            "source" in fields and patch.source is None):
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    # 与确认发送共用任务行锁，等待锁后刷新状态
    task = db.execute(select(Task).where(Task.id == task.id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one()
    if task.policy == "local_only":
        raise AppError(409, "LOCAL_ONLY", "该任务的外发策略为不外发，不能提交 AI 审核")
    # 原件已按保留期限删除后不能重新审核（SPEC 16.4，M7-06）
    if db.scalar(select(exists().where(TaskFile.task_id == task.id, TaskFile.original_deleted_at.is_not(None)))):
        raise AppError(409, "ORIGINAL_DELETED", "原件已按保留期限删除，不能重新审核")
    if task.status not in ("reviewed", "awaiting_confirm"):
        raise AppError(409, "TASK_STATE_CONFLICT", "当前状态不能重新审核")
    if "stance" in fields:
        task.stance = patch.stance
    if "source" in fields:
        task.source = patch.source
    if "user_note" in fields:
        note = (patch.user_note or "").strip() or None
        previous = crypto.decrypt_text(task.user_note_enc) if task.user_note_enc is not None else None
        if note != previous:
            # NOTE 的字符位置属于旧原文，不能将旧词条或忽略决定套用到新文字（Q-153）。
            db.execute(delete(RedactionItem).where(RedactionItem.task_id == task.id, RedactionItem.loc == "NOTE",
                                                   RedactionItem.detected_by == "user"))
            db.execute(delete(RedactionSuspect).where(RedactionSuspect.task_id == task.id, RedactionSuspect.loc == "NOTE"))
            # V2Q-35：只清除旧补充说明来源的撤销记录，正文来源及未改说明时的记录保留。
            from app.db.models import RedactionExclusion
            db.execute(delete(RedactionExclusion).where(RedactionExclusion.task_id == task.id,
                                                        RedactionExclusion.loc == "NOTE"))
            task.user_note_enc = crypto.encrypt_text(note) if note else None
            task.user_note_redacted = None
    engine.apply_party_changes(db, task, [])
    task.status = "awaiting_confirm"
    task.last_activity_at = utcnow()
    db.flush()
    result = task_detail(db, task)
    db.commit()
    return result


# ---------------------------------------------------------------- 预览（M3-06）

RAW_MEDIA_TYPES = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png"}


def preview_nodes(db: DbSession, task: Task, file: TaskFile) -> PreviewNodes:
    """该文件全部 part 的节点，按 ord 排序，原文解密后返回。

    解析中或解析失败时返回空列表。保留期清理后 text_enc 为空：text 为 None，text_available=false。
    """
    if task.status in ("parsing", "parse_failed"):
        return PreviewNodes(text_available=True, items=[])
    rows = db.scalars(select(DocNode).where(DocNode.file_id == file.id).order_by(DocNode.ord)).all()
    items = [
        PreviewNode(
            loc=r.loc,
            kind=r.kind,
            part=r.part,
            text=crypto.decrypt_text(r.text_enc) if r.text_enc is not None else None,
            clause_no=r.clause_no,
            page=r.page,
            bbox=[float(v) for v in r.bbox] if r.bbox else None,
            sheet=r.sheet,
            cell_ref=r.cell_ref,
            ocr_conf=r.ocr_conf,
            parent_loc=r.parent_loc,
        )
        for r in rows
    ]
    available = file.original_deleted_at is None and all(i.text is not None for i in items)
    return PreviewNodes(text_available=available, items=items)


def load_raw(file: TaskFile) -> tuple[bytes, str]:
    """PDF / 图片原件的明文字节和媒体类型（Q-63）。"""
    media_type = RAW_MEDIA_TYPES.get(file.ext)
    if media_type is None:
        raise AppError(404, "RAW_NOT_AVAILABLE", "该格式不提供原件预览")
    if file.original_deleted_at is not None:
        raise AppError(404, "ORIGINAL_DELETED", "原件已按保留期限删除")
    try:
        return storage.load_original(file.storage_key), media_type
    except FileNotFoundError:
        raise AppError(404, "ORIGINAL_DELETED", "原件已按保留期限删除") from None


# ---------------------------------------------------------------- 删除


def delete_task(db: DbSession, task: Task, user: User | None, ip: str | None) -> None:
    """彻底删除（17.8）：先删原件文件，再删任务行（子表级联删除；llm_calls 无外键，记录保留）。"""
    keys = db.scalars(select(TaskFile.storage_key).where(TaskFile.task_id == task.id)).all()
    for key in keys:
        storage.delete_original(key)
    # 还在排队的 parse / review 任务没有意义了；正在运行的由处理函数发现任务已删除后自行结束
    db.execute(delete(Job).where(Job.status == "queued", Job.payload["task_id"].astext == str(task.id)))
    db.execute(update(LlmCall).where(LlmCall.task_id == task.id).values(task_title=None))
    audit.record(
        db,
        "task_delete",
        user_id=user.id if user else None,
        target_type="task",
        target_id=str(task.id),
        detail={"files": len(keys)},
        ip=ip,
    )
    db.delete(task)
    db.commit()
