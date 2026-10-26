"""维护组同步入队去重与每小时到期检查。"""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.admin.settings_store import get_setting
from app.db import jobs
from app.db.base import utcnow
from app.db.models import Job, LawImport
from app.laws import imports
from app.security.errors import AppError
from app.worker import job_handler, register_periodic


def enqueue(db):
    imports.lock(db)
    if not get_setting(db,'lawsync.enabled'):
        raise AppError(409,'LAWSYNC_DISABLED','请先开启法规库自动更新')
    if db.scalar(select(LawImport.id).where(LawImport.status.in_(['parsing','pending'])).limit(1)):
        raise AppError(409,'LAW_IMPORT_PENDING','有一项法规库更新尚未处理，请先确认或放弃')
    if db.scalar(select(Job.id).where(Job.type=='law_sync',Job.status.in_(['queued','running'])).limit(1)):
        raise AppError(409,'LAWSYNC_RUNNING','法规库自动更新已在队列中')
    return jobs.enqueue(db,'law_sync',{})


def check_schedule(db):
    if not get_setting(db,'lawsync.enabled'):
        return
    last=get_setting(db,'lawsync.last_run_at')
    days=7 if get_setting(db,'lawsync.interval')=='weekly' else 30
    if last and utcnow()-datetime.fromisoformat(last)<timedelta(days=days):
        return
    try:
        enqueue(db)
        db.commit()
    except AppError:
        db.rollback()


@job_handler('law_sync')
def handle_sync(db,payload):
    from app.lawsync.sync import run
    if get_setting(db,'lawsync.enabled'):
        run(db)


register_periodic('law_sync',check_schedule,3600)
