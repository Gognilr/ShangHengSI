"""V31-S-03：术语记忆代表用例，样本全为虚构，模型不外发。"""
import pytest
from sqlalchemy import select

from app.db.models import KeepTerm, RedactionSuspect, RedactionTerm, Task
from app.main import API_PREFIX
from app.redaction import engine, keep_terms, parties
from app.security import crypto
from tests.review_support import env, ready_task, REAL_OUR_NAME  # noqa: F401
from tests.test_redaction_api import suspect_task

WORD = 'Recarburizer Blend'
ADMIN = API_PREFIX + '/admin/keep-terms'
pytestmark = pytest.mark.usefixtures('env')


@pytest.fixture(autouse=True)
def no_ner(monkeypatch):
    from app.redaction import ner
    monkeypatch.setattr(ner, 'recognize', lambda text: [])


def task_with_word(ready_task, user):
    return suspect_task(ready_task, user, '销售确认书', f'卖方：{REAL_OUR_NAME}', f'工艺采用 {WORD}。')


def retain(ready_task, user, login):
    c, csrf = login(user.username)
    headers = {'X-CSRF-Token': csrf}
    task = task_with_word(ready_task, user)
    base = f'{API_PREFIX}/tasks/{task.id}'
    item = next(s for s in c.get(base + '/redaction').json()['suspects'] if s['text'] == WORD)
    r = c.post(base + f"/redaction/suspects/{item['id']}", headers=headers, json={'decision': 'ignore'})
    assert r.status_code == 200, r.text
    r = c.post(base + '/runs', headers=headers)
    assert r.status_code == 201, r.text
    return c, headers


def test_personal_memory_applies_and_counts_only_parsing(make_user, login, ready_task, db_sessionmaker):
    user = make_user('personal')
    c, headers = retain(ready_task, user, login)
    next_task = task_with_word(ready_task, user)
    base = f'{API_PREFIX}/tasks/{next_task.id}'
    view = c.get(base + '/redaction').json()['suspects'][0]
    assert view['kept_by'] == 'memory' and view['placeholder'] is None
    with db_sessionmaker() as db:
        row = db.scalar(select(KeepTerm))
        assert crypto.decrypt_text(row.term_enc) == WORD and WORD.encode() not in row.term_enc
        assert row.hit_count == 1 and row.last_hit_at is not None
        assert parties.normalize(WORD) not in keep_terms.effective(db, make_user('unrelated').id)
    response = c.post(base + f"/redaction/suspects/{view['id']}", json={'decision': 'reset'}, headers=headers)
    assert response.status_code == 200
    with db_sessionmaker() as db:
        assert db.scalar(select(KeepTerm.hit_count)) == 1


def test_two_users_share_and_admin_can_approve_delete(make_user, login, ready_task, db_sessionmaker):
    first, second, third = [make_user(name) for name in ('first', 'second', 'third')]
    c, headers = retain(ready_task, first, login)
    assert c.get(ADMIN).status_code == 403
    retain(ready_task, second, login)
    with db_sessionmaker() as db:
        assert parties.normalize(WORD) in keep_terms.effective(db, third.id)
    make_user('biz', role='biz_admin')
    admin, csrf = login('biz')
    ah = {'X-CSRF-Token': csrf}
    item = admin.get(ADMIN, params={'q': WORD}).json()['items'][0]
    assert item['users'] == 2 and item['status'] == '已共享'
    path = ADMIN + '/' + item['term_hash']
    assert admin.post(path + '/approve', headers=ah).status_code == 200
    assert admin.get(ADMIN).json()['items'][0]['status'] == '已认可'
    assert admin.delete(path, headers=ah).status_code == 204
    with db_sessionmaker() as db:
        assert list(db.scalars(select(KeepTerm))) == []


def test_person_and_company_are_not_remembered(make_user, ready_task, db_sessionmaker):
    user = make_user('names')
    text = '示例机构有限公司；陆虚岚'
    task = ready_task(user, user_note=text)
    with db_sessionmaker() as db:
        db.add_all([RedactionSuspect(task_id=task.id, loc='NOTE', char_start=0, char_end=8,
                                   reason='before_company', decision='ignore'),
                    RedactionSuspect(task_id=task.id, loc='NOTE', char_start=9, char_end=12,
                                   reason='person_name', decision='ignore')])
        db.flush()
        keep_terms.remember(db, db.get(Task, task.id), user.id)
        assert list(db.scalars(select(KeepTerm))) == []


def test_sensitive_term_overrides_memory_and_replace_forgets_only_personal(make_user, login, ready_task, db_sessionmaker):
    user = make_user('override')
    c, headers = retain(ready_task, user, login)
    with db_sessionmaker() as db:
        term = RedactionTerm(kind='custom', category='CUSTOM', term_hash=engine.term_hash(WORD),
                             term_enc=crypto.encrypt_text(WORD))
        db.add(term)
        db.commit()
        tid = term.id
    task = task_with_word(ready_task, user)
    assert WORD not in c.get(f'{API_PREFIX}/tasks/{task.id}/redaction/outbound').json()['text']
    with db_sessionmaker() as db:
        assert not keep_terms.effective(db, user.id)
        db.get(RedactionTerm, tid).enabled = False
        db.commit()
    task = task_with_word(ready_task, user)
    base = f'{API_PREFIX}/tasks/{task.id}'
    item = c.get(base + '/redaction').json()['suspects'][0]
    assert item['kept_by'] == 'memory'
    response = c.post(base + f"/redaction/suspects/{item['id']}", headers=headers,
                      json={'decision': 'replace', 'category': 'TERM'})
    assert response.status_code == 200, response.text
    with db_sessionmaker() as db:
        assert list(db.scalars(select(KeepTerm))) == []

