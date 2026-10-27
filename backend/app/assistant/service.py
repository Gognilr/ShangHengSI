"""会话 CRUD：任务行锁串行化文件修改，审计只写标识和数量。"""
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.admin.settings_store import get_setting
from app.config import get_settings
from app.db import jobs
from app.db.base import utcnow
from app.db.models import AssistantMessage, RedactionExclusion, RedactionItem, RedactionSuspect, Task, TaskFile, User
from app.llm import gateway, usage
from app.redaction import engine
from app.security.errors import AppError
from app.tasks import service as tasks, storage
from app.assistant.schemas import SessionDetail, SessionFile, SessionItem, SessionPage, SessionPatch


def _files(db: Session, task: Task):
    return list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))


def availability(db: Session, task: Task) -> str | None:
    if db.scalar(select(exists().where(TaskFile.task_id == task.id, TaskFile.original_deleted_at.is_not(None)))):
        return "原件已按保留期限删除，本会话只能查看"
    reasons = {"parsing": "文件正在解析，完成后可以继续", "parse_failed": "有文件解析失败，请移除后继续",
               "awaiting_confirm": "请先确认新加入文件的脱敏"}
    if task.status in reasons:
        return reasons[task.status]
    if db.scalar(select(exists().where(AssistantMessage.task_id == task.id, AssistantMessage.role == "assistant",
                                       AssistantMessage.status.in_(("queued", "streaming"))))):
        return "上一条回复尚未完成"
    if not all(str(get_setting(db, key) or "").strip() for key in
               ("model.base_url", "model.api_key_enc", "model.name")):
        return gateway.MESSAGES["NOT_CONFIGURED"]
    reached = usage.cap_reached(db, task.owner_id, "assist")
    return gateway.MESSAGES[reached] if reached else None


def _item(task: Task, files: list[TaskFile]) -> SessionItem:
    return SessionItem(id=task.id, title=task.title, status=task.status, file_count=len(files),
                       last_activity_at=task.last_activity_at,
                       original_deleted=any(f.original_deleted_at is not None for f in files))


def detail(db: Session, task: Task) -> SessionDetail:
    files = _files(db, task)
    # 复用节点和表格统计的既有序列化，不改变审核接口的响应形状。
    preview = tasks.task_detail(db, task)
    confirmed = {f.id: f.confirmed_at for f in files}
    reason = availability(db, task)
    return SessionDetail(**_item(task, files).model_dump(), stance=task.stance, source=task.source, policy=task.policy,
                         error_message=task.error_message, created_at=task.created_at,
                         files=[SessionFile(**f.model_dump(), confirmed_at=confirmed[f.id]) for f in preview.files],
                         can_send=reason is None, send_reason=reason,
                         code_count=len(engine.load_redaction_map(db, task.id)))


def create(db: Session, user: User, ip: str | None) -> SessionDetail:
    task = Task(kind="assistant", owner_id=user.id, title="新会话", stance="other", source="incoming",
                policy="ai", status="active", last_activity_at=utcnow())
    db.add(task)
    db.flush()
    audit.record(db, "assistant_session_create", user_id=user.id, target_type="task", target_id=str(task.id), ip=ip)
    result = detail(db, task)
    db.commit()
    return result


def list_sessions(db: Session, user: User, q: str | None, page: int) -> SessionPage:
    conditions = [Task.owner_id == user.id, Task.kind == "assistant"]
    if q and q.strip():
        conditions.append(Task.title.ilike(tasks._like_pattern(q.strip()), escape="\\"))
    total = db.scalar(select(func.count()).select_from(Task).where(*conditions)) or 0
    rows = db.scalars(select(Task).where(*conditions).order_by(Task.last_activity_at.desc(), Task.id.desc())
                      .offset((page - 1) * 30).limit(30))
    return SessionPage(items=[_item(t, _files(db, t)) for t in rows], total=total, page=page)


def lock(db: Session, task: Task) -> Task:
    return db.execute(select(Task).where(Task.id == task.id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one()


def _writable(db: Session, task: Task) -> None:
    if any(f.original_deleted_at is not None for f in _files(db, task)):
        raise AppError(409, "SESSION_READONLY", "原件已按保留期限删除，本会话只能查看")


def patch(db: Session, task: Task, body: SessionPatch) -> SessionDetail:
    task = lock(db, task)
    fields = body.model_fields_set
    if not fields or any(getattr(body, f) is None for f in fields) or ("title" in fields and not body.title.strip()):
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    if fields & {"stance", "source"}:
        _writable(db, task)
        if task.status != "active" or any(f.confirmed_at is not None for f in _files(db, task)):
            raise AppError(409, "SESSION_BUSY", "确认文件后不能修改立场或来源")
    for name in fields:
        setattr(task, name, getattr(body, name).strip())
    db.flush()
    result = detail(db, task)
    db.commit()
    return result


def add_files(db: Session, task: Task, raw: list) -> SessionDetail:
    task = lock(db, task)
    _writable(db, task)
    if task.status != "active" or db.scalar(select(exists().where(
            AssistantMessage.task_id == task.id, AssistantMessage.status.in_(("queued", "streaming"))))):
        raise AppError(409, "SESSION_BUSY", "会话正在处理，请稍后再加文件")
    files = _files(db, task)
    if len(files) + len(raw) > tasks.MAX_FILES or len(raw) > 5:
        raise AppError(422, "TOO_MANY_FILES", "每次最多加入 5 个文件，会话合计最多 10 个文件")
    uploads = tasks.check_uploads(raw, 0, get_settings().max_upload_mb * 1024 * 1024)
    saved = []
    try:
        rows = []
        # 只允许移除未确认文件；已发送文件仍在会话中，其位置序号保持不变。
        seq = max([f.seq for f in files] + [0])
        for up in uploads:
            data = up.file.read()
            key, digest = storage.save_original(data)
            saved.append(key)
            seq += 1
            row = TaskFile(task_id=task.id, seq=seq, is_main=not files and not rows, original_name=up.name,
                           ext=up.ext, size_bytes=len(data), sha256=digest, storage_key=key)
            db.add(row)
            rows.append(row)
        db.flush()
        if task.title == "新会话":
            task.title = uploads[0].name
        task.status, task.error_message = "parsing", None
        task.last_activity_at = utcnow()
        jobs.enqueue(db, "parse", {"task_id": str(task.id), "file_ids": [str(f.id) for f in rows]})
        result = detail(db, task)
        db.commit()
        return result
    except BaseException:
        db.rollback()
        for key in saved:
            storage.delete_original(key)
        raise


def remove_file(db: Session, task: Task, file: TaskFile) -> SessionDetail:
    task = lock(db, task)
    _writable(db, task)
    if task.status == "parsing":
        raise AppError(409, "SESSION_BUSY", "文件正在解析，请稍后移除")
    db.refresh(file)
    if file.confirmed_at is not None:
        raise AppError(409, "FILE_CONFIRMED", "已确认的文件不能移除")
    prefix = f"F{file.seq}-%"
    for model in (RedactionItem, RedactionSuspect, RedactionExclusion):
        db.execute(delete(model).where(model.task_id == task.id, model.loc.like(prefix)))
    key = file.storage_key
    db.delete(file)
    db.flush()
    engine.apply_party_changes(db, task, [])
    remaining = _files(db, task)
    if not any(f.confirmed_at is None for f in remaining):
        task.status, task.error_message = "active", None
    task.last_activity_at = utcnow()
    result = detail(db, task)
    storage.delete_original(key)
    db.commit()
    return result
