"""law_import 解析处理函数；预览完成即删除加密临时文件。"""
import logging
import time
import uuid

from sqlalchemy import String, delete, select, text

from app.db.models import Job, Law, LawArticle, LawImport, LawImportFile, LawImportArticle
from app.db import jobs
from app.embedding import engine
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
        db.execute(delete(LawImportArticle).where(LawImportArticle.import_id == row.id))
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
        if row.kind == 'fulltext':
            from app.laws.fulltext import preview as preview_fulltext
            preview_fulltext(db, row, decrypt(files[0].data_enc))
        else:
            diff, unchanged, skipped = preview(db, ((f.seq, decrypt(f.data_enc)) for f in files), row.declared_status)
            row.diff, row.unchanged, row.skipped = diff, unchanged, skipped
            row.added, row.changed = len(diff['added']), len(diff['changed'])
        row.status = 'pending'
    except Exception as exc:
        # 不透出第三方异常文字、文件名或堆栈；失败事务回滚后单独收尾。
        db.rollback()
        row = db.get(LawImport, iid, with_for_update=True)
        if row is None or row.status != 'parsing':
            return
        row.status = 'failed'
        row.error = str(exc) if isinstance(exc, CatalogError) else (
            '全文压缩包解析失败，请检查文件后重新上传' if row.kind == 'fulltext' else
            '目录表格解析失败，请检查文件后重新上传')
    db.execute(delete(LawImportFile).where(LawImportFile.import_id == iid))
    if row.status == 'failed':
        db.execute(delete(LawImportArticle).where(LawImportArticle.import_id == iid))
    db.commit()
    logger.info('法规导入 %s status=%s added=%s changed=%s unchanged=%s skipped=%s 耗时=%sms',
                iid, row.status, row.added, row.changed, row.unchanged, row.skipped,
                int((time.monotonic()-started)*1000))


def enqueue_embed(db):
    db.execute(text('SELECT pg_advisory_xact_lock(7205002)'))
    if not db.scalar(select(Job.id).where(Job.type == 'law_embed', Job.status == 'queued').limit(1)):
        jobs.enqueue(db, 'law_embed', {})


@job_handler('law_embed')
def handle_embed(db, payload):
    # 同时入队或心跳重投只允许一个实际执行者；会话锁跨越每批提交。
    # 锁使用独立连接，避免会话提交后换连接而泄漏会话锁。
    with db.get_bind().connect() as guard:
        # 等待已有执行者后再扫描，不能直接吞掉这次入队：等待期间可能刚确认了新全文。
        guard.execute(text('SELECT pg_advisory_lock(7205003)'))
        try:
            while True:
                rows = db.execute(select(LawArticle, Law.title).join(Law)
                    .where(LawArticle.embedding.is_(None)).order_by(LawArticle.id).limit(64)
                    .with_for_update(of=LawArticle)).all()
                if not rows:
                    db.commit()
                    return
                vectors = engine.embed([f'《{title}》{a.article_no or ""}：{a.text}' for a, title in rows], 'passage')
                for (article, _), vector in zip(rows, vectors):
                    article.embedding = vector
                db.commit()
        finally:
            guard.execute(text('SELECT pg_advisory_unlock(7205003)'))
            guard.commit()
