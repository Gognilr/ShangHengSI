"""逐字稿校对；全部调用方必须已校验会话所有者。"""
import json

from sqlalchemy import delete, select

from app.assistant import homophone
from app.audit import service as audit
from app.db.models import DocNode, NerNodeResult, RedactionItem, RedactionSuspect, RedactionTerm, TaskFile
from app.parsing.model import strip_invisible
from app.redaction import engine
from app.security import crypto
from app.security.errors import AppError


def unpack(blob, default):
    return json.loads(crypto.decrypt_text(blob)) if blob else default


def speaker_values(db, task):
    values = []
    for blob in db.scalars(select(TaskFile.speakers_enc).where(TaskFile.task_id == task.id)):
        values += [v for v in unpack(blob, {}).values() if v]
    return list(dict.fromkeys(values))


def names(db, task):
    return list(dict.fromkeys(unpack(task.participants_enc, []) + speaker_values(db, task)))


def speaker_names(file):
    return unpack(file.speakers_enc, {})


def stamp(ms):
    seconds = (ms or 0) // 1000
    return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'


def line(db, task, file, node, memo=None):
    if node.kind != 'utterance':
        return f'⟦{node.loc}⟧{node.redacted_text or ""}'
    # 长录音逐句调用：对照表与发言人名每次构建只解密一次。
    memo = {} if memo is None else memo
    if file.id not in memo:
        memo[file.id] = speaker_names(file)
    name = memo[file.id].get(str(node.speaker))
    label = f'发言人{node.speaker}'
    if name:
        if 'map' not in memo:
            memo['map'] = engine.load_redaction_map(db, task.id)
        label = next((code for code, value in memo['map'].items() if value == name and code.startswith('[[PER_')), None)
        if label is None:
            raise AppError(409, 'SPEAKER_NOT_REDACTED', '请重新确认发言人的脱敏')
    return f'[{node.loc}] 【{label}｜{stamp(node.t_start_ms)}】{node.redacted_text or ""}'


def editable(db, task, file=None):
    from app.assistant.service import lock, _writable
    task = lock(db, task)
    _writable(db, task)
    if task.status in ('parsing', 'parse_failed'):
        raise AppError(409, 'SESSION_BUSY', '请等待逐字稿转写完成')
    if file is not None:
        db.refresh(file)
        if not file.media or file.media.get('stage') != 'done' or file.confirmed_at:
            raise AppError(409, 'FILE_READ_ONLY', '已确认或未转写完成的逐字稿不能修改')
    elif db.scalar(select(TaskFile.id).where(TaskFile.task_id == task.id, TaskFile.media.is_not(None),
                                            TaskFile.confirmed_at.is_not(None)).limit(1)):
        raise AppError(409, 'FILE_READ_ONLY', '逐字稿已确认，参会人名单不能修改')
    return task


def valid_name(value):
    if not isinstance(value, str) or not 2 <= len(value.strip()) <= 40 or strip_invisible(value) != value:
        raise AppError(422, 'VALIDATION_ERROR', '姓名须为 2～40 字')
    return value.strip()


def recompute(db, task, old_names=()):
    # 移除撤销名单的本会话词条；仍在其他名单或发言人中使用的姓名继续生效。
    removed = set(old_names) - set(names(db, task))
    texts = engine._texts_with_note(engine._body_rows(db, task.id), task)
    for item in db.scalars(select(RedactionItem).where(RedactionItem.task_id == task.id,
                              RedactionItem.detected_by == 'user', RedactionItem.category == 'PERSON')):
        if texts.get(item.loc, '')[item.char_start:item.char_end] in removed:
            db.delete(item)
    db.flush()
    engine.redact_task(db, task, style='neutral', pin=True)


def participants(db, task, values, user, ip):
    task = editable(db, task)
    if not isinstance(values, list) or len(values) > 30:
        raise AppError(422, 'VALIDATION_ERROR', '参会人最多 30 个')
    values = list(dict.fromkeys(valid_name(v) for v in values))
    old = names(db, task)
    task.participants_enc = crypto.encrypt_text(json.dumps(values, ensure_ascii=False))
    recompute(db, task, old)
    audit.record(db, 'meeting_participants', user_id=user.id, target_type='task', target_id=str(task.id), ip=ip)
    db.commit()
    return {'names':values}


