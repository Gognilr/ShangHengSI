"""逐片段脱敏与复检；不记录查询、片段、文档名或向量。"""
from sqlalchemy import select

from app.admin.settings_store import get_setting
from app.db.models import KbDoc, KbChunk, RedactionMap
from app.embedding import engine as embedding
from app.redaction import engine
from app.redaction.placeholders import NeutralAllocator
from app.redaction.recheck import recheck, recheck_context
from app.security.crypto import decrypt_text

KB_MIN_SCORE = 0.56


def protect(db, task, text):
    """按本会话对照表与问题相同的规则脱敏；有疑似项或复检命中返回 None。"""
    from app.chat.service import _redact, _terms
    db.scalar(select(RedactionMap).where(RedactionMap.task_id == task.id).with_for_update())
    mapping = engine._load_full_map(db, task.id)
    alloc = NeutralAllocator(); alloc.seed(mapping)
    result = _redact(text, mapping, _terms(db), allocator=alloc)
    if result.suspects:
        return None
    mapping.update(result.new_entries)
    # 新分配值先在内存复检；通过后才写入密文映射。
    ctx = recheck_context(db, task.id)
    if recheck(result.text, real_values=ctx.real_values + list(mapping.values()), terms=ctx.terms,
               placeholders=set(ctx.placeholders) | {key.split('#', 1)[0] for key in mapping}):
        return None
    engine.write_full_map(db, task.id, mapping)
    return result.text


def retrieve(db, task, query, *, vector=None):
    if not get_setting(db, 'assistant.kb_enabled'):
        return [], 0
    vector = embedding.embed([query], 'query')[0] if vector is None else vector
    distance = KbChunk.embedding.cosine_distance(vector)
    candidates = db.scalars(select(KbChunk).join(KbDoc).where(KbDoc.enabled.is_(True), KbDoc.status == 'ready',
                        KbChunk.embedding.is_not(None), 1 - distance >= KB_MIN_SCORE)
                        .order_by(distance, KbChunk.id).limit(5)).all()
    items, dropped, size = [], 0, 0
    for item in candidates:
        protected = protect(db, task, decrypt_text(item.text_enc))
        if protected is None:
            dropped += 1; continue
        if size + len(protected) > 4000:
            continue
        items.append({'text': protected, 'chunk_id': item.id})
        size += len(protected)
    return items, dropped
