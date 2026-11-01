"""V31-S-04：草稿的代表性接口流程，全部虚构。"""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import DocNode, Job, Party, RedactionItem, RedactionMap, Task, TaskFile
from app.main import API_PREFIX
from app.redaction import engine, ner
from app.tasks import pipeline, storage
from tests.review_support import env, REAL_OUR_NAME  # noqa: F401
from tests.test_redaction_api import docx_of

ROOT = API_PREFIX + '/tasks'
BODY = {'stance': 'sales', 'source': 'outgoing', 'policy': 'ai'}
pytestmark = pytest.mark.usefixtures('env')


@pytest.fixture(autouse=True)
def local_only_models(monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [])
    from app.review import clauses
    monkeypatch.setattr(clauses, 'store_vectors', lambda db, task: None)


@pytest.fixture
def owner(make_user, login):
    user = make_user('draft_owner')
    c, csrf = login(user.username)
    return c, {'X-CSRF-Token': csrf}, user


def document(total='1000', extra=''):
    return docx_of('销售合同', f'卖方：{REAL_OUR_NAME}', f'合同总价：USD {total}.00', '预付款：USD 100.00', extra)


def create(c, h, files=None):
    r = c.post(ROOT, headers=h, data={**BODY, 'draft': 'true', 'allow_duplicate': 'true'},
               files=files or [('files', ('主合同.docx', document(), 'application/octet-stream'))])
    assert r.status_code == 201, r.text
    return ROOT + '/' + r.json()['id']


def drain(sm, base):
    tid = base.rsplit('/', 1)[-1]
    with sm() as db:
        rows = list(db.scalars(select(Job).where(Job.type == 'parse', Job.payload['task_id'].astext == tid,
                                                Job.status == 'queued').order_by(Job.id)))
        for row in rows:
            row.status = 'running'
            db.commit()
            pipeline.handle_parse(db, row.payload)
            row.status = 'done'
            db.commit()


def test_draft_file_lifecycle_shared_codes_main_and_owner(owner, db_sessionmaker, make_user, login):
    c, h, _ = owner
    base = create(c, h)
    assert c.get(base).json()['busy']
    assert c.post(base + '/proceed', headers=h, json=BODY).json()['code'] == 'FILES_NOT_READY'
    drain(db_sessionmaker, base)
    detail = c.get(base).json()
    assert detail['status'] == 'draft' and detail['files'][0]['parse_state'] == 'done' and not detail['busy']
    first = detail['files'][0]['id']
    data = document('2000', '另由幻岚样例公司转运。')
    added = c.post(base + '/files', headers=h, files={'files': ('附件.docx', data)})
    assert added.status_code == 200, added.text
    second = added.json()['files'][1]['id']
    assert c.post(base + '/files', headers=h, files={'files': ('重名.docx', data)}).json()['code'] == 'DUPLICATE_IN_TASK'
    drain(db_sessionmaker, base)
    tid = uuid.UUID(base.rsplit('/', 1)[-1])
    with db_sessionmaker() as db:
        codes = {i.placeholder for i in db.scalars(select(RedactionItem).where(RedactionItem.task_id == tid, RedactionItem.category == 'OUR'))}
        assert codes == {'[我方]'}
        assert '10.0%' in '\n'.join(db.scalars(select(DocNode.redacted_text).where(DocNode.task_id == tid)))
    assert c.patch(base + '/files/' + second, headers=h, json={'is_main': True}).status_code == 200
    drain(db_sessionmaker, base)
    assert c.get(base).json()['title'] == '附件.docx'
    with db_sessionmaker() as db:
        assert '5.0%' in '\n'.join(db.scalars(select(DocNode.redacted_text).where(DocNode.task_id == tid)))
        file = db.get(TaskFile, uuid.UUID(second))
        key = file.storage_key
        assert '幻岚样例公司' in engine._load_full_map(db, tid).values()
    deleted = c.delete(base + '/files/' + second, headers=h)
    assert deleted.status_code == 200 and deleted.json()['files'][0]['is_main']
    with pytest.raises(FileNotFoundError):
        storage.load_original(key)
    with db_sessionmaker() as db:
        assert '幻岚样例公司' not in engine._load_full_map(db, tid).values()
        assert db.scalar(select(DocNode.id).where(DocNode.task_id == tid, DocNode.loc.startswith('F2-'))) is None
    assert c.delete(base + '/files/' + first, headers=h).json()['code'] == 'LAST_FILE'
    drain(db_sessionmaker, base)
    assert c.post(base + '/proceed', headers=h, json=BODY).json()['status'] == 'awaiting_confirm'
    make_user('draft_stranger')
    other, csrf = login('draft_stranger')
    assert other.delete(base + '/files/' + first, headers={'X-CSRF-Token': csrf}).status_code == 404
    assert c.post(base + '/runs', headers=h).status_code == 201
    assert c.post(base + '/files', headers=h, files={'files': ('新附件.docx', data)}).json()['code'] == 'TASK_STATE_CONFLICT'


def test_bad_file_does_not_fail_good_file_and_send_waits(owner, db_sessionmaker):
    c, h, _ = owner
    base = create(c, h)
    drain(db_sessionmaker, base)
    assert c.post(base + '/files', headers=h, files={'files': ('坏文件.pdf', b'%PDF-1.7\nbroken')}).status_code == 200
    # 至少一份已完成即可进入确认；解析队列未完成时发送仍受阻。
    assert c.post(base + '/proceed', headers=h, json=BODY).status_code == 200
    assert c.post(base + '/runs', headers=h).json()['code'] == 'FILES_PARSING'
    drain(db_sessionmaker, base)
    files = c.get(base).json()['files']
    assert [f['parse_state'] for f in files] == ['done', 'failed']
    assert c.post(base + '/runs', headers=h).json()['code'] == 'FILE_PARSE_FAILED'
    assert c.delete(base + '/files/' + files[1]['id'], headers=h).status_code == 200
    drain(db_sessionmaker, base)
    assert c.post(base + '/runs', headers=h).status_code == 201


def test_local_proceed_clears_all_redaction(owner, db_sessionmaker):
    c, h, _ = owner
    base = create(c, h)
    assert c.post(base + '/proceed', headers=h, json={**BODY, 'policy': 'local_only'}).json()['code'] == 'FILES_NOT_READY'
    drain(db_sessionmaker, base)
    result = c.post(base + '/proceed', headers=h, json={**BODY, 'policy': 'local_only'})
    assert result.status_code == 200 and result.json()['status'] == 'completed_local', result.text
    with db_sessionmaker() as db:
        tid = uuid.UUID(result.json()['id'])
        assert all(db.scalar(select(model.task_id).where(model.task_id == tid)) is None for model in (Party, RedactionItem, RedactionMap))
        assert all(text is None for text in db.scalars(select(DocNode.redacted_text).where(DocNode.task_id == tid)))
        assert db.get(Task, tid).last_completed_at is not None
