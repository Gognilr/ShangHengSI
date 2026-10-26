"""law_import 解析处理函数；预览完成即删除加密临时文件。"""
import logging
import time
import uuid

from sqlalchemy import String, delete, select

from app.db.models import Job, LawImport, LawImportFile
from app.laws.catalog import CatalogError, preview
from app.security.crypto import decrypt
from app.worker import job_handler, register_periodic

logger = logging.getLogger(__name__)


def finalize_interrupted(db):
    """进程中断且队列已用完重试时收尾，避免永久占住唯一待处理导入。"""
    failed_ids = select(Job.payload['import_id'].astext).where(Job.type == 'law_import', Job.status == 'failed')
    rows = db.scalars(select(LawImport).where(LawImport.status == 'parsing',
                       LawImport.id.cast(String).in_(failed_ids)).with_for_update(skip_locked=True)).all()
    for row in rows:
        row.status, row.error = 'failed', '目录导入中断，请重新上传'
        db.execute(delete(LawImportFile).where(LawImportFile.import_id == row.id))
    db.commit()


register_periodic('law_import_interrupted', finalize_interrupted, 60)


@job_handler('law_import')
def handle_import(db, payload):
    iid = uuid.UUID(payload['import_id'])
    started = time.monotonic()
    row = db.get(LawImport, iid, with_for_update=True)
    if row is None or row.status != 'parsing':
        return  # 心跳重试不重复解析已完成的记录。
    try:
        files = db.scalars(select(LawImportFile).where(LawImportFile.import_id == iid)
                           .order_by(LawImportFile.seq)).all()
        if not files:
            raise CatalogError('导入临时文件不存在，请重新上传')
        diff, unchanged, skipped = preview(db, ((f.seq, decrypt(f.data_enc)) for f in files), row.declared_status)
        row.diff, row.unchanged, row.skipped = diff, unchanged, skipped
        row.added, row.changed, row.status = len(diff['added']), len(diff['changed']), 'pending'
    except Exception as exc:
        # 不透出第三方异常文字、文件名或堆栈；失败事务回滚后单独收尾。
        db.rollback()
        row = db.get(LawImport, iid, with_for_update=True)
        if row is None or row.status != 'parsing':
            return
        row.status = 'failed'
        row.error = str(exc) if isinstance(exc, CatalogError) else '目录表格解析失败，请检查文件后重新上传'
    db.execute(delete(LawImportFile).where(LawImportFile.import_id == iid))
    db.commit()
    logger.info('法规导入 %s status=%s added=%s changed=%s unchanged=%s skipped=%s 耗时=%sms',
                iid, row.status, row.added, row.changed, row.unchanged, row.skipped,
                int((time.monotonic()-started)*1000))
