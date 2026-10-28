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


def protect(db, task, text, *, full_source=None, extra_task_ids=()):
    """先把来源所有已知真实值映射为本会话代号，再调用问题脱敏。"""
    from app.chat.service import _redact, _terms, _value_spans
    db.scalar(select(RedactionMap).where(RedactionMap.task_id == task.id).with_for_update())
    mapping = engine._load_full_map(db, task.id)
    alloc = NeutralAllocator(); alloc.seed(mapping)
    if full_source:
        known = {value.casefold(): key.split('#', 1)[0] for key, value in mapping.items() if value}
        source_codes = {}
        for key, value in sorted(full_source.items(), key=lambda pair: len(pair[1]), reverse=True):
            if not value or not _value_spans(text, value):
                continue
            base = key.split('#', 1)[0]
            code = source_codes.get(base) or known.get(value.casefold())
            if code is None:
                # 来源代号是审核旧格式；类别取固定契约前缀，未知类别保守用自定义敏感词。
                prefix = base.strip('[]').split('_', 1)[0]
                category = {'我方': 'ORG', '客户': 'ORG', '公司': 'ORG', '人名': 'PERSON', '电话': 'PHONE',
                            '金额': 'AMOUNT', '单价': 'PRICE', '地址': 'ADDR', '项目': 'PROJECT',
                            '编号': 'REFNO', '邮箱': 'EMAIL', '账号': 'ACCOUNT'}.get(prefix, 'CUSTOM')
                if category == 'ORG':
                    code = alloc.org(value, '公司')
                elif category == 'ADDR':
                    code = alloc.address('国家未知', value)
                else:
                    code = alloc.numbered(category, value)
            source_codes[base] = code
            known[value.casefold()] = code
            if code not in mapping:
                mapping[code] = value
            elif mapping[code] != value and value not in mapping.values():
                n = 1
                while f'{code}#{n}' in mapping:
                    n += 1
                mapping[f'{code}#{n}'] = value
    result = _redact(text, mapping, _terms(db), allocator=alloc)
    if result.suspects:
        return None
    mapping.update(result.new_entries)
    # 新分配值先在内存复检；通过后才写入密文映射。
    ctx = recheck_context(db, task.id, extra_task_ids=extra_task_ids)
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
        items.append({'text': protected})
        size += len(protected)
    return items, dropped
