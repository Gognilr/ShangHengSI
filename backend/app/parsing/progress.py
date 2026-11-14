"""解析阶段上报（V321-E-06）：写入正在执行的 parse 任务的 jobs.progress，供会话详情的 parse_progress 读取。

只记文件 id、阶段与页数，不含文件名和正文。解析主事务锁着任务行，所以这里用独立的短会话提交；
上报失败只丢进度，不影响解析。同一文件同一阶段每秒最多写一次。
"""
import logging
import threading
import time
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import Job

log = logging.getLogger(__name__)
_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {}
_last: dict[str, tuple[str, str, float]] = {}


def _write(db: Session, task_id: uuid.UUID, data: dict[str, Any]) -> None:
    bind = db.get_bind()
    engine = getattr(bind, 'engine', bind)
    try:
        with Session(engine) as own:
            own.execute(update(Job).where(Job.type == 'parse', Job.status == 'running',
                                          Job.payload['task_id'].astext == str(task_id)).values(progress=data))
            own.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning('解析进度上报失败: %s', type(exc).__name__)


def start(db: Session, task_id: uuid.UUID, file_ids: list[uuid.UUID]) -> None:
    key = str(task_id)
    with _lock:
        _state[key] = {'phase': 'files', 'files': {str(fid): {'stage': 'waiting', 'done': None, 'total': None} for fid in file_ids}}
        _last.pop(key, None)
        data = _state[key]
    _write(db, task_id, data)


def file(db: Session, task_id: uuid.UUID, file_id: uuid.UUID, stage: str, done: int | None = None, total: int | None = None) -> None:
    key, fid = str(task_id), str(file_id)
    now = time.monotonic()
    with _lock:
        state = _state.get(key)
        if state is None:
            return
        last = _last.get(key)
        final = stage in ('done', 'failed') or (done is not None and total is not None and done >= total)
        if last and last[0] == fid and last[1] == stage and not final and now - last[2] < 1:
            return
        _last[key] = (fid, stage, now)
        state['files'][fid] = {'stage': stage, 'done': done, 'total': total}
        data = {'phase': state['phase'], 'files': dict(state['files'])}
    _write(db, task_id, data)


def phase(db: Session, task_id: uuid.UUID, name: str) -> None:
    key = str(task_id)
    with _lock:
        state = _state.get(key)
        if state is None:
            return
        state['phase'] = name
        data = {'phase': name, 'files': dict(state['files'])}
    _write(db, task_id, data)


def finish(task_id: uuid.UUID) -> None:
    with _lock:
        _state.pop(str(task_id), None)
        _last.pop(str(task_id), None)
