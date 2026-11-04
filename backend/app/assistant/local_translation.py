"""本地翻译：原文只经过内存盘；加密译稿不参加云端历史。"""
import json
import os
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

from sqlalchemy import func, select

from app.config import get_settings
from app.db import jobs
from app.db.base import utcnow
from app.db.models import AssistantMessage, DocNode, Task, TaskFile, User
from app.security import crypto
from app.security.errors import AppError
from app.worker import job_handler

NOTICE = '本地 · 不外发。译稿由本地模型生成，请逐段核对名称、金额和业务条件。'


@contextmanager
def priority():
    marker = Path(get_settings().convert_spool_dir) / 'mt/pause' / uuid.uuid4().hex
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(mode=0o660)
    except OSError:
        pass
    try:
        yield
    finally:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass


def enabled():
    return 'mt' in os.environ.get('COMPOSE_PROFILES', '').split(',')


def local(template, params):
    return template == 'translate' and params.get('engine', 'cloud') == 'local'


def alive():
    try:
        return 0 <= time.time() - (Path(get_settings().convert_spool_dir) / 'translator.alive').stat().st_mtime <= 30
    except OSError:
        return False


def payload(row):
    return json.loads(crypto.decrypt_text(row.local_result_enc)) if row.local_result_enc else {'content': '', 'translation': {}}


def source_file(db, task, params):
    try:
        fid = uuid.UUID(params['file_id'])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AppError(422, 'VALIDATION_ERROR', '所选文件不正确') from None
    file = db.scalar(select(TaskFile).where(TaskFile.id == fid, TaskFile.task_id == task.id))
    if file is None:
        raise AppError(422, 'VALIDATION_ERROR', '所选文件不正确')
    if file.original_deleted_at is not None:
        raise AppError(409, 'SESSION_READONLY', '原件已按保留期限删除，本会话只能查看')
    if not file.parser_version or file.parse_error:
        raise AppError(422, 'FILE_NOT_PARSED', '文件尚未解析完成，请稍后再试')
    return file


def validate(db, task, body):
    from app.assistant.templates import prepare
    if not enabled():
        raise AppError(409, 'MT_DISABLED', '本地翻译未启用，请联系管理员')
    file = source_file(db, task, body.params)
    prepared = prepare('translate', body.params, {str(file.id): file.seq}, body.message)
    return prepared.message, prepared.params


def queue(db, task, source):
    reply = AssistantMessage(task_id=task.id, role='assistant', status='queued', template='translate',
                             params=source.params, model_label='本地 Hy-MT2-7B Q6_K',
                             extras={'user_message_id': source.id})
    db.add(reply)
    db.flush()
    jobs.enqueue(db, 'translate', {'task_id': str(task.id), 'message_id': reply.id})
    task.last_activity_at = utcnow()
    return reply


def _busy(db, task, user):
    if db.scalar(select(AssistantMessage.id).where(AssistantMessage.task_id == task.id,
                                                  AssistantMessage.status.in_(('queued', 'streaming'))).limit(1)):
        raise AppError(409, 'SESSION_BUSY', '上一条回复尚未完成')
    active = db.scalar(select(func.count()).select_from(AssistantMessage).join(Task, Task.id == AssistantMessage.task_id)
                       .where(Task.owner_id == user.id, AssistantMessage.role == 'assistant',
                              AssistantMessage.status.in_(('queued', 'streaming')))) or 0
    if active >= 2:
        raise AppError(409, 'SESSION_BUSY', '你已有两条回复正在生成，请稍后再发送')


def _locked(db, task, user):
    from app.assistant import service
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    task = service.lock(db, task)
    service._writable(db, task)
    _busy(db, task, user)
    return task


def retry(db, task, user, source):
    """失败后重试：沿用原用户消息，只新建一条译稿回复。"""
    from app.assistant import service
    if source.role != 'user' or not local(source.template, source.params):
        raise AppError(409, 'SESSION_BUSY', '原始消息不存在')
    task = _locked(db, task, user)
    if not enabled():
        raise AppError(409, 'MT_DISABLED', '本地翻译未启用，请联系管理员')
    source_file(db, task, source.params)
    if not alive():
        raise AppError(409, 'MT_UNAVAILABLE', '本地翻译暂不可用，请稍后再试')
    reply = queue(db, task, source)
    response = {'user': service.message_out(db, task, source), 'assistant': service.message_out(db, task, reply)}
    db.commit()
    return response


