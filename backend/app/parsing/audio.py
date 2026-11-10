"""录音经 converter 解码；明文仅留在共享内存盘，待转写 PCM 只为即将开始的转写准备。"""
import json
import os
import time
import uuid
from pathlib import Path

from app.config import get_settings
from app.parsing.legacy import _alive, _unlink
from app.parsing.model import ParseError
from app.security import crypto
from app.tasks import storage

MESSAGES = {'TOO_LONG': '录音超过 3 小时', 'NO_AUDIO': '文件中没有音轨',
            'TIMEOUT': '录音解码超时'}


def decode(db, task, file, *, pcm_now: bool = True):
    """解码录音：生成回放副本与时长；pcm_now 时另留下待转写 PCM 并删除录音原件。

    V31Q-114：转写一次一个，3 小时录音的 PCM 约 345 MB。前面已有转写在排队时解析只做回放副本与时长、保留加密原件，
    PCM 等轮到转写时再从原件解码（handle_transcribe），内存盘里不会堆积多份 PCM；原件仍在时总从原件解码，不用有损的回放副本。
    """
    spool = Path(get_settings().convert_spool_dir)
    if not _alive(spool):
        raise ParseError('文件转换服务不可用，请联系管理员')
    token = uuid.uuid4().hex
    src = spool / 'in' / f'{token}.audio'
    part = src.with_suffix('.audio.part')
    outputs = {ext: spool / 'out' / f'{token}.{ext}' for ext in ('wav', 'ogg', 'json', 'err')}
    pcm = spool / 'asr' / 'pending' / f'{file.id.hex}.wav'
    key, committed = None, False
    previous_key = file.media_storage_key
    try:
        with open(part, 'wb') as out:
            if storage._path(file.storage_key).exists():
                for chunk in crypto.decrypt_stream(storage._path(file.storage_key)):
                    out.write(chunk)
            else:
                out.write(storage.load_original(file.media_storage_key))
        os.replace(part, src)
        # 最长录音的解码预算另留排队余量；不占转写并发。
        deadline = time.monotonic() + 1320
        while not outputs['json'].exists() and not outputs['err'].exists():
            if time.monotonic() >= deadline:
                raise ParseError(MESSAGES['TIMEOUT'])
            time.sleep(0.2)
        if outputs['err'].exists():
            code = outputs['err'].read_text().strip()
            raise ParseError(MESSAGES.get(code, '录音解码失败'))
        duration = json.loads(outputs['json'].read_text())['duration_ms']
        if not isinstance(duration, int) or not 0 < duration <= 10800000:
            raise ParseError('录音时长无效')
        # 重转只取 PCM，回放副本保持不变，避免有损副本反复重编码。
        key = previous_key or storage.save_original(outputs['ogg'].read_bytes())[0]
        if pcm_now:
            pcm.parent.mkdir(parents=True, exist_ok=True)
            os.replace(outputs['wav'], pcm)
        file.media_storage_key = key
        marks = (file.media or {}).get('marks')  # 现场录音的打点，保留到转写完成
        file.media = {'duration_ms': duration, 'speakers': None, 'stage': 'queued', 'progress': 0, 'error': None,
                      **({'marks': marks} if marks else {})}
        file.doc_type, file.doc_type_source = 'letter', 'auto'
        db.commit()  # 先持久化回放副本，再删除录音原件；original_deleted_at 留给保留期。
        committed = True
        if not pcm_now:
            return None  # 原件留到轮到转写时再解码
        storage.delete_original(file.storage_key)
        return pcm
    except BaseException:
        if key and key != previous_key and not committed:
            storage.delete_original(key)
        pcm.unlink(missing_ok=True)
        raise
    finally:
        for path in [part, src, *outputs.values()]:
            _unlink(path)
