"""一次性文件审核入口，用虚构 Word 验证对比与留存边界。"""
import io
from pathlib import Path

from docx import Document
from sqlalchemy import func, select

from app.db.models import DocNode, Job, LlmCall, Task, TaskFile
from app.parsing import legacy
from tests.test_tasks_api import alice, files_dir  # noqa: F401

URL = '/api/v1/review/contract-compare'
FIXTURES = Path(__file__).parent / 'fixtures'


def _word(paragraphs, cell=None):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    if cell:
        doc.add_table(rows=1, cols=1).cell(0, 0).text = cell
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


def _pair(baseline, revised, ext='docx'):
    return {'baseline': (f'虚构基准稿.{ext}', baseline), 'revised': ('虚构修改稿.docx', revised)}


def _edits(change):
    return [(p['t'], p['s']) for p in change['segs'] if p['t'] != 'eq']


def test_contract_compare_literal_differences_and_no_task(alice, files_dir, db_sessionmaker):
    client, csrf = alice
    headers = {'X-CSRF-Token': csrf}
    old_move = '产品交付前应提供正式试验记录与合格证明（原件）。'
    new_move = '产品交付前应提供完整试验记录与合格证明(原件)。'
    old_notice = '书面通知送达后生效，双方各留存一份。'
    new_notice = '书面通知送达后生效。双方各留存一份'
    shared = ['许可证正本随货交付。', '质量保证期为交付之日起三年。']
    before = _word([old_move, old_notice, *shared, '涉密附录由接收方归档。'], '预付比例 30%')
    after = _word([new_notice, *shared, '维修工单应写明配件批次。', new_move], '预付比例 60%')

    response = client.post(URL, headers=headers, files=_pair(before, after))
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    result = response.json()
    assert not result['converted']
    notice, deletion, addition, moved, amount = result['changes']
    assert notice['type'] == 'format' and _edits(notice) == [('del', '，'), ('ins', '。'), ('del', '。')]
    assert deletion['type'] == 'del' and addition['type'] == 'add'
    assert moved['type'] == 'move' and moved['baseline_location'] == '正文第 1 段' and moved['revised_location'] == '正文第 5 段'
    assert _edits(moved) == [('del', '正式'), ('ins', '完整'), ('del', '（'), ('ins', '('), ('del', '）'), ('ins', ')')]
    assert amount['baseline_location'] == '表 1 · 第 1 行第 1 列' and _edits(amount) == [('del', '3'), ('ins', '6')]
    # 缝线（V36-F-04）：两稿正文按顺序回给本人浏览器，差异挂在各自的位置上；删除处挂在修改稿的锚点
    assert [n['text'] for n in result['baseline']][:2] == [old_move, old_notice]
    assert moved['baseline_loc'] == result['baseline'][0]['loc'] and moved['revised_loc'] == result['revised'][4]['loc']
    assert deletion['revised_loc'] is None and deletion['anchor'] in {n['loc'] for n in result['revised']}
    assert result['pairs'][moved['baseline_loc']] == moved['revised_loc']

    punctuation = client.post(URL, headers=headers, files=_pair(_word(['货到签收。']), _word(['货到签收'])))
    [only_change] = punctuation.json()['changes']
    assert only_change['type'] == 'format' and _edits(only_change) == [('del', '。')]
    assert client.post(URL, headers=headers, files=_pair(before, before)).json()['changes'] == []
    with db_sessionmaker() as db:
        for model in (Task, TaskFile, DocNode, Job, LlmCall):
            assert db.scalar(select(func.count()).select_from(model)) == 0
    assert not any(path.is_file() for path in files_dir.rglob('*'))


def test_contract_compare_tracks_column_change_and_visible_whitespace(alice):
    client, csrf = alice
    headers = {'X-CSRF-Token': csrf}

    def word(lines, rows):
        doc = Document()
        for line in lines:
            doc.add_paragraph(line)
        table = doc.add_table(rows=len(rows), cols=len(rows[0]))
        for row, cells in zip(table.rows, rows):
            for cell, text in zip(row.cells, cells):
                cell.text = text
        stream = io.BytesIO()
        doc.save(stream)
        return stream.getvalue()

    baseline = word(['原正文'], [('迁移条款', ''), ('固定行', '说明  ')])
    revised = word(['新加段落', '  原正文'], [('新增行', ''), ('', '迁移条款'), ('固定行', '说明')])
    answer = client.post(URL, headers=headers, files=_pair(baseline, revised))
    assert answer.status_code == 200, answer.text
    changes = answer.json()['changes']
    [move] = [c for c in changes if c['type'] == 'move']
    assert move['baseline_location'] == '表 1 · 第 1 行第 1 列'
    assert move['revised_location'] == '表 1 · 第 2 行第 2 列'
    whitespace = [c for c in changes if c['type'] == 'format']
    assert [_edits(c) for c in whitespace] == [[('ins', '  ')], [('del', '  ')]]
    assert not any('固定行' in str(c['segs']) for c in changes)


def test_contract_compare_legacy_and_input_errors(alice, monkeypatch):
    client, csrf = alice
    headers = {'X-CSRF-Token': csrf}
    doc = (FIXTURES / 'legacy' / 'contract_basic.doc').read_bytes()
    docx = (FIXTURES / 'docx' / 'contract_basic.docx').read_bytes()

    def convert(data, ext):
        assert ext == 'doc' and data == doc
        return (FIXTURES / 'legacy' / 'contract_basic_doc.docx').read_bytes()

    monkeypatch.setattr(legacy, 'convert', convert)
    result = client.post(URL, headers=headers, files=_pair(doc, docx, ext='DOC'))
    body = result.json()
    assert result.status_code == 200 and body['converted'] and body['changes'] == [] and len(body['baseline']) == len(body['revised'])

    unsupported = client.post(URL, headers=headers, files=_pair(doc, docx, ext='rtf'))
    assert unsupported.status_code == 422 and unsupported.json()['code'] == 'UNSUPPORTED_FILE_TYPE'
    empty = client.post(URL, headers=headers, files=_pair(_word([]), docx))
    assert empty.status_code == 422 and empty.json()['code'] == 'CONTRACT_TEXT_MISSING'
