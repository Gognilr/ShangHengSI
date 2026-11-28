"""会话 CRUD：任务行锁串行化文件修改，审计只写标识和数量。"""
import os
from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.admin.settings_store import get_setting
from app.config import get_settings
from app.db import jobs
from app.db.base import utcnow
from app.db.models import Job, AssistantMessage, LlmModel, Party, RedactionMap, RedactionExclusion, RedactionItem, RedactionSuspect, ReviewRun, Task, TaskFile, User
from app.llm import gateway, usage
from app.models_api import select_model
from app.redaction import engine
from app.redaction.placeholders import NEUTRAL
from app.redaction.restore import check
from app.review.service import MarkOut
from app.security.errors import AppError, NotFoundError
from app.tasks import service as tasks, storage, filecheck
from app.assistant.schemas import HistoryItem, HistoryPage, SessionDetail, SessionFile, SessionItem, SessionPage, SessionPatch


def asr_enabled() -> bool:
    return 'asr' in os.environ.get('COMPOSE_PROFILES', '').split(',')


def mt_enabled() -> bool:
    from app.assistant.local_translation import enabled
    return enabled()


def _files(db: Session, task: Task):
    return list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))


def availability(db: Session, task: Task) -> str | None:
    if db.scalar(select(exists().where(TaskFile.task_id == task.id, TaskFile.original_deleted_at.is_not(None)))):
        return "原件已按保留期限删除，本会话只能查看"
    reasons = {"parse_failed": "有文件解析失败，请移除后继续"}
    if task.status in reasons:
        return reasons[task.status]
    if db.scalar(select(exists().where(AssistantMessage.task_id == task.id, AssistantMessage.role == "assistant",
                                       AssistantMessage.status.in_(("queued", "streaming"))))):
        return "上一条回复尚未完成"
    if review_active(db, task):
        return REVIEW_BUSY
    # 详情尚未指定本次模型，具体用途、结构化能力和配额在发送时检查。
    if not (task.purpose == 'translate' and mt_enabled()) and not db.scalar(select(exists().where(LlmModel.enabled.is_(True), LlmModel.for_assist.is_(True)))):
        return gateway.MESSAGES["NOT_CONFIGURED"]
    return None


REVIEW_BUSY = "审核进行中，完成后再发送"


def review_active(db: Session, task: Task) -> bool:
    """会话发起的审核轮次排队或进行中（V32-A-02）：期间不发送消息、不改材料，外发正文保持一致。"""
    from app.review.service import session_review_active
    return task.purpose == 'chat' and session_review_active(db, task.id)


def _not_reviewing(db: Session, task: Task) -> None:
    if review_active(db, task):
        raise AppError(409, "SESSION_BUSY", REVIEW_BUSY)


def choose_model(db: Session, task: Task, model_id=None, template=None):
    if model_id is None:
        model_id = db.scalar(select(AssistantMessage.model_id).where(AssistantMessage.task_id == task.id,
                             AssistantMessage.role == "assistant", AssistantMessage.model_id.is_not(None))
                             .order_by(AssistantMessage.id.desc()).limit(1))
    return select_model(db, model_id, "assist", structured=template == "compare")


def review_session_clause():
    """审核入口或已有轮次的工作台会话。没有可证入口的旧会话按普通会话保留。"""
    return (Task.kind == "assistant") & (Task.purpose == "chat") & (
        Task.review_intent.is_(True) | exists().where(ReviewRun.task_id == Task.id))


def _run_stats(db: Session, ids):
    if not ids:
        return {}
    return {tid: (count, active) for tid, count, active in db.execute(
        select(ReviewRun.task_id, func.count(), func.bool_or(ReviewRun.status.in_(("queued", "running"))))
        .where(ReviewRun.task_id.in_(ids)).group_by(ReviewRun.task_id))}


def _latest_run_status(db: Session, rows: list[Task]) -> dict:
    """往返线（V36-F-01）：各任务最新一轮的状态。"""
    ids = [t.latest_run_id for t in rows if t.latest_run_id]
    return dict(db.execute(select(ReviewRun.task_id, ReviewRun.status).where(ReviewRun.id.in_(ids))).all()) if ids else {}


def _generating(db: Session, ids) -> set:
    return set(db.scalars(select(AssistantMessage.task_id).where(
        AssistantMessage.task_id.in_(ids), AssistantMessage.role == 'assistant',
        AssistantMessage.status.in_(('queued', 'streaming'))))) if ids else set()


def _meeting_metas(db: Session, rows: list[Task]) -> dict:
    """会议行的说明（时长、发言人数、状态），会话列表与历史共用。"""
    from app.db.models import DocNode
    ids = [t.id for t in rows if t.purpose == 'meeting']
    if not ids:
        return {}
    minuted = set(db.scalars(select(AssistantMessage.task_id).where(
        AssistantMessage.task_id.in_(ids), AssistantMessage.role == 'assistant', AssistantMessage.status == 'done')))
    speakers = dict(db.execute(select(DocNode.task_id, func.count(func.distinct(DocNode.speaker)))
                               .where(DocNode.task_id.in_(ids), DocNode.speaker.is_not(None))
                               .group_by(DocNode.task_id)).all())
    return {t.id: _meeting_meta(_files(db, t), t.id in minuted, speakers.get(t.id, 0)) for t in rows if t.purpose == 'meeting'}


def _is_review(task, count: int) -> bool:
    return task.kind == "assistant" and task.purpose == "chat" and bool(task.review_intent or count)


def _translation_replies():
    return select(AssistantMessage).where(AssistantMessage.role == 'assistant', AssistantMessage.template == 'translate')


def _translation_states(db: Session, tasks: list[Task]) -> dict:
    ids = [task.id for task in tasks if task.purpose == 'translate']
    if not ids:
        return {}
    latest_ids = _translation_replies().with_only_columns(func.max(AssistantMessage.id)).where(
        AssistantMessage.task_id.in_(ids)).group_by(AssistantMessage.task_id)
    rows = db.execute(select(AssistantMessage.task_id, AssistantMessage.status,
                             AssistantMessage.params['engine'].astext.label('engine')).where(
        AssistantMessage.id.in_(latest_ids)))
    return {row.task_id: row for row in rows}


def _translation_state(task: Task, reply) -> dict:
    mode = reply.engine if reply else None
    return {'translation_engine': task.translation_engine or (mode if mode in ('local', 'cloud') else None),
            'translation_status': reply.status if reply else None}


