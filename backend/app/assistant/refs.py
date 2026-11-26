"""回复的参考资料：公司知识库片段（V2-D）。查询原文只在内存中用于本地向量，不记录。"""
from sqlalchemy import select

from app.db.models import AssistantMessage
from app.embedding import engine as embedding
from app.redaction import engine


def prepare(db, task, user_message):
    """每条回复只检索一次；返回 ({'kb': [...]}, extras 元数据)。"""
    from app.admin.settings_store import get_setting
    from app.kb.retrieve import retrieve as kb_retrieve
    from app.redaction.restore import restore
    refs = {'kb': []}
    dropped = 0
    if user_message.params.get('use_kb', False) and get_setting(db, 'assistant.kb_enabled'):
        query = restore(user_message.content_redacted, engine.load_redaction_map(db, task.id))
        refs['kb'], dropped = kb_retrieve(db, task, query, vector=embedding.embed([query], 'query')[0])
    # 引用登记只存本地 id（V321-E-13）：编号与 prompt.build_messages 中的 [KBn] 一一对应。
    # 编号在会话内固定（V33-B-02）：同一片段沿用前几轮的编号，新片段往后编，模型回指上一轮的编号也不会指错；
    # 前几轮登记过的编号随本条带上（carried），本轮回复写到时同样能定位，没写到不列入依据。
    earlier = {}
    if user_message.id is not None:
        for extras in db.scalars(select(AssistantMessage.extras).where(
                AssistantMessage.task_id == task.id, AssistantMessage.role == 'assistant',
                AssistantMessage.id < user_message.id).order_by(AssistantMessage.id)):
            for item in (extras or {}).get('refs', []):
                if item.get('kind') == 'kb':
                    earlier[item['ref']] = {k: v for k, v in item.items() if k != 'carried'}
    known = {str(item['chunk_id']): ref for ref, item in earlier.items()}
    registered = []
    for item in refs['kb']:
        ref = known.get(str(item['chunk_id']))
        if ref is None:
            used = [int(r[2:]) for r in known.values() if r[2:].isdigit()]
            ref = known[str(item['chunk_id'])] = f'KB{max(used, default=0) + 1}'
        item['ref'] = ref
        registered.append({'ref': ref, 'kind': 'kb', 'chunk_id': item['chunk_id']})
    current = {r['ref'] for r in registered}
    metadata = {'kb_sent': len(refs['kb']), 'kb_dropped': dropped,
                'refs': registered + [{**item, 'ref': ref, 'carried': True} for ref, item in earlier.items() if ref not in current]}
    return refs, metadata
