"""导入事务：串行上传、差异过期检查与原子确认。"""
from datetime import date

from sqlalchemy import select, text

from app.db.base import utcnow
from app.db.models import Law, LawImport
from app.security.errors import AppError, NotFoundError
from app.laws.catalog import iso

LOCK_KEY = 7_205_001


def lock(db):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK_KEY})


def load(db, iid, *, locked=False):
    row = db.get(LawImport, iid, with_for_update=locked)
    if row is None:
        raise NotFoundError('LAW_IMPORT_NOT_FOUND', '法规导入记录不存在')
    return row


def _stale():
    raise AppError(409, 'LAW_IMPORT_STALE', '法规库在此期间已变化，请放弃后重新导入')


def apply(db, iid, user_id):
    lock(db)
    record = load(db, iid, locked=True)
    if record.status != 'pending':
        raise AppError(409, 'LAW_IMPORT_STATE', '当前状态不能确认生效')
    # 锁住法规表，确保检查与写入之间连直接 SQL 写入也不能改变旧值或插入相同键。
    db.execute(text('LOCK TABLE laws IN SHARE ROW EXCLUSIVE MODE'))
    if record.kind == 'fulltext':
        from app.laws.fulltext import apply as apply_fulltext
        apply_fulltext(db, record)
        record.status, record.applied_by, record.applied_at = 'applied', user_id, utcnow()
        return record
    diff = record.diff
    changed = []
    for item in diff['changed']:
        row = db.get(Law, item['law_id'], populate_existing=True)
        if row is None or row.title != item['title'] or iso(row.published_on) != item['published_on']:
            _stale()
        if any(iso(getattr(row, field)) != values[0] for field, values in item['changes'].items()):
            _stale()
        changed.append((row, item['changes']))
    for item in diff['added']:
        if db.scalar(select(Law.id).where(Law.title == item['title'],
                                          Law.published_on == date.fromisoformat(item['published_on']))) is not None:
            _stale()
    now = utcnow()
    for row, changes in changed:
        for field, (_, value) in changes.items():
            setattr(row, field, date.fromisoformat(value) if field == 'effective_on' and value else value)
        if record.kind != 'sync' or row.source != 'builtin':
            row.source = 'sync' if record.kind == 'sync' else 'import'
        row.updated_at = now
    for item in diff['added']:
        values = dict(item)
        for field in ('published_on', 'effective_on'):
            values[field] = date.fromisoformat(values[field]) if values[field] else None
        db.add(Law(**values, source='sync' if record.kind == 'sync' else 'import', created_at=now, updated_at=now))
    if record.kind == 'sync':
        from app.laws.fulltext import apply as apply_fulltext
        db.flush()
        matched = []
        for item in diff.get('fulltext', []):
            law = db.scalar(select(Law).where(Law.title == item['title'],
                              Law.published_on == date.fromisoformat(item['published_on'])))
            if law is None:
                _stale()
            matched.append({**item, 'law_id': law.id})
        # 复用全文数量核对、替换、暂存清理及向量化入队；保持目录与全文同一事务。
        original_diff = record.diff
        record.diff = {'matched': matched}
        apply_fulltext(db, record)
        record.diff = original_diff
    record.status, record.applied_by, record.applied_at = 'applied', user_id, now
    return record


def discard(db, iid, user_id):
    lock(db)
    row = load(db, iid, locked=True)
    if row.status not in ('pending', 'failed'):
        raise AppError(409, 'LAW_IMPORT_STATE', '当前状态不能放弃')
    row.status, row.applied_by, row.applied_at = 'discarded', user_id, utcnow()
    from sqlalchemy import delete
    from app.db.models import LawImportArticle
    db.execute(delete(LawImportArticle).where(LawImportArticle.import_id == row.id))
    return row