def _check_translation_engine(db, user_id, purpose, value):
    if value is None:
        return
    if purpose != 'translate':
        raise AppError(422, 'INVALID_PURPOSE', '仅翻译会话可选择翻译方式')
    if value == 'cloud':
        from app.security.permissions import user_permissions
        if 'use.translate' not in user_permissions(db, user_id):
            raise AppError(403, 'FORBIDDEN', '没有云端翻译权限，请联系系统管理员')


def _item(task: Task, files: list[TaskFile], *, generating: bool = False, reviewing: bool = False,
          run_count: int = 0, translation_reply=None) -> SessionItem:
    return SessionItem(id=task.id, title=task.title, status=task.status, file_count=len(files),
                       last_activity_at=task.last_activity_at,
                       original_deleted=any(f.original_deleted_at is not None for f in files), generating=generating,
                       purpose=task.purpose, reviewing=reviewing, run_count=run_count, is_review=_is_review(task, run_count),
                       policy=task.policy, **_translation_state(task, translation_reply))


def _meeting_meta(files: list[TaskFile], minuted: bool, speakers: int) -> dict | None:
    """会议列表一行的说明：时长、发言人数（逐字稿里不同的发言人编号数）、状态。不含任何原文。"""
    audio = next((f for f in files if f.media), None)
    if audio is None:
        return None
    stage = audio.media.get('stage')
    state = ('failed' if stage == 'failed' else 'transcribing' if stage != 'done' else
             'proofread' if audio.confirmed_at is None else 'minuted' if minuted else 'confirmed')
    return {'duration_ms': audio.media.get('duration_ms') or 0,
            'speakers': speakers if stage == 'done' else 0, 'state': state}


def detail(db: Session, task: Task) -> SessionDetail:
    from app.assistant.meeting import unpack
    files = _files(db, task)
    # 复用节点和表格统计的既有序列化，不改变审核接口的响应形状。
    preview = tasks.task_detail(db, task)
    confirmed = {f.id: f.confirmed_at for f in files}
    media = {}
    for file in files:
        if file.media is None:
            media[file.id] = None
            continue
        job_id = db.scalar(select(Job.id).where(Job.type == 'transcribe',
                           Job.payload['file_id'].astext == str(file.id), Job.status.in_(('queued', 'running')))
                           .order_by(Job.id.desc()).limit(1))
        ahead = db.scalar(select(func.count()).select_from(Job).where(Job.type == 'transcribe',
                          Job.status.in_(('queued', 'running')), Job.id < job_id)) if job_id else 0
        media[file.id] = {**file.media, 'queue_position': ahead}
    reason = availability(db, task)
    from app.review import drafts
    chains = drafts.chain_info(files) if task.purpose == 'chat' else {}
    rows = {f.id: f for f in files}
    generating = bool(db.scalar(select(exists().where(AssistantMessage.task_id == task.id,
                       AssistantMessage.role == 'assistant', AssistantMessage.status.in_(('queued', 'streaming'))))))
    run_count, reviewing = _run_stats(db, [task.id]).get(task.id, (0, False)) if task.purpose == 'chat' else (0, False)
    translation_reply = _translation_states(db, [task]).get(task.id)
    runs = _review_file_ids(db, task) if task.purpose == 'chat' else []
    return SessionDetail(**_item(task, files, generating=generating, reviewing=reviewing, run_count=run_count,
                                 translation_reply=translation_reply).model_dump(),
                         stance=task.stance, source=task.source,
                         error_message=task.error_message, created_at=task.created_at,
                         files=[SessionFile(**f.model_dump(), confirmed_at=confirmed[f.id], media=media[f.id],
                                            speakers=unpack(rows[f.id].speakers_enc, {}) if media[f.id] is not None else None,
                                            prev_file_id=rows[f.id].prev_file_id if f.id in chains else None,
                                            draft_no=chains[f.id][0] if f.id in chains else None,
                                            in_review=_file_in_use(db, task, f, runs)) for f in preview.files],
                         can_send=reason is None, send_reason=reason,
                         code_count=len(engine.load_redaction_map(db, task.id)), kb_enabled=bool(get_setting(db, 'assistant.kb_enabled')),
                         participants=unpack(task.participants_enc, []), asr_enabled=asr_enabled(), mt_enabled=mt_enabled(),
                         material_version=task.material_version, review_running=reviewing,
                         parse_progress=_parse_progress(db, task),
                         draft_hint=drafts.draft_hint(db, task, files) if task.purpose == 'chat' else None)


def _parse_progress(db: Session, task: Task) -> dict | None:
    """解析中的会话：取正在执行的 parse 任务的阶段上报；还在排队时为空对象（V321-E-06）。"""
    if task.status != 'parsing':
        return None
    row = db.execute(select(Job.status, Job.progress).where(Job.type == 'parse', Job.payload['task_id'].astext == str(task.id),
                     Job.status.in_(('queued', 'running'))).order_by(Job.id.desc()).limit(1)).first()
    return (row.progress if row else None) or {}


def create(db: Session, user: User, ip: str | None, purpose: str = 'chat', *, review_intent: bool = False,
           translation_engine: str | None = None) -> SessionDetail:
    from app.security import permissions
    if review_intent:
        if purpose != "chat":
            raise AppError(422, "INVALID_PURPOSE", "仅文件审核会话可标记为审核入口")
        permissions.ensure(db, user, "use.review")
    elif purpose == 'chat':
        # 工作台：持有提问或审核任一权限即可新建、加文件、确认（V32-A-02）；提问与审核各自在发送时校验
        if not permissions.user_permissions(db, user.id) & {'use.assistant', 'use.review'}:
            raise AppError(403, "FORBIDDEN", "没有此项权限，请联系系统管理员")
    elif purpose != 'translate':  # 翻译页可只用本地翻译，云端发送时再校验
        permissions.ensure(db, user, permissions.PURPOSE_PERM[purpose])
    _check_translation_engine(db, user.id, purpose, translation_engine)
    task = Task(kind="assistant", owner_id=user.id, title="新会话", stance="other", source="incoming",
                policy="ai", status="active", last_activity_at=utcnow(), purpose=purpose, review_intent=review_intent,
                translation_engine=translation_engine)
    db.add(task)
    db.flush()
    if purpose == 'chat':
        # 日期是否平移在会话开始时按全局开关决定，之后固定；问答与审核共用同一坐标（V32-A-02）
        engine.write_full_map(db, task.id, {'@date_shift_days': str(engine.new_shift_days(db))})
    audit.record(db, "assistant_session_create", user_id=user.id, target_type="task", target_id=str(task.id), ip=ip)
    result = detail(db, task)
    db.commit()
    return result


