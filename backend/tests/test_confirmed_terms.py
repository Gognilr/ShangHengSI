"""V31-S-03a：三条确认记忆业务用例，全部为虚构数据。"""
import pytest
from sqlalchemy import select

from app.db.models import RedactionTerm, Task, RedactionItem
from app.redaction import engine, ner
from app.security import crypto
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_redaction_api import alice, suspect_task, url  # noqa: F401

pytestmark = pytest.mark.usefixtures('env')


@pytest.fixture(autouse=True)
def no_ner(monkeypatch):
    monkeypatch.setattr(ner, 'recognize', lambda text: [])


def test_project_confirmation_replaces_in_next_task(alice, ready_task, db_sessionmaker):
    user, c, h = alice
    word = '虚构远岚工程'
    first = suspect_task(ready_task, user, '卖方：星河虚构进出口有限公司', word)
    assert c.post(url(first, 'redaction/manual'), headers=h,
                  json={'loc': 'F1-P2', 'char_start': 0, 'char_end': len(word), 'category': 'PROJECT'}).status_code == 200
    assert c.post(url(first, 'runs'), headers=h).status_code == 201
    second = suspect_task(ready_task, user, '通知', word)
    with db_sessionmaker() as db:
        row = db.scalar(select(RedactionTerm).where(RedactionTerm.term_hash == engine.term_hash(word)))
        assert row.category == 'PROJECT' and crypto.decrypt_text(row.term_enc) == word
        item = db.scalar(select(RedactionItem).where(RedactionItem.task_id == second.id))
        assert item.category == 'PROJECT' and item.detected_by == 'term'


def test_alias_and_main_share_code_even_when_only_alias_appears(alice, ready_task, db_sessionmaker):
    user, c, h = alice
    name, alias = '星河虚构进出口有限公司', '虚构星河'
    first = suspect_task(ready_task, user, f'卖方：{name}（以下简称“{alias}”）', f'{alias}负责供货。')
    assert c.post(url(first, 'runs'), headers=h).status_code == 201
    second = suspect_task(ready_task, user, '通知', name, alias)
    third = suspect_task(ready_task, user, '通知', alias)
    with db_sessionmaker() as db:
        row = db.scalar(select(RedactionTerm).where(RedactionTerm.term_hash == engine.term_hash(alias)))
        assert row.alias_of == engine.term_hash(name)
        assert len(set(db.scalars(select(RedactionItem.placeholder).where(RedactionItem.task_id == second.id)))) == 1
        assert alias not in engine.build_outbound_text(db, db.get(Task, third.id))
    admin = c.get('/api/v1/admin/terms', params={'kind': 'learned'})
    # 普通用户仍无权浏览公司记忆。
    assert admin.status_code == 403


def test_common_and_short_numbers_are_not_learned(alice, ready_task, db_sessionmaker):
    user, _, _ = alice
    first = suspect_task(ready_task, user, '合同', '2023', '10%')
    with db_sessionmaker() as db:
        for loc, word, category in [('F1-P1', '合同', 'NAME'), ('F1-P2', '2023', 'REFNO'), ('F1-P3', '10%', 'TERM')]:
            db.add(RedactionItem(task_id=first.id, loc=loc, char_start=0, char_end=len(word), category=category,
                                 placeholder='[专有名词_1]', detected_by='user'))
        db.flush()
        engine.learn_confirmed_names(db, db.get(Task, first.id), user.id)
        assert list(db.scalars(select(RedactionTerm))) == []