def send(db, task, user, body):
    from app.assistant import service
    task = _locked(db, task, user)
    text, params = validate(db, task, body)
    if not alive():
        raise AppError(409, 'MT_UNAVAILABLE', '本地翻译暂不可用，请稍后再试')
    source = AssistantMessage(task_id=task.id, role='user', status='done', template='translate', params=params,
                              local_result_enc=crypto.encrypt_text(json.dumps({'content': text}, ensure_ascii=False)))
    db.add(source)
    db.flush()
    reply = queue(db, task, source)
    response = {'user': service.message_out(db, task, source), 'assistant': service.message_out(db, task, reply)}
    db.commit()
    return response


def exchange(text, target, cancelled):
    """单段交换；随机标识不含文件名。删除请求即在段落边界取消。"""
    root = Path(get_settings().convert_spool_dir) / 'mt'
    for directory in ('in', 'out'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    request = root / 'in' / (token + '.json')
    result = root / 'out' / (token + '.json')
    error = result.with_suffix('.err')
    part = request.with_suffix('.part')
    try:
        part.write_text(json.dumps({'text': text, 'target': target}, ensure_ascii=False), encoding='utf-8')
        part.replace(request)
        deadline = time.monotonic() + 1800
        while not result.exists():
            if cancelled():
                return None
            if error.exists() or not alive():
                raise AppError(409, 'MT_UNAVAILABLE', '本地翻译暂不可用，请稍后再试')
            if time.monotonic() >= deadline:
                raise AppError(409, 'MT_TIMEOUT', '本段翻译超时，请稍后重试')
            time.sleep(1)
        value = json.loads(result.read_text(encoding='utf-8'))['translation']
        if not isinstance(value, str) or not value.strip():
            raise AppError(409, 'MT_UNAVAILABLE', '本地翻译未返回译文，请稍后再试')
        return value
    finally:
        for path in (request, part, result, error):
            path.unlink(missing_ok=True)


@job_handler('translate')
def handle_translate(db, job):
    from app.assistant import service
    tid, mid = uuid.UUID(job['task_id']), int(job['message_id'])
    task = db.scalar(select(Task).where(Task.id == tid).with_for_update())
    row = db.get(AssistantMessage, mid, with_for_update=True)
    if task is None or row is None or row.task_id != tid or row.status not in ('queued', 'streaming'):
        db.rollback()
        return
    try:
        if row.status == 'streaming':
            raise AppError(409, 'ASSIST_INTERRUPTED', '服务重启，翻译中断，请重试')
        service._writable(db, task)
        source = db.get(AssistantMessage, int(row.extras['user_message_id']))
        if source is None or source.task_id != tid or not local(row.template, row.params):
            raise AppError(409, 'ASSIST_INTERRUPTED', '原始消息不存在')
        file = source_file(db, task, row.params)
        nodes = [(n.loc, crypto.decrypt_text(n.text_enc)) for n in db.scalars(select(DocNode).where(
            DocNode.file_id == file.id, DocNode.part == 'body').order_by(DocNode.ord)) if n.text_enc]
        # 控制单段上下文与输出上限；超长原始段落按 800 字切分，导出时仍归回原节点。
        chunks = [(loc, text[start:start + 800]) for loc, text in nodes for start in range(0, len(text), 800)]
        if not chunks:
            raise AppError(422, 'FILE_NOT_PARSED', '所选文件没有可翻译的正文')
        row.status = 'streaming'
        row.extras = {**row.extras, 'segments_total': len(chunks), 'segments_done': 0}
        target = row.params['target']
        db.commit()

        def cancelled():
            current = db.scalar(select(AssistantMessage).where(AssistantMessage.id == mid)
                                .execution_options(populate_existing=True))
            stop = current is None or current.status != 'streaming' or current.extras.get('stop_requested', False)
            db.rollback()
            return stop

        translations = {}
        for index, (loc, text) in enumerate(chunks, 1):
            if cancelled():
                break
            translated = exchange(text, target, cancelled)
            if translated is None:
                break
            translations[loc] = (translations.get(loc, '') + '\n' + translated).strip()
            row = db.get(AssistantMessage, mid, with_for_update=True)
            if row is None:
                db.rollback()
                return
            row.local_result_enc = crypto.encrypt_text(json.dumps(
                {'content': '\n\n'.join(translations.values()), 'translation': translations}, ensure_ascii=False))
            row.extras = {**row.extras, 'segments_done': index}
            db.commit()
        row = db.get(AssistantMessage, mid, with_for_update=True)
        if row is not None:
            row.status = 'stopped' if row.extras.get('stop_requested') or len(translations) == 0 else 'done'
            db.commit()
    except Exception as exc:
        db.rollback()
        row = db.get(AssistantMessage, mid, with_for_update=True)
        if row is not None:
            row.status = 'failed'
            row.error_code = exc.code if isinstance(exc, AppError) else 'MT_UNAVAILABLE'
            db.commit()
        if not isinstance(exc, AppError):
            raise