def message_out(db: Session, task: Task, message: AssistantMessage) -> dict:
    from app.assistant.stream import incremental
    from app.assistant import local_translation as mt
    if mt.local(message.template, message.params):
        value = mt.payload(message)
        return dict(id=message.id, role=message.role, content=value.get('content', ''), status=message.status,
                    template=message.template, params=message.params, extras=message.extras,
                    model_id=None, model_label=message.model_label, tokens_in=0, tokens_out=0,
                    error_code=message.error_code, error=error_text(message.error_code), created_at=message.created_at,
                    finding_id=None, marks=[], calcs=[], translation=value.get('translation', {}), translation_notice=mt.NOTICE)
    mapping = _message_mapping(db, task, message)
    dates = engine.load_date_pairs(db, task.id)  # 平移的会话：提问与回复里的日期按还原对显示（V32-A-02）
    content, _ = _incremental(db, task, message, mapping, 0,
                              finished=message.status not in ("queued", "streaming"), dates=dates)
    result = {"id": message.id, "role": message.role, "content": content, "status": message.status,
            "template": message.template, "params": message.params, "extras": message.extras,
            "model_id": message.model_id, "model_label": message.model_label,
            "tokens_in": message.tokens_in, "tokens_out": message.tokens_out,
            "error_code": message.error_code, "error": message.extras.get('discussion_error') or error_text(message.error_code), "created_at": message.created_at,
            "finding_id": str(message.finding_id) if message.finding_id else None, "marks": [], "calcs": []}
    if message.role == "user":
        result["sent"] = message.content_redacted  # 实际发给模型的提问文字（只含代号），供“查看实际发送内容”
    if message.role == "assistant":
        if message.status not in ('queued', 'streaming'):
            # 与 incremental() 用同一份助手代号表，标记位置才与 content 一致（设计 V31-R 4.6）。
            neutral = {code: value for code, value in mapping.items() if NEUTRAL.fullmatch(code)}
            result['marks'] = [MarkOut.model_validate(mark).model_dump()
                               for mark in check(message.content_redacted, neutral, dates=dates,
                                                 locations=_discussion_locations(db, task) if _located(message) else None).marks
                               # 译文行首的 ⟦F1-P1⟧ 是对齐用的分段标记，不算未能还原（V321-Z）
                               if not (message.template == 'translate' and mark.kind == 'note')]
            if _located(message):
                from app.assistant.sources import build
                result['sources'], result['cites'] = build(db, task, message, content, _discussion_locations(db, task))
            if '【算式】' in message.content_redacted:
                from app.calc import add_to_message, code_context
                add_to_message(result, message.content_redacted, neutral, **code_context(db, task, message.content_redacted, neutral))
        from app.redaction.restore import restore
        if message.template == "translate":
            shift = _shift_days(db, task)
            result["translation"] = {loc: dates_unshift(restore(text, mapping), shift)
                                     for loc, text in message.extras.get("translation", {}).items()}
            result["translation_notice"] = ("名称、地址、金额按原文写法还原，请核对"
                                            if message.params.get('engine') == 'cloud' else "请逐段核对译文与原文")
        elif message.template == "crosscheck" and message.role == "assistant" and message.local_result_enc:
            from app.assistant.crosscheck import view
            result["crosscheck"] = view(db, task, message)
        elif message.template == "compare" and message.extras.get("compare"):
            from app.assistant.compare import calculate
            result["compare"] = _decimal_strings(calculate(message.extras["compare"], mapping))
        elif message.template == "reply":
            from app.assistant.templates import draft_only
            result["draft"] = draft_only(content)
    return result


def _discussion_locations(db, task):
    from app.db.models import DocNode
    from app.review.service import _View
    # 列表接口逐条输出消息：同一请求内只算一次（普通问答也换位置后调用变多，V321-E-13）
    cache = db.info.setdefault('assistant_locations', {})
    key = (task.id, task.material_version)
    if key not in cache:
        cache[key] = _View(db, task.id).readable(db.scalars(select(DocNode.loc).where(DocNode.task_id == task.id)))
    return cache[key]


def _incremental(db, task, message, mapping, offset, *, finished, dates):
    import re
    from app.assistant.stream import incremental
    raw = message.content_redacted
    located = _located(message)
    if located and not finished:
        # 位置编号可能拆在任一流式片段处，暂扣末尾候选，避免已显示前缀缩短。
        # 表格位置有多段（F1-T2-R3-C4），每段都可能未写完。
        raw = re.sub(r'\[?F\d*(?:-[A-Z]{0,2}\d*)*$', '', raw)
    if located:
        # 全角括号写法（【F1-P8】）按半角处理，同样换成可读位置（V33-B-02）
        raw = re.sub(r'[【［]\s*(F\d+(?:-[A-Z]{1,2}\d+){1,4})\s*[】］]', r'[\1]', raw)
    text, _ = incremental(raw, mapping, 0, finished=finished, dates=dates)
    if located:
        text = check(text, {}, locations=_discussion_locations(db, task)).text
    return text[offset:], len(text)


def _located(message) -> bool:
    """回复里的段落编号在本机换成可读位置：讨论与普通问答都换（V321-E-13）；翻译、对照表另有结构。"""
    return bool(message.finding_id) or (message.role == 'assistant' and message.template not in ('translate', 'compare', 'crosscheck'))


def _message_mapping(db: Session, task: Task, message: AssistantMessage) -> dict:
    target = message.params.get("target")
    if message.role == "assistant" and message.template == "translate" and isinstance(target, str):
        from app.assistant.translate import target_mapping
        return target_mapping(engine._load_full_map(db, task.id), target)
    return engine.load_redaction_map(db, task.id)


def _shift_days(db: Session, task: Task) -> int:
    return int(engine._load_full_map(db, task.id).get('@date_shift_days') or 0)


