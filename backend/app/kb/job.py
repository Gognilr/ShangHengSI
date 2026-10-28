"""知识库解析：复用解析器、转换器与 OCR 锁；输入仅在内存解密。"""
import base64
import re
import uuid

from sqlalchemy import select, delete

from app.db.models import KbDoc, KbChunk, Job
from app.embedding import engine
from app.security import crypto
from app.tasks.pipeline import _parse_bytes
from app.worker import job_handler


def split(nodes, limit=800):
    """完整段落合并到约 800 字；标题开启片段，表格按首行列名展开。"""
    out, heading, parts, size = [], None, [], 0
    tables, table_headers = {}, {}
    ordered = []
    for node in nodes:
        if node.part != 'body':
            continue
        if node.kind in ('table_cell', 'sheet_cell'):
            match = re.match(r'(.*?)-R(\d+)-C(\d+)$', node.loc)
            sheet = re.match(r'(.*?)-([A-Z]+)(\d+)$', node.loc) if node.kind == 'sheet_cell' else None
            if match:
                group, row, col = match.group(1), int(match.group(2)), int(match.group(3))
            elif sheet:
                group, row, col = sheet.group(1), int(sheet.group(3)), sheet.group(2)
            else:
                ordered.append((False, node.text)); continue
            key = (group, row)
            if key not in tables:
                tables[key] = []
                ordered.append((key, None))
            tables[key].append((col, node.text))
        else:
            ordered.append((node.kind == 'heading', node.text))
    def flush():
        nonlocal parts, size
        if parts:
            out.append((heading, '\n'.join(parts)))
        parts, size = [], 0
    for marker, content in ordered:
        if isinstance(marker, tuple):
            group, _ = marker
            values = sorted(tables[marker])
            if group not in table_headers:
                table_headers[group] = dict(values)
                continue
            content = '；'.join(f'{table_headers[group].get(col, "列" + str(col))}：{value}' for col, value in values)
            marker = False
        if not content or not content.strip():
            continue
        if marker is True:
            flush(); heading = content
        if parts and size + len(content) + 1 > limit:
            flush()
        parts.append(content)
        size += len(content) + 1
    flush()
    return out


@job_handler('kb_import')
def handle_import(db, payload):
    kid = uuid.UUID(payload['doc_id'])
    row = db.get(KbDoc, kid, with_for_update=True)
    if row is None or row.status != 'parsing':
        db.rollback(); return
    try:
        parsed, _ = _parse_bytes(payload['ext'], crypto.decrypt(base64.b64decode(payload['data_enc'])), 1, 0)
        parts = split(parsed.nodes)
        if not parts:
            raise ValueError('empty')
        vectors = engine.embed([text for _, text in parts], 'passage')
        if len(vectors) != len(parts):
            raise ValueError('embedding count')
        db.execute(delete(KbChunk).where(KbChunk.doc_id == kid))
        db.add_all(KbChunk(doc_id=kid, seq=i, heading_enc=crypto.encrypt_text(heading) if heading else None,
                           text_enc=crypto.encrypt_text(text), embedding=vector)
                   for i, ((heading, text), vector) in enumerate(zip(parts, vectors), 1))
        row.status, row.chunk_count, row.error_message = 'ready', len(parts), None
        _clear_input(db, kid)
        db.commit()
    except Exception:
        db.rollback()
        row = db.get(KbDoc, kid, with_for_update=True)
        if row is not None and row.status == 'parsing':
            row.status, row.error_message = 'failed', '知识库解析失败，请重新上传；仍然失败请联系管理员'
        _clear_input(db, kid)
        db.commit()
        raise


def _clear_input(db, kid):
    for job in db.scalars(select(Job).where(Job.type == 'kb_import', Job.payload['doc_id'].astext == str(kid))):
        job.payload = {'doc_id': str(kid)}