def speakers(db, task, file, values, user, ip):
    task = editable(db, task, file)
    if not isinstance(values, dict) or any(not k.isdigit() or not 1 <= int(k) <= 255 or str(int(k)) != k for k in values):
        raise AppError(422, 'VALIDATION_ERROR', '发言人编号不正确')
    old = names(db, task)
    values = {k: valid_name(v) if v is not None else None for k,v in values.items()}
    file.speakers_enc = crypto.encrypt_text(json.dumps(values, ensure_ascii=False))
    recompute(db, task, old)
    audit.record(db, 'meeting_speakers', user_id=user.id, target_type='file', target_id=str(file.id), ip=ip)
    db.commit()
    return values


def change(db, task, file, loc, text=None, speaker=None):
    node = db.scalar(select(DocNode).where(DocNode.file_id == file.id, DocNode.loc == loc, DocNode.kind == 'utterance'))
    if node is None:
        raise AppError(404, 'UTTERANCE_NOT_FOUND', '逐字稿句子不存在')
    if text is not None:
        if not isinstance(text, str) or not 1 <= len(text) <= 2000:
            raise AppError(422, 'VALIDATION_ERROR', '每句须为 1～2000 字')
        text = strip_invisible(text)
        if not text.strip():
            raise AppError(422, 'VALIDATION_ERROR', '句子文字不能为空')
        node.text_enc, node.low_conf_spans = crypto.encrypt_text(text), []
        for model in (RedactionItem, RedactionSuspect, NerNodeResult):
            db.execute(delete(model).where(model.task_id == task.id, model.loc == loc))
    if speaker is not None:
        if type(speaker) is not int or not 1 <= speaker <= 255:
            raise AppError(422, 'VALIDATION_ERROR', '发言人编号不正确')
        node.speaker = speaker
    return node


def edit(db, task, file, loc, text, speaker, user, ip):
    task = editable(db, task, file)
    change(db, task, file, loc, text, speaker)
    recompute(db, task)
    audit.record(db, 'utterance_edit', user_id=user.id, target_type='file', target_id=str(file.id), detail={'loc':loc}, ip=ip)
    db.commit()
    return {'ok':True}


def suggestions(db, task, file):
    candidates = names(db, task) + [crypto.decrypt_text(blob) for blob in db.scalars(select(RedactionTerm.term_enc).where(
        RedactionTerm.created_by == task.owner_id, RedactionTerm.enabled.is_(True), RedactionTerm.kind == 'learned',
        RedactionTerm.category.in_(('PERSON', 'ORG'))))]
    return [{'loc':node.loc, **s} for node in db.scalars(select(DocNode).where(DocNode.file_id == file.id,
            DocNode.kind == 'utterance').order_by(DocNode.ord)) if node.text_enc
            for s in homophone.suggest(crypto.decrypt_text(node.text_enc), candidates)]


def apply(db, task, file, items, user, ip):
    task = editable(db, task, file)
    allowed = {(s['loc'],s['start'],s['end'],s['to']) for s in suggestions(db, task, file)}
    if len(items) > 10000 or any((s.loc,s.start,s.end,s.to) not in allowed for s in items):
        raise AppError(409, 'SUGGESTION_CHANGED', '建议已改变，请刷新后重试')
    grouped = {}
    for item in items:
        grouped.setdefault(item.loc, []).append(item)
    for loc, values in grouped.items():
        node = db.scalar(select(DocNode).where(DocNode.file_id == file.id, DocNode.loc == loc))
        text, previous = crypto.decrypt_text(node.text_enc), len(crypto.decrypt_text(node.text_enc))
        for s in sorted(values, key=lambda s:s.start, reverse=True):
            if s.end > previous:
                raise AppError(422, 'OVERLAPPING_SUGGESTIONS', '建议区间重叠，请逐条采纳')
            text, previous = text[:s.start] + s.to + text[s.end:], s.start
        change(db, task, file, loc, text)
        audit.record(db, 'utterance_edit', user_id=user.id, target_type='file', target_id=str(file.id), detail={'loc':loc}, ip=ip)
    recompute(db, task)
    db.commit()
    return {'ok':True}