def dates_unshift(text: str, days: int) -> str:
    """译文中的日期都来自原文：按平移天数整体移回，换了写法（如中译英）的日期同样正确（V32-A-02）。"""
    if not days:
        return text
    from datetime import timedelta
    from app.redaction import dates
    out, cursor = [], 0
    for hit in dates.find_dates(text):
        out += [text[cursor:hit.start], dates.render(hit.style, hit.value - timedelta(days=days))]
        cursor = hit.end
    return ''.join(out) + text[cursor:]


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
    return {"MT_DISABLED": "本地翻译未启用，请联系管理员", "MT_UNAVAILABLE": "本地翻译暂不可用，请稍后重试", "MT_TIMEOUT": "本段翻译超时，请稍后重试", "MODEL_NOT_ALLOWED": "所选模型不可用于本次操作，请换一个模型或联系管理员",
            "ASSIST_INTERRUPTED": "服务重启，回复中断，请重试", "ASSIST_TOO_LONG": "所选文件过长，请减少使用的文件",
            "ASSIST_QUOTA": "翻译调用次数不足，请明天再试或联系管理员",
            "SESSION_READONLY": "原件已按保留期限删除，本会话只能查看"}.get(code, gateway.MESSAGES.get(code)) if code else None


def owned_message(db: Session, task: Task, mid: int) -> AssistantMessage:
    row = db.get(AssistantMessage, mid)
    if row is None or row.task_id != task.id:
        raise NotFoundError("MESSAGE_NOT_FOUND", "消息不存在")
    return row


def confirm(db: Session, task: Task, user: User, ip: str | None) -> SessionDetail:
    task = confirm_files(db, task, user, ip)
    result = detail(db, task)
    db.commit()
    return result


def confirm_files(db: Session, task: Task, user: User, ip: str | None) -> Task:
    """确认脱敏的全部检查与写入，不提交；供“确认并发起审核”在同一事务里接着发起（V321-E-06）。"""
    from app.redaction.recheck import recheck, recheck_context
    task = lock(db, task)
    _writable(db, task)
    _not_reviewing(db, task)
    if task.status != "awaiting_confirm":
        raise AppError(409, "SESSION_BUSY", "当前会话状态不能确认脱敏")
    db.scalar(select(RedactionMap).where(RedactionMap.task_id == task.id).with_for_update())
    _cover_session_values(db, task, user, ip=ip)
    engine.ensure_default_replacements(db, task)
    count =db.scalar(select(func.count()).select_from(Party).where(Party.task_id == task.id, Party.is_our_party.is_(True))) or 0
    if count > 1:
        raise AppError(409, "OUR_PARTY_REQUIRED", "我方最多只能选择一方")
    def precheck():
        ctx = recheck_context(db, task.id)
        return recheck(engine.build_outbound_text(db, task), real_values=ctx.real_values, terms=ctx.terms,
                       placeholders=ctx.placeholders, kept=ctx.kept)

    reasons = precheck()
    if reasons:  # 先查后重算（V31-U5F-02/03）：过不了复检才按当前规则重算一次再查，正常情况下只扫一遍
        engine.refresh_if_blocked(db, task)
        reasons = precheck()
    if reasons:
        raise AppError(409, "OUTBOUND_BLOCKED", gateway.MESSAGES["BLOCKED"] + f"（{gateway.describe_reasons(reasons)}）")
    now, count = utcnow(), 0
    for file in _files(db, task):
        if file.confirmed_at is None:
            file.confirmed_at = now
            count += 1
    engine.learn_confirmed_names(db, task, user.id)
    from app.redaction.keep_terms import remember
    remember(db, task, user.id)
    task.status, task.last_activity_at = "active", now
    task.material_version = (task.material_version or 0) + 1
    audit.record(db, "assistant_files_confirm", user_id=user.id, target_type="task", target_id=str(task.id),
                 detail={"files": count}, ip=ip)
    db.flush()
    return task


def _validate_input(db: Session, task: Task, body):
    from app.assistant import local_translation as mt
    if mt.local(body.template, body.params):
        return mt.validate(db, task, body)
    if body.template is not None:
        from app.assistant.templates import prepare
        requested = [body.params.get('file_id')] if body.template == 'translate' else body.params.get('file_ids', [])
        if isinstance(requested, list):
            for file in _files(db, task):
                if str(file.id) in requested and file.confirmed_at is None:
                    reason = '录音正在本机转写' if file.media and not file.parser_version else f'{file.original_name} 尚未确认脱敏'
                    raise AppError(422, 'FILE_NOT_CONFIRMED', reason)
        files = {str(f.id): f.seq for f in _files(db, task) if f.confirmed_at is not None and
                 (body.template != 'minutes' or f.ext in filecheck.AUDIO_EXTS and (f.media or {}).get('stage') == 'done')}
        result = prepare(body.template, body.params, files, body.message)
        return result.message, result.params
    allowed = {'omit_finding_advice'} if body.finding_id else {'file_ids'}
    if set(body.params) - allowed or ('omit_finding_advice' in body.params and type(body.params['omit_finding_advice']) is not bool):
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    text = body.message.strip()
    if not 1 <= len(text) <= 4000:
        raise AppError(422, "VALIDATION_ERROR", "请输入 1～4000 字的消息")
    return text, body.params


def _preflight(db: Session, task: Task, template, params, model_id: int, finding_id=None):
    engine.ensure_default_replacements(db, task)
    if finding_id is not None:  # 按问题讨论：上下文放不下时发送前就拒绝（V32-B-02）
        from app.assistant import context
        context.build_for(db, task, finding_id, None, model_id=model_id,
                          omit_advice=params.get('omit_finding_advice', False))
    elif template == "crosscheck":
        from app.assistant.prompt import selected_files
        selected_files(db, task, params)  # 本机核对不受单次外发字数限制；超长时只是不请模型定位
    elif template == "translate":
        from app.assistant.translate import require_quota, task_segments
        require_quota(len(task_segments(db, task, params)), usage.remaining_calls(db, task.owner_id, model_id))
    else:
        from app.assistant.prompt import file_block
        file_block(db, task, params, model_id=model_id)


def _question(db: Session, task: Task, text: str, decisions=None):
    from app.chat.service import _redact, _terms
    from app.redaction.placeholders import NeutralAllocator
    mapping = engine._load_full_map(db, task.id)
    alloc = NeutralAllocator()
    alloc.seed(mapping)
    return _redact(text, mapping, _terms(db), decisions=decisions, allocator=alloc)


