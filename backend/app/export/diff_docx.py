"""变化对照表与批注版里的“对方改动”批注（V35-D-02，设计 docs/v35/design/diff.md 3.5）。

只给意见、不改原件：对照表在内存中新生成；批注只挂批注，文档文字一字不改（沿用 14.1 的定位与逐字校验）。
"""
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.shared import Cm

from app.db.models import Task, TaskFile
from app.export.report_docx import _clean, _font, _new_document
from app.review import drafts

TYPE = {'mod': '修改', 'add': '新增', 'del': '删除', 'move': '位置调整', 'format': '格式变化'}


def side_text(change: dict) -> tuple[str, str]:
    """(上一稿, 本稿) 的文字：修改只取变化附近（前后各 12 字），新增 / 删除 / 位置调整取整段。"""
    segs = change['segs']
    if change['type'] in ('add', 'del', 'move'):
        text = segs[0]['s']
        if change['type'] == 'add':
            return '（无）', text
        if change['type'] == 'del':
            return text, '（本稿已删除）'
        return f"{change.get('prev_clause') or ''} {text}".strip(), f"{change['clause']} {text}".strip()
    first = next(i for i, s in enumerate(segs) if s['t'] != 'eq')
    last = max(i for i, s in enumerate(segs) if s['t'] != 'eq')
    head = segs[first - 1]['s'][-12:] if first > 0 and segs[first - 1]['t'] == 'eq' else ''
    tail = segs[last + 1]['s'][:12] if last + 1 < len(segs) and segs[last + 1]['t'] == 'eq' else ''
    old = ''.join(s['s'] for s in segs[first:last + 1] if s['t'] in ('eq', 'del'))
    new = ''.join(s['s'] for s in segs[first:last + 1] if s['t'] in ('eq', 'ins'))
    lead = '…' if first == 1 and len(segs[0]['s']) > 12 else ''
    trail = '…' if last + 1 < len(segs) and len(segs[last + 1]['s']) > 12 else ''
    return lead + head + old + tail + trail, lead + head + new + tail + trail


def trace_label(change: dict) -> str:
    if change['untracked'] is None:
        return '—'
    if change['mixed']:
        return '部分未留痕'
    return '未留痕' if change['untracked'] else '已留修订痕迹'


def comment_text(change: dict) -> str:
    old, new = side_text(change)
    trace = '' if change['untracked'] is None else ' · 未留痕' if change['untracked'] else ' · 已留痕'
    return f"【对方改动 · {TYPE[change['type']]}{trace}】原文：{old}　本稿：{new}\n—— 商衡SI 多稿对比"


def _stem(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', name.rsplit('.', 1)[0]) or '文件'


def build_diff_table(db, task: Task, file: TaskFile) -> tuple[str, bytes]:
    result = drafts.diff(db, task, file)
    changes = [c for c in result['changes'] if c['type'] != 'format']
    fmt = len(result['changes']) - len(changes)
    doc = _new_document('与上一稿的变化对照表')
    s = result['summary']
    info = doc.add_paragraph()
    _font(info.add_run(_clean(
        f"本稿：{file.original_name}（第 {result['no']} 稿）　对照：{result['prev_name']}（第 {result['prev_no']} 稿）\n"
        f"修改 {s['mod']} 处 · 新增 {s['add']} 条 · 删除 {s['del']} 条 · 位置调整 {s['move']} 处"
        + (f" · 未留痕 {s['untracked']} 处" if result['trace'] != 'none' else '')
        + (f"；另有格式变化 {fmt} 处（只改标点、空格或编号），未列出" if fmt else ''))), 10)
    note = doc.add_paragraph()
    tips = ['本表由本机逐字对比生成，未发送给模型；页眉页脚、批注、文本框不在对比范围内。']
    if result['trace'] == 'flag':
        tips.append('本稿解析较早，只能按段落判断是否留痕。')
    if result['ocr']:
        tips.append('扫描件识字可能有误差，单字差异请对照原件。')
    _font(note.add_run(''.join(tips)), 9)
    if not changes:
        _font(doc.add_paragraph().add_run('两稿正文没有实质变化。'), 10.5)
    else:
        table = doc.add_table(rows=1, cols=4)
        table.style = 'Table Grid'
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        widths = (Cm(4), Cm(10.6), Cm(10.6), Cm(2.6))
        for cell, title, width in zip(table.rows[0].cells, ('位置', '上一稿', '本稿', '是否留痕'), widths):
            cell.width = width
            _font(cell.paragraphs[0].add_run(title), 10, True)
        for c in changes:
            old, new = side_text(c)
            bold = bool(c['untracked'])
            row = table.add_row().cells
            for cell, text, width in zip(row, (f"{TYPE[c['type']]}　{c['clause'] or '—'}", old, new, trace_label(c)), widths):
                cell.width = width
                _font(cell.paragraphs[0].add_run(_clean(text)), 10, bold)
    stream = io.BytesIO()
    doc.save(stream)
    name = f"{_stem(file.original_name)}_与上一稿对照_{datetime.now(ZoneInfo('Asia/Shanghai')):%Y%m%d}.docx"
    return name, stream.getvalue()


def change_comments(db, task: Task, file: TaskFile) -> list[tuple[str | None, str | None, str]]:
    """批注版要挂的变化批注：(位置, 定位用的本稿文字, 批注文字)。删除的段落挂在后一段开头。"""
    if file.prev_file_id is None or task.purpose != 'chat':
        return []
    out = []
    for c in drafts.diff(db, task, file)['changes']:
        if c['type'] == 'format':
            continue
        if c['type'] == 'del':
            out.append((c.get('anchor'), None, comment_text(c)))
            continue
        quote = next((s['s'] for s in c['segs'] if s['t'] == 'ins'), None) if c['type'] == 'mod' else None
        out.append((c['loc'], quote if quote and len(quote.strip()) >= 2 else None, comment_text(c)))
    return out
