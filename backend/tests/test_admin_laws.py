"""T-41：目录上传、差异确认、权限与提醒，全部内容虚构。"""
import logging
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.models import Law, LawImport, LawImportFile, AuditLog, Job
from app.laws.job import handle_import
from app.security import crypto
from tests.review_support import env  # noqa: F401
from tests.fixtures.laws.make_law_fixtures import catalog_bytes

ROOT = '/api/v1/admin'


@pytest.fixture
def admin(make_user, login, env):
    make_user('lawboss', role='biz_admin')
    return login('lawboss')


def upload(admin, rows=None, **over):
    c, token = admin
    data = {'kind': 'catalog', 'status': 'repealed'} | over
    return c.post(ROOT+'/law-imports', data=data, headers={'X-CSRF-Token': token},
                  files=[('files', ('目录.xlsx', catalog_bytes(rows or [
                      ['示例供货合同法', '2020-01-01', None, '法律']]), 'application/octet-stream'))])


def parse(sm, iid):
    with sm() as db:
        handle_import(db, {'import_id': iid})


def action(admin, iid, verb):
    c, token = admin
    return c.post(f'{ROOT}/law-imports/{iid}/{verb}', headers={'X-CSRF-Token': token})


def seed(sm):
    with sm() as db:
        for title, status in [('示例旧法', 'valid'), ('示例不变法', 'repealed'), ('示例未列法', 'valid')]:
            db.add(Law(title=title, category='法律', status=status, source='builtin',
                       published_on=date(2020, 1, 1), replaced_by='示例替代法'))
        db.commit()


def test_t41_1_2_7_13_14_preview_apply(admin, db_sessionmaker, caplog):
    seed(db_sessionmaker)
    rows = [[t, '2020-01-01', None, '法律'] for t in ['示例新法甲', '示例新法乙', '示例旧法', '示例不变法']]
    with caplog.at_level(logging.INFO):
        r = upload(admin, rows)
        assert r.status_code == 202, r.text
        iid = r.json()['id']
        with db_sessionmaker() as db:
            f = db.scalar(select(LawImportFile))
            assert crypto.decrypt(f.data_enc).startswith(b'PK') and not f.data_enc.startswith(b'PK')
            j = db.scalar(select(Job))
            assert j.type == 'law_import' and j.payload == {'import_id': iid}
        parse(db_sessionmaker, iid)
        got = admin[0].get(f'{ROOT}/law-imports/{iid}').json()
        assert (got['status'], got['added'], got['changed'], got['unchanged']) == ('pending', 2, 1, 1)
        assert got['diff']['changed'][0]['changes'] == {'status': ['valid', 'repealed']}
        assert all(x['status'] == 'repealed' for x in got['diff']['added'])
        with db_sessionmaker() as db:
            assert db.scalar(select(func.count()).select_from(Law)) == 3
            assert db.scalar(select(func.count()).select_from(LawImportFile)) == 0
        assert action(admin, iid, 'apply').status_code == 200
    with db_sessionmaker() as db:
        laws = {x.title: x for x in db.scalars(select(Law))}
        assert len(laws) == 5 and laws['示例旧法'].source == 'import'
        assert laws['示例未列法'].status == 'valid'
        assert laws['示例旧法'].replaced_by == '示例替代法'
        audit = db.scalar(select(AuditLog).where(AuditLog.action == 'law_import_apply'))
        assert audit.detail == {'kind': 'catalog', 'added': 2, 'changed': 1}
    assert all(t not in caplog.text for t in ['示例新法甲', '示例旧法', '示例供货合同法'])
    assert action(admin, iid, 'apply').status_code == 409
    assert 'diff' not in admin[0].get(ROOT+'/law-imports').json()['items'][0]


def test_t41_3_10_17_discard_and_alerts(admin, db_sessionmaker, make_user, login):
    iid = upload(admin).json()['id']
    assert upload(admin).json()['code'] == 'LAW_IMPORT_PENDING'
    assert action(admin, iid, 'discard').status_code == 409
    parse(db_sessionmaker, iid)
    assert upload(admin).status_code == 409
    for role in ['biz_admin', 'admin', 'sys_admin', 'user']:
        make_user(role, role=role)
        codes = [x['code'] for x in login(role)[0].get('/api/v1/auth/me').json()['alerts']]
        assert ('LAW_IMPORT_PENDING' in codes) == (role in ['biz_admin', 'admin'])
    assert action(admin, iid, 'discard').status_code == 200
    assert admin[0].get(f'{ROOT}/law-imports/{iid}').json()['status'] == 'discarded'
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(Law)) == 0
    assert action(admin, iid, 'discard').status_code == 409


