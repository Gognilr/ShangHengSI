"""一次性合同对比，只读取本次请求的两份文件。"""
import re

from fastapi import UploadFile

from app.config import get_settings
from app.parsing import legacy
from app.parsing.docx import parse_docx
from app.parsing.model import ParseError, ParsedNode
from app.review.drafts import compare
from app.security.errors import AppError
from app.tasks.service import clean_filename, inspect_upload

_TABLE_LOCATION = re.compile(r'-T(\d+)-R(\d+)-C(\d+)')


def _read(file: UploadFile, label: str, seq: int, max_bytes: int) -> tuple[list[ParsedNode], bool]:
    ext = clean_filename(file.filename).rpartition('.')[2].lower()
    if ext not in ('docx', 'doc'):
        raise AppError(422, 'UNSUPPORTED_FILE_TYPE', f'{label}只支持 DOCX 和 DOC 文件')
    # 校验错误只用稿件角色，文件名保留在调用者页面。
    inspect_upload(f'{label}.{ext}', file.file, max_bytes)
    data = file.file.read()
    try:
        parsed = parse_docx(legacy.convert(data, ext) if ext == 'doc' else data, seq, preserve_whitespace=True)
    except ParseError as exc:
        message = str(exc).replace('DOCX / XLSX', 'DOCX')
        raise AppError(422, 'CONTRACT_READ_FAILED', f'{label}读取失败。{message}') from None
    nodes = [node for node in parsed.nodes if node.part == 'body' and node.text.strip()]
    if not nodes:
        raise AppError(422, 'CONTRACT_TEXT_MISSING', f'{label}没有可对比的正文或表格文字，请检查文件')
    return nodes, ext == 'doc'


def _location(node: ParsedNode) -> str:
    cells = _TABLE_LOCATION.findall(node.loc)
    if cells:
        return ' / '.join(f'表 {table} · 第 {row} 行第 {col} 列' for table, row, col in cells)
    _, marker, paragraph = node.loc.rpartition('-P')
    return f'正文第 {paragraph if marker else node.ord} 段'


def compare_contracts(baseline: UploadFile, revised: UploadFile) -> dict:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    old, old_converted = _read(baseline, '基准稿', 1, max_bytes)
    new, new_converted = _read(revised, '修改稿', 2, max_bytes)
    old_locations = {node.loc: _location(node) for node in old}
    new_locations = {node.loc: _location(node) for node in new}
    changes = compare(old, new, mode='none', literal=True)['changes']
    # 两稿正文只回给上传者本人的浏览器画缝线（V36-F-04），不落库、不缓存（接口 no-store）
    return {
        'changes': [{
            'id': change['id'], 'type': change['type'], 'clause': change['clause'],
            'baseline_location': old_locations.get(change['prev_loc']),
            'revised_location': new_locations.get(change['loc']),
            'baseline_loc': change['prev_loc'], 'revised_loc': change['loc'],
            'anchor': change.get('anchor'), 'after': bool(change.get('after')),
            'brief': change['brief'], 'segs': change['segs'],
        } for change in changes],
        'baseline': [{'loc': node.loc, 'kind': node.kind, 'text': node.text} for node in old],
        'revised': [{'loc': node.loc, 'kind': node.kind, 'text': node.text} for node in new],
        'converted': old_converted or new_converted,
    }
