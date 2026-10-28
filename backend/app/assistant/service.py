"""会话 CRUD：任务行锁串行化文件修改，审计只写标识和数量。"""
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.admin.settings_store import get_setting
from app.config import get_settings
from app.db import jobs
from app.db.base import utcnow
from app.db.models import AssistantMessage, Party, RedactionMap, RedactionExclusion, RedactionItem, RedactionSuspect, Task, TaskFile, User
from app.llm import gateway, usage
from app.redaction import engine
from app.security.errors import AppError, NotFoundError
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


def message_out(db: Session, task: Task, message: AssistantMessage) -> dict:
    from app.assistant.stream import incremental
    mapping = _message_mapping(db, task, message)
    content, _ = incremental(message.content_redacted, mapping, 0,
                              finished=message.status not in ("queued", "streaming"))
    result = {"id": message.id, "role": message.role, "content": content, "status": message.status,
            "template": message.template, "params": message.params, "extras": message.extras,
            "tokens_in": message.tokens_in, "tokens_out": message.tokens_out,
            "error_code": message.error_code, "error": error_text(message.error_code), "created_at": message.created_at}
    if message.role == "assistant":
        from app.redaction.restore import restore
        if message.template == "translate":
            result["translation"] = {loc: restore(text, mapping) for loc, text in message.extras.get("translation", {}).items()}
            result["translation_notice"] = "名称、地址、金额按原文写法还原，请核对"
        elif message.template == "compare" and message.extras.get("compare"):
            from app.assistant.compare import calculate
            result["compare"] = _decimal_strings(calculate(message.extras["compare"], mapping))
        elif message.template == "reply":
            from app.assistant.templates import draft_only
            result["draft"] = draft_only(content)
    return result


def _message_mapping(db: Session, task: Task, message: AssistantMessage) -> dict:
    if message.role == "assistant" and message.template == "translate":
        from app.assistant.translate import target_mapping
        return target_mapping(engine._load_full_map(db, task.id), message.params["target"])
    return engine.load_redaction_map(db, task.id)


def _decimal_strings(value):
    from decimal import Decimal
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _decimal_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decimal_strings(item) for item in value]
    return value


def error_text(code: str | None) -> str | None:
    return {"ASSIST_INTERRUPTED": "服务重启，回复中断，请重试", "ASSIST_TOO_LONG": "所选文件过长，请减少使用的文件",
            "ASSIST_QUOTA": "翻译调用次数不足，请明天再试或联系管理员",
            "SESSION_READONLY": "原件已按保留期限删除，本会话只能查看"}.get(code, gateway.MESSAGES.get(code)) if code else None


def owned_message(db: Session, task: Task, mid: int) -> AssistantMessage:
    row = db.get(AssistantMessage, mid)
    if row is None or row.task_id != task.id:
        raise NotFoundError("MESSAGE_NOT_FOUND", "消息不存在")
    return row


def confirm(db: Session, task: Task, user: User, ip: str | None) -> SessionDetail:
    from app.redaction.recheck import recheck, recheck_context
    task = lock(db, task)
    _writable(db, task)
    if task.status != "awaiting_confirm":
        raise AppError(409, "SESSION_BUSY", "当前会话状态不能确认脱敏")
    if db.scalar(select(exists().where(RedactionSuspect.task_id == task.id, RedactionSuspect.decision == "pending"))):
        raise AppError(409, "SUSPECTS_PENDING", "还有疑似遗漏的敏感信息未处理，请先逐项处理")
    count = db.scalar(select(func.count()).select_from(Party).where(Party.task_id == task.id, Party.is_our_party.is_(True))) or 0
    if count > 1:
        raise AppError(409, "OUR_PARTY_REQUIRED", "我方最多只能选择一方")
    ctx = recheck_context(db, task.id)
    reasons = recheck(engine.build_outbound_text(db, task), real_values=ctx.real_values, terms=ctx.terms, placeholders=ctx.placeholders)
    if reasons:
        raise AppError(409, "OUTBOUND_BLOCKED", gateway.MESSAGES["BLOCKED"] + f"（{gateway.format_reasons(reasons)}）")
    now, count = utcnow(), 0
    for file in _files(db, task):
        if file.confirmed_at is None:
            file.confirmed_at = now
            count += 1
    engine.learn_confirmed_names(db, task, user.id)
    task.status, task.last_activity_at = "active", now
    audit.record(db, "assistant_files_confirm", user_id=user.id, target_type="task", target_id=str(task.id),
                 detail={"files": count}, ip=ip)
    db.flush()
    result = detail(db, task)
    db.commit()
    return result


def _validate_input(db: Session, task: Task, body):
    if body.use_kb or body.use_history:
        raise AppError(422, "VALIDATION_ERROR", "参考资料功能尚未接入")
    if body.template is not None:
        from app.assistant.templates import prepare
        files = {str(f.id): f.seq for f in _files(db, task) if f.confirmed_at is not None}
        result = prepare(body.template, body.params, files, body.message)
        return result.message, result.params
    if set(body.params) - {"file_ids"}:
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    text = body.message.strip()
    if not 1 <= len(text) <= 4000:
        raise AppError(422, "VALIDATION_ERROR", "请输入 1～4000 字的消息")
    return text, body.params


