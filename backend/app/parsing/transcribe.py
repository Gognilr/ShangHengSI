"""worker 转写任务；录音、声纹不进入模型网关。"""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.models import DocNode, Task, TaskFile
from app.security import crypto
from app.tasks import storage
from app.worker import job_handler

POLL_SECONDS = 10
UNAVAILABLE = '转写服务暂不可用，可稍后重试'


def alive(spool):
    try:
        return time.time() - (spool / 'transcriber.alive').stat().st_mtime <= 30
    except OSError:
        return False


def finish(db, task_id):
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True))
    if task is None:
        return
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task_id)
                           .execution_options(populate_existing=True)))
    pending = [f for f in files if f.confirmed_at is None]
    if any(f.media and f.media.get('stage') == 'failed' for f in pending):
        task.status = 'parse_failed'
    elif all(f.parser_version for f in pending):
        from app.checks.service import run_table_checks
        from app.redaction.engine import redact_task
        run_table_checks(db, task)
        redact_task(db, task, style='neutral', pin=True)
        task.status, task.error_message = 'awaiting_confirm', None
    db.commit()


def exchange(db, file, pcm):
    spool = Path(get_settings().convert_spool_dir)
    if not alive(spool):
        raise RuntimeError('unavailable')
    # 每次投递使用新 id；重启后的旧结果不会被误采纳。
    token = uuid.uuid4().hex
    base = spool / 'asr'
    for sub in ('in', 'out', 'progress'):
        (base / sub).mkdir(parents=True, exist_ok=True)
    src = base / 'in' / f'{token}.wav'
    req = src.with_suffix('.json')
    result, error = base / 'out' / f'{token}.json', base / 'out' / f'{token}.err'
    progress = base / 'progress' / token
    duration = file.media['duration_ms']
    try:
        os.replace(pcm, src)
        part = req.with_suffix('.part')
        part.write_text(json.dumps({'speakers': file.media.get('speakers')}))
        os.replace(part, req)
        deadline = time.monotonic() + duration / 1000 * 2 + 1800
        while not result.exists() and not error.exists():
            if not alive(spool):
                raise RuntimeError('unavailable')
            if time.monotonic() >= deadline:
                raise RuntimeError('timeout')
            stage, done = 'transcribing', 0
            try:
                raw = progress.read_text().strip()
                if raw == 'waiting_memory':
                    stage = raw
                else:
                    done = max(0, min(duration, int(raw)))
            except (OSError, ValueError):
                pass
            db.refresh(file)
            file.media = {**file.media, 'stage': stage, 'progress': done}
            db.commit()
            time.sleep(POLL_SECONDS)
        if error.exists():
            raise RuntimeError('timeout' if error.read_text().strip() == 'TIMEOUT' else 'transcribe_failed')
        return json.loads(result.read_text(encoding='utf-8'))
    finally:
        for path in (src, req, req.with_suffix('.part'), result, error, progress):
            path.unlink(missing_ok=True)


def save_nodes(db, task, file, result):
    duration = result['duration_ms']
    if type(duration) is not int or not 0 < duration <= 10800000:
        raise ValueError('duration')
    db.execute(delete(DocNode).where(DocNode.file_id == file.id))
    for k, segment in enumerate(sorted(result['segments'], key=lambda s: s['start_ms']), 1):
        start, end, speaker = segment['start_ms'], segment['end_ms'], segment['speaker']
        text, low = segment['text'], segment.get('low', [])
        if (any(type(v) is not int for v in (start, end, speaker)) or
                not 0 <= start < end <= duration or end-start > 30000 or not 1 <= speaker <= 255 or
                not isinstance(text, str) or any(not 0 <= a < b <= len(text) for a, b in low)):
            raise ValueError('segment')
        db.add(DocNode(task_id=task.id, file_id=file.id, loc=f'F{file.seq}-U{k}', ord=k,
                       kind='utterance', part='body', text_enc=crypto.encrypt_text(text),
                       t_start_ms=start, t_end_ms=end, speaker=speaker, low_conf_spans=low))
    manifest = Path(os.environ.get('ASR_MANIFEST_FILE', '/etc/aireview/model-manifests/asr.sha256'))
    file.parser_version = 'asr-firered-' + hashlib.sha256(manifest.read_bytes()).hexdigest()[:8]
    file.doc_type, file.doc_type_source, file.parse_error = 'letter', 'auto', None
    file.media = {**file.media, 'stage': 'done', 'progress': duration, 'error': None}


@job_handler('transcribe')
def handle_transcribe(db, payload):
    task_id, file_id = uuid.UUID(payload['task_id']), uuid.UUID(payload['file_id'])
    task, file = db.get(Task, task_id), db.get(TaskFile, file_id)
    if task is None or file is None or file.task_id != task_id or file.confirmed_at or file.parser_version:
        return
    pcm = Path(get_settings().convert_spool_dir) / 'asr/pending' / f'{file.id.hex}.wav'
    try:
        if not alive(Path(get_settings().convert_spool_dir)):
            raise RuntimeError('unavailable')
        if not pcm.exists():
            from app.parsing.audio import decode
            decode(db, task, file)
        result = exchange(db, file, pcm)
        # 长任务完成后重新取行锁；会话删除或保留期清理后丢弃结果。
        db.expire_all()
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        file = db.get(TaskFile, file_id)
        if task is None or file is None or file.original_deleted_at is not None:
            db.rollback()
            return
        save_nodes(db, task, file, result)
        db.commit()
        finish(db, task_id)
    except Exception as exc:
        db.rollback()
        file, task = db.get(TaskFile, file_id), db.get(Task, task_id)
        if file is not None and task is not None:
            code = str(exc) if isinstance(exc, RuntimeError) and str(exc) in ('unavailable', 'timeout') else 'transcribe_failed'
            file.media = {**(file.media or {}), 'stage': 'failed', 'error': code}
            file.parse_error = UNAVAILABLE if code == 'unavailable' else '录音转写失败，可稍后重试'
            task.status, task.error_message = 'parse_failed', file.parse_error
            db.commit()
        raise
    finally:
        storage.delete_pending_audio(file_id)
