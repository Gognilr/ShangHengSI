"""知识库上传与预览：仅密文持久化，审计只写标识与数量。"""
import hashlib
import uuid

from sqlalchemy import select, text

from app.audit import service as audit
from app.db import jobs
from app.db.models import KbDoc, KbChunk, User
from app.security import crypto
from app.security.errors import AppError, NotFoundError
from app.tasks.service import inspect_upload


def load(db, kid, *, locked=False):
    try:
        kid = uuid.UUID(str(kid))
    except ValueError:
        raise NotFoundError('KB_NOT_FOUND', '知识库文档不存在') from None
    row = db.get(KbDoc, kid, with_for_update=locked)
    if row is None:
        raise NotFoundError('KB_NOT_FOUND', '知识库文档不存在')
    return row


def view(db, row):
    creator = db.get(User, row.created_by)
    return dict(id=row.id, name=crypto.decrypt_text(row.name_enc), status=row.status,
                error_message=row.error_message, enabled=row.enabled, chunk_count=row.chunk_count,
                created_by=row.created_by, uploaded_by=creator.username if creator else '',
                created_at=row.created_at, updated_at=row.updated_at)


def create(db, upload, user):
    checked = inspect_upload(upload.filename, upload.file, 20 * 1024 * 1024)
    if checked.ext not in ('docx', 'pdf', 'xlsx', 'doc', 'wps', 'rtf', 'xls', 'et'):
        raise AppError(422, 'UNSUPPORTED_FILE_TYPE', '知识库支持 docx、pdf、xlsx 及对应旧格式')
    data = checked.file.read()
    digest = hashlib.sha256(data).hexdigest()
    # 序列化重复检查，避免并发上传触发唯一约束后变成内部错误。
    db.execute(text('SELECT pg_advisory_xact_lock(7205005)'))
    if db.scalar(select(KbDoc.id).where(KbDoc.content_hash == digest)):
        raise AppError(409, 'KB_DUPLICATE', '相同内容的知识库文档已存在')
    row = KbDoc(name_enc=crypto.encrypt_text(checked.name), status='parsing', content_hash=digest, created_by=user.id)
    db.add(row); db.flush()
    # 任务卡没有原件表：解析输入仅作为队列密文，解析结束即移除，不落未加密临时文件。
    import base64
    jobs.enqueue(db, 'kb_import', {'doc_id': str(row.id), 'ext': checked.ext,
                                 'data_enc': base64.b64encode(crypto.encrypt(data)).decode('ascii')})
    audit.record(db, 'kb_upload', user_id=user.id, target_type='kb_doc', target_id=str(row.id), detail={'chunks': 0})
    return row


def chunks(db, row):
    items = db.scalars(select(KbChunk).where(KbChunk.doc_id == row.id).order_by(KbChunk.seq).limit(200))
    return {'items': [dict(id=item.id, seq=item.seq,
                           heading=crypto.decrypt_text(item.heading_enc) if item.heading_enc else None,
                           text=crypto.decrypt_text(item.text_enc)) for item in items]}


def update(db, row, values, user):
    if 'enabled' in values:
        row.enabled = values['enabled']
    if 'name' in values:
        row.name_enc = crypto.encrypt_text(values['name'].strip())
    audit.record(db, 'kb_update', user_id=user.id, target_type='kb_doc', target_id=str(row.id),
                 detail={'chunks': row.chunk_count})
    db.flush()


def remove(db, row, user):
    # 删除同时移除未完成队列中的密文输入，避免保留已删除文档的原件。
    for job in db.scalars(select(jobs.Job).where(jobs.Job.type == 'kb_import',
                                                jobs.Job.payload['doc_id'].astext == str(row.id)).with_for_update()):
        job.payload = {'doc_id': str(row.id)}
    audit.record(db, 'kb_delete', user_id=user.id, target_type='kb_doc', target_id=str(row.id),
                 detail={'chunks': row.chunk_count})
    db.delete(row)