def _discussion_check(db, task, finding_id, params, question, model_id):
    from app.assistant import context
    from app.redaction.recheck import recheck, recheck_context
    ctx = recheck_context(db, task.id)
    def reasons(text):
        return recheck(text, real_values=ctx.real_values, terms=ctx.terms,
                       placeholders=ctx.placeholders, kept=ctx.kept)
    hits = reasons(question)
    if hits:
        raise AppError(409, 'DISCUSSION_INPUT_BLOCKED', '你的输入含未脱敏信息：' + gateway.describe_reasons(hits) + '。请返回输入框修改。')
    block, _ = context.build_for(db, task, finding_id, None, model_id=model_id,
                                 omit_advice=params.get('omit_finding_advice', False))
    hits = reasons(block)
    if hits:
        reduced = context.finding_block(db, task, context.owned_finding(db, task, finding_id), omit_advice=True)
        removable = not params.get('omit_finding_advice') and not reasons(reduced)
        raise AppError(409, 'DISCUSSION_ADVICE_BLOCKED' if removable else 'DISCUSSION_CONTEXT_BLOCKED',
                       '问题结论与相关条款含未脱敏信息：' + gateway.describe_reasons(hits)
                       + ('。可不带该问题的结论与建议条文发送。' if removable else '。请核对材料中的脱敏结果。'))


def preview(db: Session, task: Task, body) -> dict:
    from app.assistant.prompt import selected_files
    _writable(db, task)
    if body.finding_id:
        from app.assistant import context
        context.owned_finding(db, task, body.finding_id)
    text, params = _validate_input(db, task, body)
    from app.assistant import local_translation as mt
    if mt.local(body.template, params):
        return {'redacted': text, 'changed': False, 'suspects': []}
    if not body.finding_id:
        selected_files(db, task, params)
    result = _question(db, task, text)
    if body.finding_id:
        _discussion_check(db, task, body.finding_id, params, result.text, body.model_id)
    uncovered = engine.uncovered_session_values(db, task, [item["text"] for item in result.suspects])
    for item in result.suspects:
        item["in_files"] = item["text"] in uncovered
    return {"redacted": result.text, "changed": result.changed, "suspects": result.suspects,
            "material_version": task.material_version}


def _cover_session_values(db: Session, task: Task, user: User,
                          extra: tuple[engine.UserTerm, ...] = (), *, ip: str | None = None) -> None:
    count = engine.cover_session_values(db, task, extra)
    if count:
        audit.record(db, "assistant_session_terms", user_id=user.id, target_type="task",
                     target_id=str(task.id), detail={"count": count}, ip=ip)


def _require_send(db: Session, task: Task, model_id: int):
    _writable(db, task)
    reason = availability(db, task)
    if reason:
        code = next((k for k, value in gateway.MESSAGES.items() if value == reason), "SESSION_BUSY")
        raise AppError(409, code, reason)
    reached = usage.cap_reached(db, task.owner_id, "assist", model_id)
    if reached:
        raise AppError(409, reached, gateway.MESSAGES[reached])
    # 所有会话的发送都先锁 owner，确保每人两个生成名额的检查与写入串行。
    active = db.scalar(select(func.count()).select_from(AssistantMessage).join(Task, Task.id == AssistantMessage.task_id)
                       .where(Task.owner_id == task.owner_id, AssistantMessage.role == "assistant",
                              AssistantMessage.status.in_(("queued", "streaming")))) or 0
    if active >= 2:
        raise AppError(409, "SESSION_BUSY", "你已有两条回复正在生成，请稍后再发送")


def _queue_reply(db: Session, task: Task, user_message: AssistantMessage, model: LlmModel) -> AssistantMessage:
    reply = AssistantMessage(task_id=task.id, role="assistant", status="queued", template=user_message.template,
                             params=user_message.params, extras={"user_message_id": user_message.id},
                             finding_id=user_message.finding_id, model_id=model.id, model_label=model.display_name)
    db.add(reply)
    db.flush()
    jobs.enqueue(db, "assist", {"task_id": str(task.id), "message_id": reply.id})
    task.last_activity_at = utcnow()
    return reply


def send(db: Session, task: Task, user: User, body) -> dict:
    from app.assistant import local_translation as mt
    if mt.local(body.template, body.params):
        return mt.send(db, task, user, body)
    from app.assistant.prompt import selected_files
    from app.security import permissions
    if body.finding_id:
        from app.assistant import context
        if body.template is not None:
            raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
        permissions.ensure(db, user, "use.review")  # 问题来自审核，讨论按审核权限
        context.owned_finding(db, task, body.finding_id)
    else:
        permissions.ensure_cloud_send(db, user, task.purpose, body.template)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    task = lock(db, task)
    tasks.check_material(task, body.expected_material_version)
    model = choose_model(db, task, body.model_id, body.template)
    _require_send(db, task, model.id)
    text, params = _validate_input(db, task, body)
    _preflight(db, task, body.template, params, model.id, body.finding_id)
    db.scalar(select(RedactionMap).where(RedactionMap.task_id == task.id).with_for_update())
    # 先按当前对照表验证疑似项区间；重算文件前固定消息已分配的代号。
    result = _question(db, task, text, decisions=[d.model_dump() for d in body.decisions])
    if body.finding_id:
        _discussion_check(db, task, body.finding_id, params, result.text, model.id)
    chosen = {(d.start, d.end): d.category for d in body.decisions if d.decision == "replace"}
    extra = tuple(engine.UserTerm(s["text"], chosen[s["start"], s["end"]]) for s in result.suspects
                  if (s["start"], s["end"]) in chosen)
    if result.new_entries:
        engine.write_full_map(db, task.id, {**engine._load_full_map(db, task.id), **result.new_entries})
    _cover_session_values(db, task, user, extra)
    if not body.finding_id:
        params = {**params, "file_ids": [str(f.id) for f in selected_files(db, task, params)]}
    if body.template == "translate":
        params["engine"] = "cloud"
    params.update(use_kb=body.use_kb)
    row = AssistantMessage(task_id=task.id, role="user", content_redacted=result.text, status="done",
                           params=params, template=body.template, material_version=task.material_version,
                           finding_id=body.finding_id)
    db.add(row)
    db.flush()
    reply = _queue_reply(db, task, row, model)
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
    from app.assistant import local_translation as mt
    if mt.local(row.template, row.params):
        content = mt.payload(row).get('content', '')
        if offset > len(content):
            raise AppError(422, 'VALIDATION_ERROR', '消息偏移量不正确')
        return dict(status=row.status, text_from_offset=content[offset:], offset=len(content), error=error_text(row.error_code))
    text, new_offset = _incremental(db, task, row, _message_mapping(db, task, row), offset,
                                   finished=row.status not in ("queued", "streaming"),
                                   dates=engine.load_date_pairs(db, task.id))
    if offset > new_offset:
        raise AppError(422, "VALIDATION_ERROR", "消息偏移量不正确")
    result = {"status": row.status, "text_from_offset": text, "offset": new_offset,
              "error": row.extras.get('discussion_error') or error_text(row.error_code)}
    if row.template == "compare" and row.role == "assistant" and row.extras.get("compare"):
        result["compare"] = message_out(db, task, row)["compare"]
    if row.template == "crosscheck" and row.role == "assistant" and row.local_result_enc:
        result["crosscheck"] = message_out(db, task, row)["crosscheck"]
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
    previous = owned_message(db, task, mid)
    if previous.role != "assistant" or previous.status != "failed":
        raise AppError(409, "SESSION_BUSY", "只能重试失败的回复")
    from app.assistant import local_translation as mt
    if mt.local(previous.template, previous.params):
        return mt.retry(db, task, user, owned_message(db, task, int(previous.extras.get('user_message_id', 0))))
    from app.security import permissions
    if previous.finding_id:
        permissions.ensure(db, user, "use.review")
    else:
        permissions.ensure_cloud_send(db, user, task.purpose, previous.template)
    model = select_model(db, previous.model_id, "assist", structured=previous.template == "compare")
    _require_send(db, task, model.id)
    source = owned_message(db, task, int(previous.extras.get("user_message_id", 0)))
    if source.role != "user":
        raise AppError(409, "SESSION_BUSY", "原始消息不存在")
    _preflight(db, task, source.template, source.params, model.id, source.finding_id)
    reply = _queue_reply(db, task, source, model)
    result = {"user": message_out(db, task, source), "assistant": message_out(db, task, reply)}
    db.commit()
    return result


