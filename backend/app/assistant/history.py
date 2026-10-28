"""只检索本人原件仍在的审核条款；来源名称不进入外发。"""
from sqlalchemy import select, exists

from app.db.models import Task, TaskFile, DocNode, TaskClauseVector
from app.embedding import engine as embedding
from app.kb.retrieve import protect
from app.redaction import engine
from app.security.crypto import decrypt_text

CLAUSE_PAIR_MIN = 0.70
_TYPES = {'contract': '合同', 'quotation': '报价单', 'order': '订单', 'inquiry': '询价单', 'letter': '函件'}


def retrieve(db, task, query, *, vector=None):
    vector = embedding.embed([query], 'query')[0] if vector is None else vector
    distance = TaskClauseVector.embedding.cosine_distance(vector)
    deleted = exists().where(TaskFile.task_id == Task.id, TaskFile.original_deleted_at.is_not(None))
    rows = db.execute(select(TaskClauseVector, Task).join(Task, Task.id == TaskClauseVector.task_id)
        .where(Task.owner_id == task.owner_id, Task.kind == 'review', ~deleted,
               TaskClauseVector.embedding.is_not(None), 1 - distance >= CLAUSE_PAIR_MIN)
        .order_by(distance, TaskClauseVector.id).limit(3)).all()
    out = []
    for clause, source in rows:
        nodes = db.scalars(select(DocNode).join(TaskFile).where(DocNode.task_id == source.id,
             TaskFile.seq == clause.file_seq, DocNode.part == 'body', DocNode.loc.in_(clause.locs))
             .order_by(DocNode.ord)).all()
        if not nodes:
            continue
        text = '\n'.join(decrypt_text(node.text_enc) for node in nodes)
        protected = protect(db, task, text, full_source=engine._load_full_map(db, source.id), extra_task_ids=[source.id])
        if protected is None:
            continue
        file = db.get(TaskFile, nodes[0].file_id)
        month = source.created_at.strftime('%Y-%m')
        out.append({'task_id': str(source.id), 'label': f'{_TYPES.get(file.doc_type, "合同")}，{month}',
                    'month': month, 'text': protected})
    return out


def prepare(db, task, user_message):
    """每条回复只检索一次；查询原文仅在此内存变量中用于本地向量。"""
    from app.admin.settings_store import get_setting
    from app.kb.retrieve import retrieve as kb_retrieve
    from app.redaction.restore import restore
    use_kb = user_message.params.get('use_kb', False) and get_setting(db, 'assistant.kb_enabled')
    use_history = user_message.params.get('use_history', False)
    refs = {'kb': [], 'history': []}
    dropped = 0
    if use_kb or use_history:
        query = restore(user_message.content_redacted, engine.load_redaction_map(db, task.id))
        vector = embedding.embed([query], 'query')[0]
        if use_kb:
            refs['kb'], dropped = kb_retrieve(db, task, query, vector=vector)
        if use_history:
            refs['history'] = retrieve(db, task, query, vector=vector)
    metadata = {'kb_sent': len(refs['kb']), 'kb_dropped': dropped,
                'history_sources': [{key: item[key] for key in ('task_id', 'month')} for item in refs['history']]}
    return refs, metadata