@pytest.mark.parametrize('conflict', ['changed', 'added'])
def test_t41_11_stale_is_atomic(admin, db_sessionmaker, conflict):
    seed(db_sessionmaker)
    iid = upload(admin, [[t, '2020-01-01', None, '法律'] for t in ['示例新法', '示例旧法']]).json()['id']
    parse(db_sessionmaker, iid)
    with db_sessionmaker() as db:
        if conflict == 'changed':
            db.scalar(select(Law).where(Law.title == '示例旧法')).status = 'amended'
        else:
            db.add(Law(title='示例新法', category='法律', published_on=date(2020, 1, 1), status='valid', source='sync'))
        db.commit()
    response = action(admin, iid, 'apply')
    assert response.status_code == 409 and response.json()['code'] == 'LAW_IMPORT_STALE'
    with db_sessionmaker() as db:
        assert db.get(LawImport, uuid.UUID(iid)).status == 'pending'
        assert not list(db.scalars(select(Law).where(Law.source == 'import')))


@pytest.mark.parametrize('role', ['user', 'sys_admin'])
def test_t41_12_permissions(make_user, login, role):
    make_user(role, role=role)
    admin = login(role)
    assert upload(admin).status_code == 403
    c, token = admin
    for path in ['/laws', '/law-imports', '/law-imports/'+str(uuid.uuid4())]:
        assert c.get(ROOT+path).status_code == 403
    assert action(admin, str(uuid.uuid4()), 'apply').status_code == 403
    assert c.patch(ROOT+'/laws/1', json={'replaced_by': '示例'}, headers={'X-CSRF-Token': token}).status_code == 403


def test_t41_16_patch_and_query(admin, db_sessionmaker):
    seed(db_sessionmaker)
    c, token = admin
    got = c.get(ROOT+'/laws', params={'q': '示例旧', 'status': 'valid', 'category': '法律', 'page_size': 1}).json()
    assert got['total'] == 1 and len(got['items']) == 1
    assert got['counts']['valid'] == 2
    row = got['items'][0]
    assert row['has_fulltext'] is False
    path = ROOT+f"/laws/{row['id']}"
    headers = {'X-CSRF-Token': token}
    assert c.patch(path, json={'replaced_by': '示例新法'}, headers=headers).status_code == 200
    assert c.patch(path, json={'status': 'repealed'}, headers=headers).status_code == 422
    assert c.patch(path, json={'replaced_by': None}, headers=headers).json()['replaced_by'] is None
    with db_sessionmaker() as db:
        assert db.get(Law, row['id']).status == 'valid'
        assert db.scalar(select(AuditLog).where(AuditLog.action == 'law_update')) is not None


@pytest.mark.parametrize('data', [{'kind': 'fulltext'}, {'status': 'wrong'}])
def test_upload_rejects_invalid_fields(admin, data):
    assert upload(admin, **data).status_code == 422


@pytest.mark.parametrize('name,size,count', [('目录.csv', 1, 1), ('目录.xlsx', 5*1024*1024+1, 1),
                                           ('目录.xlsx', 1, 11)])
def test_upload_limits(admin, db_sessionmaker, name, size, count):
    c, token = admin
    r = c.post(ROOT+'/law-imports', data={'kind': 'catalog', 'status': 'valid'},
               files=[('files', (name, b'x'*size))]*count, headers={'X-CSRF-Token': token})
    assert r.status_code == 422
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(LawImport)) == 0
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_concurrent_uploads_only_one_accepted(admin, db_sessionmaker):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upload(admin).status_code, range(2)))
    assert sorted(results) == [202, 409]
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(LawImport)) == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_effective_date_changes_preserve_other_metadata(admin, db_sessionmaker):
    seed(db_sessionmaker)
    with db_sessionmaker() as db:
        law = db.scalar(select(Law).where(Law.title == '示例旧法'))
        law.effective_on = date(2020, 2, 1)
        law.repealed_on = date(2025, 1, 1)
        law.flk_id = 'a'*32
        law.office = '示例机关'
        db.commit()
    iid = upload(admin, [['示例旧法', '2020-01-01', '2021-01-01', '司法解释']]).json()['id']
    parse(db_sessionmaker, iid)
    diff = admin[0].get(f'{ROOT}/law-imports/{iid}').json()['diff']['changed'][0]['changes']
    assert diff == {'status': ['valid', 'repealed'], 'category': ['法律', '司法解释'],
                    'effective_on': ['2020-02-01', '2021-01-01']}
    assert action(admin, iid, 'apply').status_code == 200
    with db_sessionmaker() as db:
        law = db.scalar(select(Law).where(Law.title == '示例旧法'))
        assert law.effective_on == date(2021, 1, 1) and law.repealed_on == date(2025, 1, 1)
        assert law.flk_id == 'a'*32 and law.office == '示例机关'
    # 空施行日期不覆盖已存的日期。
    iid = upload(admin, [['示例旧法', '2020-01-01', None, '司法解释']]).json()['id']
    parse(db_sessionmaker, iid)
    assert admin[0].get(f'{ROOT}/law-imports/{iid}').json()['unchanged'] == 1