def list_sessions(db: Session, user: User, q: str | None, page: int, purpose: str | None = None) -> SessionPage:
    conditions = [Task.owner_id == user.id, Task.kind == "assistant"]
    if purpose:
        conditions.append(Task.purpose == purpose)
    if q and q.strip():
        conditions.append(Task.title.ilike(tasks._like_pattern(q.strip()), escape="\\"))
    total = db.scalar(select(func.count()).select_from(Task).where(*conditions)) or 0
    rows = db.scalars(select(Task).where(*conditions).order_by(Task.last_activity_at.desc(), Task.id.desc())
                      .offset((page - 1) * 30).limit(30))
    rows = list(rows)
    ids = [t.id for t in rows]
    generating = _generating(db, ids)
    run_stats = _run_stats(db, ids)
    translations = _translation_states(db, rows)
    meetings = _meeting_metas(db, rows)
    run_status = _latest_run_status(db, rows)
    items = []
    for t in rows:
        files = _files(db, t)
        item = _item(t, files, generating=t.id in generating, run_count=run_stats.get(t.id, (0, False))[0],
                     reviewing=t.purpose == 'chat' and run_stats.get(t.id, (0, False))[1], translation_reply=translations.get(t.id))
        item.meeting = meetings.get(t.id)
        item.run_status = run_status.get(t.id)
        items.append(item)
    return SessionPage(items=items, total=total, page=page,
                       kb_enabled=bool(get_setting(db, 'assistant.kb_enabled')), asr_enabled=asr_enabled(), mt_enabled=mt_enabled())


def history(db: Session, user: User, q: str | None, date_from, date_to, page: int, kind: str | None = None) -> HistoryPage:
    """本人的审核、标书和会话按权限合并，按最近活动倒序返回。kind 可筛选审核、标书、对话、会议和翻译。"""
    from datetime import datetime, time, timedelta
    from app.bid import service as bid_service
    from app.db.models import BidItem, BidProject, Finding
    from app.security.permissions import user_permissions
    from app.tasks.schemas import TaskListItem
    size = tasks.PAGE_SIZE
    perms = user_permissions(db, user.id)
    reviewed = review_session_clause()
    chat = (Task.kind == "assistant") & (Task.purpose == "chat")
    types = {"translate": (Task.kind == "assistant") & (Task.purpose == "translate")}
    if "use.review" in perms:
        types["review"] = (Task.kind == "review") | reviewed
    if "use.assistant" in perms:
        types["chat"] = chat & ~reviewed if "use.review" in perms else chat
    if "use.bid" in perms:
        types["bid"] = Task.kind == "bid"
    if "use.meeting" in perms:
        types["meeting"] = (Task.kind == "assistant") & (Task.purpose == "meeting")
    if kind and kind not in types:
        return HistoryPage(items=[], total=0, page=page, page_size=size)
    conds = [Task.owner_id == user.id, types[kind] if kind else or_(*types.values())]
    # 没有发过消息、没有文件的空会话不列
    # 日期筛选按“最近活动”，与列表显示和排序一致（V32-B-01 评审）
    conds.append((Task.kind != "assistant") | (Task.title != "新会话") | exists().where(TaskFile.task_id == Task.id))
    if q and q.strip():
        pattern = tasks._like_pattern(q.strip())
        conds.append(Task.title.ilike(pattern, escape="\\") | exists().where(
            TaskFile.task_id == Task.id, TaskFile.original_name.ilike(pattern, escape="\\")))
    tz = tasks._tz()
    if date_from is not None:
        conds.append(Task.last_activity_at >= datetime.combine(date_from, time.min, tz))
    if date_to is not None:
        conds.append(Task.last_activity_at < datetime.combine(date_to + timedelta(days=1), time.min, tz))
    main_doc_type = select(TaskFile.doc_type).where(TaskFile.task_id == Task.id, TaskFile.is_main).scalar_subquery()
    total = db.scalar(select(func.count()).select_from(Task).where(*conds)) or 0
    rows = db.execute(select(Task, main_doc_type.label("doc_type")).where(*conds)
                      .order_by(Task.last_activity_at.desc(), Task.id.desc()).offset((page - 1) * size).limit(size)).all()
    ids = [t.id for t, _ in rows]
    runs = dict(db.execute(select(ReviewRun.task_id, func.count()).where(ReviewRun.task_id.in_(ids))
                           .group_by(ReviewRun.task_id)).all()) if ids else {}
    files = dict(db.execute(select(TaskFile.task_id, func.count()).where(TaskFile.task_id.in_(ids))
                            .group_by(TaskFile.task_id)).all()) if ids else {}
    # 最新一轮：待处理数、轮次 id、是否进行中（会话行与旧审核任务行都带，行内导出报告用）
    latest: dict = {}
    for run in db.execute(select(ReviewRun.task_id, ReviewRun.id, ReviewRun.status).where(ReviewRun.task_id.in_(ids))
                          .order_by(ReviewRun.run_no.desc())).all() if ids else []:
        latest.setdefault(run.task_id, run)
    pending = dict(db.execute(select(Finding.run_id, func.count()).where(
        Finding.run_id.in_([r.id for r in latest.values()]), Finding.handling_status == 'pending',
        Finding.carry.is_distinct_from('resolved'))
        .group_by(Finding.run_id)).all()) if latest else {}
    bid_ids = [t.id for t, _ in rows if t.kind == "bid"]
    bids = bid_service.history_stats(db, bid_ids) if bid_ids else {}
    translations = _translation_states(db, [t for t, _ in rows])
    generating = _generating(db, [t.id for t, _ in rows if t.kind == "assistant"])
    meetings = _meeting_metas(db, [t for t, _ in rows])
    items = []
    for t, doc_type in rows:
        if t.kind == "bid":
            stage, veto, todo, category, model_state = bids.get(t.id, ("preparing", 0, 0, None, None))
            row = TaskListItem(id=t.id, title=t.title, doc_type=None, stance=t.stance, status=t.status, high_count=veto,
                               created_at=t.created_at, **bid_service.expiry(db, t, stage))
            items.append(HistoryItem(**row.model_dump(), kind="bid", stage=stage, category=category, last_activity_at=t.last_activity_at,
                                     file_count=files.get(t.id, 0), pending_count=todo, reviewing=stage == "checking",
                                     policy=t.policy, model_state=model_state))
            continue
        row = tasks.list_row(db, t, doc_type)
        last = latest.get(t.id)
        items.append(HistoryItem(**row.model_dump(), kind="review" if t.kind == "review" else "session", purpose=t.purpose,
                                 last_activity_at=t.last_activity_at, run_count=runs.get(t.id, 0), file_count=files.get(t.id, 0),
                                 is_review=_is_review(t, runs.get(t.id, 0)),
                                 pending_count=pending.get(last.id, 0) if last else 0,
                                 latest_run_id=last.id if last and last.status in ("completed", "partial") else None,
                                 reviewing=bool(last and last.status in ("queued", "running")),
                                 blocked=bool(last and last.status == "blocked"),
                                 policy=t.policy, run_status=last.status if last else None,
                                 generating=t.id in generating, meeting=meetings.get(t.id),
                                 **_translation_state(t, translations.get(t.id))))
    return HistoryPage(items=items, total=total, page=page, page_size=size)