def _preflight(db: Session, task: Task, template, params):
    if template == "translate":
        from app.assistant.translate import require_quota, task_segments
        require_quota(len(task_segments(db, task, params)), usage.remaining_calls(db, task.owner_id))
    else:
        from app.assistant.prompt import file_block
        file_block(db, task, params)


def _question(db: Session, task: Task, text: str, decisions=None):
    from app.chat.service import _redact, _terms
    from app.redaction.placeholders import NeutralAllocator
    mapping = engine._load_full_map(db, task.id)
    alloc = NeutralAllocator()
    alloc.seed(mapping)
    return _redact(text, mapping, _terms(db), decisions=decisions, allocator=alloc)


def preview(db: Session, task: Task, body) -> dict:
    from app.assistant.prompt import selected_files
    _writable(db, task)
    text, params = _validate_input(db, task, body)
    selected_files(db, task, params)
    result = _question(db, task, text)
    return {"redacted": result.text, "changed": result.changed, "suspects": result.suspects}


def _require_send(db: Session, task: Task):
    _writable(db, task)
    reason = availability(db, task)
    if reason:
        code = next((k for k, value in gateway.MESSAGES.items() if value == reason), "SESSION_BUSY")
        raise AppError(409, code, reason)
    # 所有会话的发送都先锁 owner，确保每人两个生成名额的检查与写入串行。
    active = db.scalar(select(func.count()).select_from(AssistantMessage).join(Task, Task.id == AssistantMessage.task_id)
                       .where(Task.owner_id == task.owner_id, AssistantMessage.role == "assistant",
                              AssistantMessage.status.in_(("queued", "streaming")))) or 0
    if active >= 2:
        raise AppError(409, "SESSION_BUSY", "你已有两条回复正在生成，请稍后再发送")


def _queue_reply(db: Session, task: Task, user_message: AssistantMessage) -> AssistantMessage:
    reply = AssistantMessage(task_id=task.id, role="assistant", status="queued", template=user_message.template,
                             params=user_message.params, extras={"user_message_id": user_message.id})
    db.add(reply)
    db.flush()
    jobs.enqueue(db, "assist", {"task_id": str(task.id), "message_id": reply.id})
    task.last_activity_at = utcnow()
    return reply


def send(db: Session, task: Task, user: User, body) -> dict:
    from app.assistant.prompt import selected_files
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    task = lock(db, task)
    _require_send(db, task)
    text, params = _validate_input(db, task, body)
    _preflight(db, task, body.template, params)
    db.scalar(select(RedactionMap).where(RedactionMap.task_id == task.id).with_for_update())
    result = _question(db, task, text, decisions=[d.model_dump() for d in body.decisions])
    if result.new_entries:
        engine.write_full_map(db, task.id, {**engine._load_full_map(db, task.id), **result.new_entries})
    params = {**params, "file_ids": [str(f.id) for f in selected_files(db, task, params)]}
    row = AssistantMessage(task_id=task.id, role="user", content_redacted=result.text, status="done",
                           params=params, template=body.template)
    db.add(row)
    db.flush()
    reply = _queue_reply(db, task, row)
    response = {"user": message_out(db, task, row), "assistant": message_out(db, task, reply)}
    db.commit()
    return response


def list_messages(db: Session, task: Task, after_id: int) -> dict:
    rows = db.scalars(select(AssistantMessage).where(AssistantMessage.task_id == task.id, AssistantMessage.id > after_id)
                      .order_by(AssistantMessage.id))
    return {"items": [message_out(db, task, row) for row in rows]}


def get_delta(db: Session, task: Task, mid: int, offset: int) -> dict:
    from app.assistant.stream import incremental
    row = owned_message(db, task, mid)
    text, new_offset = incremental(row.content_redacted, _message_mapping(db, task, row), offset,
                                   finished=row.status not in ("queued", "streaming"))
    if offset > new_offset:
        raise AppError(422, "VALIDATION_ERROR", "消息偏移量不正确")
    result = {"status": row.status, "text_from_offset": text, "offset": new_offset, "error": error_text(row.error_code)}
    if row.template == "compare" and row.role == "assistant" and row.extras.get("compare"):
        result["compare"] = message_out(db, task, row)["compare"]
    return result


def stop(db: Session, task: Task, mid: int) -> dict:
    task = lock(db, task)
    row = owned_message(db, task, mid)
    db.refresh(row, with_for_update=True)
    if row.role != "assistant":
        raise AppError(409, "SESSION_BUSY", "只能停止助手回复")
    if row.status == "queued":
        row.status = "stopped"
    elif row.status == "streaming":
        row.extras = {**row.extras, "stop_requested": True}
    result = message_out(db, task, row)
    db.commit()
    return result


def retry(db: Session, task: Task, user: User, mid: int) -> dict:
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    task = lock(db, task)
    _require_send(db, task)
    previous = owned_message(db, task, mid)
    if previous.role != "assistant" or previous.status != "failed":
        raise AppError(409, "SESSION_BUSY", "只能重试失败的回复")
    source = owned_message(db, task, int(previous.extras.get("user_message_id", 0)))
    if source.role != "user":
        raise AppError(409, "SESSION_BUSY", "原始消息不存在")
    _preflight(db, task, source.template, source.params)
    reply = _queue_reply(db, task, source)
    result = {"user": message_out(db, task, source), "assistant": message_out(db, task, reply)}
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
