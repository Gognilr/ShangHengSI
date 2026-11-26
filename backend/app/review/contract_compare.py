"""一次性合同对比，只读取本次请求的两份文件。"""
import re
from io import BytesIO

from fastapi import UploadFile

from app.config import get_settings
from app.export.report_docx import _clean, _font, _new_document, _table
from app.parsing import legacy
from app.parsing.docx import ComparisonDocx, parse_docx
from app.parsing.model import ParseError, ParsedNode
from app.review.drafts import COMPARE_PARTS, _region_location, compare
from app.security.errors import AppError
from app.tasks.service import clean_filename, inspect_upload

_TABLE_LOCATION = re.compile(r'-T(\d+)-R(\d+)-C(\d+)')


def _read(file: UploadFile, label: str, seq: int, max_bytes: int) -> tuple[ComparisonDocx, bool]:
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
    parsed.nodes = [node for node in parsed.nodes if node.part in COMPARE_PARTS and (node.text or node.text_before)]
    if not parsed.nodes:
        raise AppError(422, 'CONTRACT_TEXT_MISSING', f'{label}没有可对比的文字，请检查文件')
    return parsed, ext == 'doc'


def _location(node: ParsedNode) -> str:
    if region := _region_location(node):
        return region
    cells = _TABLE_LOCATION.findall(node.loc)
    if cells:
        return ' / '.join(f'表 {table} · 第 {row} 行第 {col} 列' for table, row, col in cells)
    _, marker, paragraph = node.loc.rpartition('-P')
    return f'正文第 {paragraph if marker else node.ord} 段'


def compare_contracts(baseline: UploadFile, revised: UploadFile) -> dict:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    old_doc, old_converted = _read(baseline, '基准稿', 1, max_bytes)
    new_doc, new_converted = _read(revised, '修改稿', 2, max_bytes)
    old, new = old_doc.nodes, new_doc.nodes
    old_locations = {node.loc: _location(node) for node in old}
    new_locations = {node.loc: _location(node) for node in new}
    result = compare(old, new, mode='exact', literal=True, with_map=True, include_regions=True, compact_numbering=True)

    def locations(change):
        return {
            'baseline_location': old_locations.get(change['prev_loc']), 'revised_location': new_locations.get(change['loc']),
            'baseline_loc': change['prev_loc'], 'revised_loc': change['loc'], 'segs': change['segs'],
        }

    def paper(nodes):
        ordered = sorted((n for n in nodes if n.text), key=lambda n: n.part == 'footer')
        return [{'loc': n.loc, 'kind': n.kind, 'part': n.part, 'text': n.text} for n in ordered]

    return {
        'changes': [{
            **locations(change), 'id': change['id'], 'type': change['type'], 'clause': change['clause'],
            'anchor': change.get('anchor'), 'after': bool(change.get('after')),
            'brief': change['brief'], 'untracked': change['untracked'], 'mixed': change['mixed'],
            'detail_type': change.get('detail_type'), 'visibility': change.get('visibility'),
            'visibility_segs': change.get('visibility_segs', []),
            'members': [locations(member) for member in change.get('numbering_members', ())],
        } for change in result['changes']],
        'baseline': paper(old),
        'revised': paper(new),
        'baseline_omitted': old_doc.omitted, 'revised_omitted': new_doc.omitted,
        # 基准稿位置 → 修改稿位置（未变、修改、位置调整的段落），用来给新增段落在基准稿一侧找落点
        'pairs': result['map'],
        'converted': old_converted or new_converted,
    }


_CHANGE_LABELS = {
    'mod': '文字修改', 'add': '新增', 'del': '删除', 'move': '位置调整', 'format': '标点、空格',
    'numbering': '编号变化', 'visibility': '隐藏文字',
}


def _side(changes, side, excluded):
    locations, content = [], []
    for change in changes:
        locations.append(change[f'{side}_location'] or '—')
        if content:
            content.append(('\n', False))
        segments = [(s['s'], s['t'] != 'eq') for s in change['segs'] if s['t'] != excluded]
        content.extend(segments or [('—', False)])
    return '\n'.join(locations), [(_clean(text), marked) for text, marked in content]


def export_contract_comparison(baseline: UploadFile, revised: UploadFile) -> bytes:
    result = compare_contracts(baseline, revised)
    doc = _new_document('合同对比表')
    _font(doc.add_paragraph().add_run(_clean(
        f"基准稿：{clean_filename(baseline.filename)}\n修改稿：{clean_filename(revised.filename)}")), 10)
    for side, label in (('baseline', '基准稿'), ('revised', '修改稿')):
        if omitted := result[f'{side}_omitted']:
            _font(doc.add_paragraph().add_run(f"{label}含{'、'.join(omitted)}，未对比。"), 9)
    rows = []
    for number, change in enumerate(result['changes'], 1):
        changes = [change, *change['members']]
        old_loc, old_text = _side(changes, 'baseline', 'ins')
        new_loc, new_text = _side(changes, 'revised', 'del')
        label = _CHANGE_LABELS[change['detail_type'] or change['type']]
        if change['members'] or change['visibility']:
            label += '\n' + (change['brief']['text'] if change['members'] else change['visibility'])
        trace = '已留修订痕迹\n未留痕' if change['mixed'] else '未留痕' if change['untracked'] else '已留修订痕迹'
        if change['mixed']:
            trace_parts = []
            for item in changes:
                for s in item['segs']:
                    if 'u' not in s or (change['detail_type'] == 'visibility' and s['t'] == 'del'):
                        continue
                    status = '未留痕' if s['u'] else '已留修订痕迹'
                    operation = '可见性' if change['detail_type'] == 'visibility' else '删' if s['t'] == 'del' else '增'
                    trace_parts.append(f"{status}（{operation}）：{s['s']}")
            for s in change['visibility_segs']:
                status = '未留痕' if s['u'] else '已留修订痕迹'
                trace_parts.append(f"{status}（可见性）：{s['s']}")
            trace = '\n'.join(trace_parts)
        rows.append((number, label, old_loc, new_loc, old_text, new_text, trace))
    if rows:
        _table(doc, ('序号', '类型', '基准稿位置', '修改稿位置', '基准稿原文', '修改稿原文', '是否留痕'),
               rows, (1.1, 2.8, 3.0, 3.0, 6.2, 6.2, 4.4))
    else:
        _font(doc.add_paragraph().add_run('未发现差异。'), 10)
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()