def waiting(db: Session, user: User) -> dict[str, int]:
    """侧栏菜单右侧的数字（V35-F-02）：只数等本人处理的事，即待确认遮住的会话、旧审核任务与标书项目。
    文件审核入口或已有审核轮次的会话算文件审核，其余对话算工作台。"""
    from app.db.models import BidProject
    reviewed = review_session_clause()
    waiting_task = (Task.owner_id == user.id) & (Task.status == "awaiting_confirm")
    def count(*conds) -> int:
        return db.scalar(select(func.count()).select_from(Task).where(waiting_task, *conds)) or 0
    chat = (Task.kind == "assistant") & (Task.purpose == "chat")
    bid = db.scalar(select(func.count()).select_from(BidProject).join(Task, Task.id == BidProject.task_id).where(
        Task.owner_id == user.id, or_(BidProject.extract_state == "awaiting", BidProject.model_state == "awaiting"))) or 0
    from app.security.permissions import user_permissions
    latest_engine = _translation_replies().with_only_columns(AssistantMessage.params['engine'].astext).where(
        AssistantMessage.task_id == Task.id).order_by(AssistantMessage.id.desc()).limit(1).scalar_subquery()
    translate = (count(Task.kind == "assistant", Task.purpose == "translate",
                       func.coalesce(Task.translation_engine, latest_engine) == 'cloud')
                 if 'use.translate' in user_permissions(db, user.id) else 0)
    return {"workbench": count(chat, ~reviewed), "review": count((Task.kind == "review") | reviewed), "bid": bid,
            "meeting": count(Task.kind == "assistant", Task.purpose == "meeting"),
            "translate": translate}


def my_stats(db: Session, user: User) -> dict[str, int]:
    """工作台信任条（V35-F-03）：本人本月在本机遮住的处数、发送前复检拦下的次数、模型调用次数，以及今天的调用数与每人每日上限。
    只查现有表，不建统计表；遮住数按本月新建的本人任务数遮住项（每处一行），调用数与额度同口径（审核按轮）。"""
    from app.db.models import LlmCall
    start = usage.month_start()
    masked = db.scalar(select(func.count()).select_from(RedactionItem).join(Task, Task.id == RedactionItem.task_id)
                       .where(Task.owner_id == user.id, Task.created_at >= start)) or 0
    blocked = db.scalar(select(func.count()).select_from(LlmCall).where(
        LlmCall.user_id == user.id, LlmCall.status == "blocked", LlmCall.created_at >= start)) or 0
    mine = (LlmCall.user_id == user.id, LlmCall.purpose != "test")
    return {"masked": masked, "blocked": blocked, "calls": usage._quota_calls(db, start, *mine),
            "today_calls": usage._quota_calls(db, usage.day_start(), *mine),
            "daily_cap": int(get_setting(db, "model.user_daily_cap") or 0)}


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
        _not_reviewing(db, task)
        if task.status != "active" or any(f.confirmed_at is not None for f in _files(db, task)):
            raise AppError(409, "SESSION_BUSY", "确认文件后不能修改立场或来源")
    if "translation_engine" in fields:
        _writable(db, task)
        _check_translation_engine(db, task.owner_id, task.purpose, body.translation_engine)
        if db.scalar(select(exists().where(AssistantMessage.task_id == task.id, AssistantMessage.role == 'assistant',
                                           AssistantMessage.status.in_(('queued', 'streaming'))))):
            raise AppError(409, "SESSION_BUSY", "请等本次翻译结束后再切换方式")
    for name in fields:
        setattr(task, name, getattr(body, name).strip())
    db.flush()
    result = detail(db, task)
    db.commit()
    return result


