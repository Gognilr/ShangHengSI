"""回复依据（V321-E-13）：只认本地登记过的 id，模型自写的编号对不上就按原文显示。

sources 是本条回复可点的依据；cites 是回复正文（已还原）里要渲染成锚点的原样文字与对应依据。
引用登记只是本地 id 与标题，不改外发内容。
"""
import re
import uuid

from sqlalchemy import select

from app.db.models import AssistantMessage, DocNode, Finding, KbChunk, KbDoc, Task, TaskFile
from app.laws.index import canonical
from app.redaction.restore import _LOCATION
from app.security.crypto import decrypt_text
from app.security.errors import NotFoundError

_TAG = re.compile(r'[\[［【]\s*(KB|H)\s*(\d{1,3})\s*[\]］】]')
_NO = r'第[零〇一二三四五六七八九十百千\d]+条(?:之[一二三四五六七八九十]+)?'
_CITATION = re.compile(rf'《([^》\n]{{1,60}})》\s*({_NO})((?:\s*[、和及与]\s*{_NO})*)')
_MORE = re.compile(rf'\s*[、和及与]\s*({_NO})')
_DIGITS = {c: i for i, c in enumerate('零一二三四五六七八九')} | {'〇': 0, '两': 2}
_UNITS = {'十': 10, '百': 100, '千': 1000}


def _number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    total = digit = 0
    for char in text:
        if char in _DIGITS:
            digit = _DIGITS[char]
        elif char in _UNITS:
            total += (digit or 1) * _UNITS[char]
            digit = 0
        else:
            return None
    return total + digit


def _article(no: str) -> tuple | None:
    m = re.fullmatch(r'第([零〇一二三四五六七八九十百千\d]+)条(?:之([一二三四五六七八九十]+))?', no or '')
    return (_number(m[1]), m[2] or '') if m else None


def _laws(db, message: AssistantMessage) -> list[dict]:
    finding = db.get(Finding, message.finding_id) if message.finding_id else None
    return [{'ref': f'LAW{i}', **r} for i, r in enumerate(finding.law_refs if finding else [], 1)
            if r.get('title') and r.get('article_no')]


def build(db, task: Task, message: AssistantMessage, content: str, locations: dict[str, str]) -> tuple[list, list]:
    """content 为还原后的正文；locations 为本会话 loc → 可读位置。"""
    sources, cites, seen = [], [], set()

    def cite(token: str, ref: str):
        if token and all(c['token'] != token for c in cites):
            cites.append({'token': token, 'ref': ref})

    for m in _LOCATION.finditer(message.content_redacted or ''):
        loc = m.group().strip('[]')
        if loc in locations and loc not in seen:
            seen.add(loc)
            sources.append({'ref': loc, 'kind': 'para', 'label': locations[loc], 'cited': True})
            cite(locations[loc], loc)
    registered = {r['ref']: r for r in message.extras.get('refs', [])}
    used = []
    for m in _TAG.finditer(content):
        ref = f'{m[1]}{int(m[2])}'
        if ref in registered:
            cite(m.group(), ref)
            if ref not in used:
                used.append(ref)
    # 引用过的在前，附带未引用的在后；前几轮带来的编号（carried）只在本条写到时列出（V33-B-02）
    for ref in used + [r for r in registered if r not in used and not registered[r].get('carried')]:
        item = registered[ref]
        if item['kind'] == 'kb':
            chunk = db.get(KbChunk, item['chunk_id'])
            doc = db.get(KbDoc, chunk.doc_id) if chunk else None
            label = f'公司知识库 · {decrypt_text(doc.name_enc)}' if doc else '公司知识库 · 片段已删除'
        else:
            label = f'历史合同 · {item.get("label", "合同")}'
        sources.append({'ref': ref, 'kind': item['kind'], 'label': label, 'cited': ref in used})
    laws = _laws(db, message)
    cited_laws = set()
    for m in _CITATION.finditer(content):
        title = canonical(m[1].strip())
        # 连写的条号（《民法典》第六百二十条、第六百二十一条）：首条连书名号作锚点，后续条连分隔符作锚点（V33-B-02）
        parts = [(m.group(0)[:m.end(2) - m.start(0)], m[2])] + [(x.group(0), x[1]) for x in _MORE.finditer(m[3] or '')]
        for token, no in parts:
            law = next((r for r in laws if (canonical(r['title']), _article(r['article_no'])) == (title, _article(no))), None)
            if law:
                cite(token.strip(), law['ref'])
                cited_laws.add(law['ref'])
    for law in sorted(laws, key=lambda r: r['ref'] not in cited_laws):
        sources.append({'ref': law['ref'], 'kind': 'law', 'label': f'《{law["title"]}》{law["article_no"]}',
                        'cited': law['ref'] in cited_laws})
    return sources, cites


def detail(db, task: Task, message: AssistantMessage, ref: str) -> dict:
    """依据原文：只放行本条回复登记过的 ref；历史合同必须仍是本人、原件仍在。"""
    missing = NotFoundError('SOURCE_NOT_FOUND', '依据不存在或已删除')
    if ref.startswith('LAW'):
        law = next((r for r in _laws(db, message) if r['ref'] == ref), None)
        if law is None:
            raise missing
        return {'kind': 'law', 'title': f'《{law["title"]}》{law["article_no"]}', 'text': law.get('text') or '', 'link': None}
    item = next((r for r in message.extras.get('refs', []) if r['ref'] == ref), None)
    if item is None:
        raise missing
    if item['kind'] == 'kb':
        chunk = db.get(KbChunk, item['chunk_id'])
        doc = db.get(KbDoc, chunk.doc_id) if chunk else None
        if doc is None:
            raise missing
        return {'kind': 'kb', 'title': decrypt_text(doc.name_enc), 'text': decrypt_text(chunk.text_enc), 'link': None}
    source = db.get(Task, uuid.UUID(item['task_id']))
    if source is None or source.owner_id != task.owner_id:
        raise missing
    file = db.scalar(select(TaskFile).where(TaskFile.task_id == source.id, TaskFile.seq == item['file_seq']))
    if file is None or file.original_deleted_at is not None:
        raise missing
    nodes = db.scalars(select(DocNode).where(DocNode.file_id == file.id, DocNode.part == 'body',
                                             DocNode.loc.in_(item['locs'])).order_by(DocNode.ord)).all()
    if not nodes:
        raise missing
    link = f'/assistant/{source.id}?mode=review' if source.kind == 'assistant' else f'/review/{source.id}'
    return {'kind': 'history', 'title': item.get('label', '合同'), 'text': '\n'.join(decrypt_text(n.text_enc) for n in nodes),
            'link': link}
