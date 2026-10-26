"""T-41：按内容识别、表头、日期、重复、限额与失败清理。"""
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.laws import catalog
from app.laws.job import handle_import
from app.db.models import LawImport, LawImportFile, Law
from app.parsing import legacy
from app.parsing.model import ParseError
from app.security import crypto
from tests.review_support import env  # noqa: F401
from tests.fixtures.laws.make_law_fixtures import catalog_bytes, HEADERS


def run(sm, files, status='valid'):
    with sm() as db:
        row = LawImport(kind='catalog', status='parsing', declared_status=status, file_names=['目录.xlsx']*len(files))
        db.add(row)
        db.flush()
        iid = row.id
        for n, data in enumerate(files, 1):
            db.add(LawImportFile(import_id=iid, seq=n, name='目录.xlsx', data_enc=crypto.encrypt(data)))
        db.commit()
        handle_import(db, {'import_id': str(iid)})
    with sm() as db:
        assert db.scalar(select(func.count()).select_from(LawImportFile)) == 0
        assert db.scalar(select(func.count()).select_from(Law)) == 0
        return db.get(LawImport, iid)


def test_t41_4_ole_conversion_by_content(db_sessionmaker, env, monkeypatch):
    calls = []
    original_check = catalog.filecheck.check_ole
    def checked(f, ext, name):
        calls.append(ext)
        return original_check(f, ext, name)
    monkeypatch.setattr(catalog.filecheck, 'check_ole', checked)
    monkeypatch.setattr(legacy, 'convert', lambda data, ext: catalog_bytes([
        ['示例供货合同法', '2020-01-01', None, '法律']]))
    got = run(db_sessionmaker, [(Path(__file__).parent/'fixtures/legacy/quotation.xls').read_bytes()])
    assert got.status == 'pending' and got.added == 1 and calls == ['xls']


def test_t41_4_converter_unavailable(db_sessionmaker, env, monkeypatch):
    monkeypatch.setattr(catalog.filecheck, 'check_ole', lambda *a, **kw: None)
    def fail(*args):
        raise ParseError(legacy.MSG_UNAVAILABLE)
    monkeypatch.setattr(legacy, 'convert', fail)
    got = run(db_sessionmaker, [catalog.filecheck.OLE_MAGIC+b'fake'])
    assert got.status == 'failed' and got.error == legacy.MSG_UNAVAILABLE


def test_t41_5_missing_column(db_sessionmaker, env):
    got = run(db_sessionmaker, [catalog_bytes([], ['标题', '法律法规分类'])])
    assert got.status == 'failed' and got.error == '第 1 个文件缺少列：公布日期'


def test_t41_6_8_9_status_duplicates_skips(db_sessionmaker, env):
    first = catalog_bytes([
        ['示例法甲', datetime(2020, 1, 1), None, '法律', '已废止'],
        [None, '2020-01-01', None, '法律', None],
        ['示例法乙', 'bad', None, '法律', None],
        ['示例法丙', None, None, '法律', None],
        ['长'*301, '2020-01-01', None, '法律', None],
        ['示例法丁', '2020-02-01', '2021-01-01', '法律', '未知'],
    ], HEADERS+['时效性'])
    second = catalog_bytes([['示例法甲', '2020-01-01', None, '法律']])
    got = run(db_sessionmaker, [first, second])
    assert got.status == 'pending' and got.added == 2 and got.skipped == 4
    assert got.diff['duplicates'] == 1
    assert [x['status'] for x in got.diff['added']] == ['repealed', 'valid']
    assert [x['row'] for x in got.diff['skipped']] == [3, 4, 5, 6]
    assert all(x['file'] == 1 and x['reason'] for x in got.diff['skipped'])


@pytest.mark.parametrize('content', [b'not a spreadsheet', b'PK\x03\x04broken'])
def test_invalid_content_cleanup(db_sessionmaker, env, content):
    assert run(db_sessionmaker, [content]).status == 'failed'


def test_row_limits_and_skip_details(db_sessionmaker, env, monkeypatch):
    monkeypatch.setattr(catalog, 'MAX_FILE_ROWS', 2)
    assert run(db_sessionmaker, [catalog_bytes([[None]]*3)]).status == 'failed'
    monkeypatch.setattr(catalog, 'MAX_FILE_ROWS', 5000)
    monkeypatch.setattr(catalog, 'MAX_TOTAL_ROWS', 3)
    assert run(db_sessionmaker, [catalog_bytes([[None]]*2)]*2).status == 'failed'
    monkeypatch.setattr(catalog, 'MAX_TOTAL_ROWS', 20000)
    got = run(db_sessionmaker, [catalog_bytes([[None]]*205)])
    assert got.skipped == 205 and len(got.diff['skipped']) == 200


def test_header_first_nonempty_within_five_and_bad_optional_dates(db_sessionmaker, env):
    got = run(db_sessionmaker, [catalog_bytes([
        [], [], [], [' 标题 ', ' 公布日期 ', '法律法规分类', '施行日期'],
        [' 示例法 ', '2020-01-01', '法律', 'bad'],
    ], headers=[])])
    assert got.status == 'pending' and got.diff['added'][0]['title'] == '示例法'
    assert got.diff['added'][0]['effective_on'] is None


def test_macro_rejected_without_contents(db_sessionmaker, env):
    import io
    import zipfile
    buf = io.BytesIO(catalog_bytes([['示例法', '2020-01-01', None, '法律']]))
    with zipfile.ZipFile(buf, 'a') as z:
        z.writestr('xl/vbaProject.bin', b'fictional macro')
    got = run(db_sessionmaker, [buf.getvalue()])
    assert got.status == 'failed' and '含宏' in got.error and '示例法' not in got.error


def test_unexpected_exception_cleanup_and_no_logging(db_sessionmaker, env, monkeypatch, caplog):
    def fail(*a, **kw):
        raise RuntimeError('内部文件标题与秘密内容')
    monkeypatch.setattr(catalog, 'load_workbook', fail)
    got = run(db_sessionmaker, [catalog_bytes([['示例法', '2020-01-01', None, '法律']])])
    assert got.status == 'failed' and got.error == '目录表格解析失败，请检查文件后重新上传'
    assert '内部文件标题与秘密内容' not in caplog.text


def test_interrupted_final_failure_cleans_files(db_sessionmaker, env):
    from app.db.models import Job
    from app.laws.job import finalize_interrupted
    with db_sessionmaker() as db:
        row = LawImport(kind='catalog', status='parsing', declared_status='valid', file_names=['目录.xlsx'])
        db.add(row)
        db.flush()
        iid = row.id
        db.add(LawImportFile(import_id=iid, seq=1, name='目录.xlsx', data_enc=crypto.encrypt(b'private file')))
        db.add(Job(type='law_import', status='failed', payload={'import_id': str(iid)}))
        db.commit()
        finalize_interrupted(db)
        assert db.get(LawImport, iid).status == 'failed'
        assert db.scalar(select(func.count()).select_from(LawImportFile)) == 0
        finalize_interrupted(db)
        assert db.get(LawImport, iid).error == '目录导入中断，请重新上传'


def test_encrypted_import_files_in_key_rotation():
    from app.security.rotate import encrypted_columns
    assert LawImportFile.__table__.c.data_enc in encrypted_columns()