def add_files(db: Session, task: Task, raw: list, marks: list[int] | None = None, draft_of: str | None = None) -> SessionDetail:
    """draft_of：结果页“传新一稿”（V35-D-02），新文件直接认作该文件所在链最新一稿的下一稿，不靠相似度。"""
    task = lock(db, task)
    _writable(db, task)
    _not_reviewing(db, task)
    if task.status not in ("active", "parsing", "awaiting_confirm") or db.scalar(select(exists().where(
            AssistantMessage.task_id == task.id, AssistantMessage.status.in_(("queued", "streaming"))))):
        raise AppError(409, "SESSION_BUSY", "会话正在处理，请稍后再加文件")
    files = _files(db, task)
    if len(files) + len(raw) > tasks.MAX_FILES or len(raw) > 5:
        raise AppError(422, "TOO_MANY_FILES", "每次最多加入 5 个文件，会话合计最多 10 个文件")
    audio_count, uploads = 0, []
    if not raw:
        raise AppError(422, 'NO_FILES', '请至少选择一个文件')
    draft_prev = None
    if draft_of is not None:
        from app.review import drafts
        from app.security.deps import _parse_id
        fid = _parse_id(draft_of)
        base = next((f for f in files if f.id == fid and not f.media), None)
        if base is None or task.purpose != 'chat' or len(raw) != 1:
            raise AppError(422, 'VALIDATION_ERROR', '新一稿只能是本会话里文件审核的一份文件')
        draft_prev = drafts.latest_of(files, base)
    for name, source in raw:
        cleaned = tasks.clean_filename(name)
        ext = cleaned.rsplit('.', 1)[-1].lower()
        if ext not in filecheck.AUDIO_EXTS:
            uploads.append(tasks.inspect_upload(name, source, get_settings().max_upload_mb * 1024 * 1024))
            continue
        if task.purpose != 'meeting':  # V31Q-98：录音只在“会议纪要”中转写（V31-U5-07）
            raise AppError(422, 'AUDIO_MEETING_ONLY', '录音请到“会议纪要”中处理')
        if not asr_enabled():
            raise AppError(422, 'ASR_DISABLED', '录音转写未启用，请联系管理员')
        audio_count += 1
        if audio_count > 1:
            raise AppError(422, 'TOO_MANY_AUDIO', '一次最多加入 1 个录音')
        source.seek(0, 2)
        size = source.tell()
        if size > get_settings().max_audio_mb * 1024 * 1024:
            raise AppError(422, 'FILE_TOO_LARGE', '录音超过大小限制')
        source.seek(0)
        filecheck.check_audio(source.read(12), ext, cleaned)
        source.seek(0)
        uploads.append(tasks.CheckedUpload(name=cleaned, ext=ext, size=size, file=source))
    saved = []
    try:
        rows = []
        # 只允许移除未确认文件；已发送文件仍在会话中，其位置序号保持不变。
        seq = max([task.last_file_seq or 0] + [f.seq for f in files])  # 高水位：移除后再上传不复用序号
        for up in uploads:
            audio = up.ext in filecheck.AUDIO_EXTS
            key, digest = storage.save_audio(up.file) if audio else storage.save_original(up.file.read())
            saved.append(key)
            seq += 1
            row = TaskFile(task_id=task.id, seq=seq, is_main=not files and not rows, original_name=up.name,
                           ext=up.ext, size_bytes=up.size, sha256=digest, storage_key=key,
                           prev_file_id=draft_prev.id if draft_prev is not None else None,
                           media={"stage": "decoding", "progress": 0, "error": None,
                                  **({"marks": marks} if marks else {})} if audio else None)
            db.add(row)
            rows.append(row)
        task.last_file_seq = seq
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


def _review_file_ids(db: Session, task: Task) -> list:
    from app.db.models import ReviewRun
    return db.scalars(select(ReviewRun.file_ids).where(ReviewRun.task_id == task.id)).all()


def _file_in_use(db: Session, task: Task, file: TaskFile, runs: list | None = None) -> bool:
    """被任一审核轮次引用；file_ids 为空的旧轮次视为引用全部文件。"""
    runs = _review_file_ids(db, task) if runs is None else runs
    return any(not ids or str(file.id) in ids for ids in runs)


def remove_file(db: Session, task: Task, file: TaskFile) -> SessionDetail:
    task = lock(db, task)
    _writable(db, task)
    _not_reviewing(db, task)
    if task.status == "parsing":
        raise AppError(409, "SESSION_BUSY", "文件正在解析，请稍后移除")
    db.refresh(file)
    if _file_in_use(db, task, file):
        raise AppError(409, "FILE_IN_USE", "该文件已用于审核，不能移除；可在发送时不选它")
    # V31-U5F-04：已确认的文件也可移除；后续提问不再使用它，已有问答与对照表保留（历史回复仍能还原）。
    prefix = f"F{file.seq}-%"
    from app.db.models import NerNodeResult, TaskClauseVector
    for model in (RedactionItem, RedactionSuspect, RedactionExclusion, NerNodeResult):
        db.execute(delete(model).where(model.task_id == task.id, model.loc.like(prefix)))
    db.execute(delete(TaskClauseVector).where(TaskClauseVector.task_id == task.id, TaskClauseVector.file_seq == file.seq))
    key = file.storage_key
    media_key = file.media_storage_key
    converted_key = file.converted_storage_key  # 旧格式转换结果同样删除（V31Q-114），否则文件行删除后再也找不到它
    db.delete(file)
    db.flush()
    engine.apply_party_changes(db, task, [])
    from app.tasks.pipeline import session_checks
    session_checks(db, task)
    remaining = _files(db, task)
    # 删光文件且没有问答、没有审核：会话回到空白，标题还原为“新会话”，历史记录与近期会话不再列它；再上传时按新文件命名
    if not remaining and not db.scalar(select(exists().where(AssistantMessage.task_id == task.id))) \
            and not db.scalar(select(exists().where(ReviewRun.task_id == task.id))):
        task.title = "新会话"
    if not any(f.confirmed_at is None for f in remaining):
        task.status, task.error_message = "active", None
    elif all(f.parser_version for f in remaining if f.confirmed_at is None):
        task.status, task.error_message = 'awaiting_confirm', None
    task.last_activity_at = utcnow()
    result = detail(db, task)
    storage.delete_original(key)
    storage.delete_pending_audio(file.id)
    if media_key:
        storage.delete_original(media_key)
    if converted_key:
        storage.delete_original(converted_key)
    db.commit()
    return result


def retranscribe(db, task, file):
    task = lock(db, task)
    _writable(db, task)
    db.refresh(file)
    if not file.media or file.media.get('stage') != 'failed' or file.confirmed_at is not None:
        raise AppError(409, 'FILE_NOT_FAILED', '只能重试转写失败的录音')
    if not file.media_storage_key:
        raise AppError(409, 'MEDIA_UNAVAILABLE', '回放副本不可用，请重新加入录音')
    file.media = {**file.media, 'stage': 'queued', 'progress': 0, 'error': None}
    file.parse_error = None
    task.status, task.error_message = 'parsing', None
    jobs.enqueue(db, 'transcribe', {'task_id': str(task.id), 'file_id': str(file.id)})
    db.commit()
    return detail(db, task)
