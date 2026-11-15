"""只检索本人原件仍在的审核条款；来源名称不进入外发。"""
import re

from sqlalchemy import or_, select, exists

from app.db.models import AssistantMessage, ReviewRun, Task, TaskFile, DocNode, TaskClauseVector
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
    # 会话里审核过的文件也算历史合同（V32-A-02）；本会话自身不检索
    reviewed = exists().where(ReviewRun.task_id == Task.id)
    rows = db.execute(select(TaskClauseVector, Task).join(Task, Task.id == TaskClauseVector.task_id)
        .where(Task.owner_id == task.owner_id, Task.id != task.id,
               or_(Task.kind == 'review', (Task.kind == 'assistant') & reviewed),
               ~deleted, TaskClauseVector.embedding.is_not(None), 1 - distance >= CLAUSE_PAIR_MIN)
        .order_by(distance, TaskClauseVector.id).limit(10)).all()
    out = []
    for clause, source in rows:
        if len(out) >= 3:
            break
        if source.kind == 'assistant' and not _reviewed_file(db, source, clause.file_seq):
            continue
        nodes = db.scalars(select(DocNode).join(TaskFile).where(DocNode.task_id == source.id,
             TaskFile.seq == clause.file_seq, DocNode.part == 'body', DocNode.loc.in_(clause.locs))
             .order_by(DocNode.ord)).all()
        if not nodes:
            continue
        text = '\n'.join(decrypt_text(node.text_enc) for node in nodes)
        # 日期偏移等以 @ 开头的特殊键不是真实值（V31-P-07）。
        source_map = {k: v for k, v in engine._load_full_map(db, source.id).items() if not k.startswith('@')}
        protected = protect(db, task, text, full_source=source_map, extra_task_ids=[source.id])
        if protected is None:
            continue
        file = db.get(TaskFile, nodes[0].file_id)
        month = source.created_at.strftime('%Y-%m')
        out.append({'task_id': str(source.id), 'label': f'{_TYPES.get(file.doc_type, "合同")}，{month}',
                    'month': month, 'text': protected, 'file_seq': clause.file_seq, 'locs': list(clause.locs)})
    return out


def _reviewed_file(db, source, seq) -> bool:
    """会话里只有被审核轮次选用过的文件计入；file_ids 为空的轮次视为全部文件。"""
    file_id = db.scalar(select(TaskFile.id).where(TaskFile.task_id == source.id, TaskFile.seq == seq))
    return any(not ids or str(file_id) in ids
               for ids in db.scalars(select(ReviewRun.file_ids).where(ReviewRun.task_id == source.id)))


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
    # 引用登记只存本地 id（V321-E-13）：编号与 prompt.build_messages 中的 [KBn]、[Hn] 一一对应。
    # 编号在会话内固定（V33-B-02）：同一片段沿用前几轮的编号，新片段往后编，模型回指上一轮的编号也不会指错；
    # 前几轮登记过的编号随本条带上（carried），本轮回复写到时同样能定位，没写到不列入依据。
    earlier = {}
    if user_message.id is not None:
        for extras in db.scalars(select(AssistantMessage.extras).where(
                AssistantMessage.task_id == task.id, AssistantMessage.role == 'assistant',
                AssistantMessage.id < user_message.id).order_by(AssistantMessage.id)):
            for item in (extras or {}).get('refs', []):
                earlier[item['ref']] = {k: v for k, v in item.items() if k != 'carried'}
    known = {_ref_key(item): ref for ref, item in earlier.items()}
    registered = []
    for kind, prefix, items in (('kb', 'KB', refs['kb']), ('history', 'H', refs['history'])):
        for item in items:
            entry = ({'kind': 'kb', 'chunk_id': item['chunk_id']} if kind == 'kb' else
                     {'kind': 'history', 'task_id': item['task_id'], 'file_seq': item['file_seq'],
                      'locs': item['locs'], 'label': item['label']})
            ref = known.get(_ref_key(entry))
            if ref is None:
                used = [int(r[len(prefix):]) for r in known.values() if re.fullmatch(rf'{prefix}\d+', r)]
                ref = known[_ref_key(entry)] = f'{prefix}{max(used, default=0) + 1}'
            item['ref'] = ref
            registered.append({'ref': ref, **entry})
    current = {r['ref'] for r in registered}
    metadata = {'kb_sent': len(refs['kb']), 'kb_dropped': dropped,
                'refs': registered + [{**item, 'ref': ref, 'carried': True} for ref, item in earlier.items() if ref not in current]}
    return refs, metadata


def _ref_key(item):
    if item['kind'] == 'kb':
        return ('kb', str(item['chunk_id']))
    return ('history', str(item['task_id']), item['file_seq'], tuple(item['locs']))
