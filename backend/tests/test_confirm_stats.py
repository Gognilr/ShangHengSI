"""V31-S-05：成功操作计数与本地汇总两个代表用例。"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import AuditLog, Task
from app.main import API_PREFIX
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_redaction_api import node_loc, suspect_task

pytestmark = pytest.mark.usefixtures('env')


def test_successful_operations_record_and_reset(make_user, login, ready_task, db_sessionmaker):
    user = make_user('stats_owner')
    c, csrf = login(user.username)
    h = {'X-CSRF-Token': csrf}
    task = suspect_task(ready_task, user)
    base = f'{API_PREFIX}/tasks/{task.id}'
    items = c.get(base + '/redaction').json()['suspects']
    ids = [v['id'] for v in items]
    for decision in ('ignore', 'reset'):
        response = c.post(base + '/redaction/suspects/batch', headers=h, json={'ids': ids, 'decision': decision})
        assert response.status_code == 200, response.text
    response = c.post(base + f"/redaction/suspects/{ids[0]}", headers=h, json={'decision': 'replace', 'category': 'TERM'})
    assert response.status_code == 200, response.text
    loc, start, end = node_loc(c, task, '另行通知装船日期')
    response = c.post(base + '/redaction/manual', headers=h,
                      json={'loc': loc, 'char_start': start, 'char_end': end, 'category': 'CUSTOM'})
    assert response.status_code == 200, response.text
    fid = c.get(base).json()['files'][0]['id']
    assert c.patch(base + '/files/' + fid, headers=h, json={'doc_type': 'contract'}).status_code == 200
    with db_sessionmaker() as db:
        row = db.get(Task, task.id)
        assert row.confirm_ops == 5 and row.awaiting_since is not None
        row.awaiting_since = utcnow() - timedelta(seconds=9)
        db.commit()
    response = c.post(base + '/runs', headers=h)
    assert response.status_code == 201, response.text
    with db_sessionmaker() as db:
        detail = db.scalar(select(AuditLog.detail).where(AuditLog.action == 'run_create'))['confirm']
        assert detail == {'ops': 5, 'seconds': detail['seconds'], 'uncertain': 2, 'kept_user': 0,
                          'kept_memory': 0, 'replaced_user': 1, 'manual': 1, 'files': 1}
        assert detail['seconds'] >= 9 and all(isinstance(v, int) for v in detail.values())
        row = db.get(Task, task.id)
        assert row.confirm_ops == 0 and row.awaiting_since is None


def test_admin_stats_ratios(make_user, login, db_sessionmaker):
    make_user('stats_biz', role='biz_admin')
    c, _ = login('stats_biz')
    with db_sessionmaker() as db:
        for ops, seconds, uncertain, user, memory in ((0, 10, 2, 1, 0), (4, 30, 6, 1, 2)):
            db.add(AuditLog(action='run_create', detail={'confirm': dict(ops=ops, seconds=seconds, uncertain=uncertain,
                       kept_user=user, kept_memory=memory, replaced_user=0, manual=0, files=1)}))
        db.add(AuditLog(action='run_create', detail={'run_no': 1}))  # 旧审计没有确认数据，不混入分母。
        db.commit()
    response = c.get(API_PREFIX + '/admin/redaction/stats', params={'days': 30})
    assert response.status_code == 200, response.text
    assert response.json() == dict(tasks=2, zero_ops_ratio=0.5, avg_ops=2, median_seconds=20,
                                   avg_uncertain=4, kept_ratio=0.5, memory_ratio=0.25,
                                   unable_ratio=0, unable_by_reason=dict(redacted=0, absent=0, other=0),
                                   redacted_top_requirements=[])
    assert c.get(API_PREFIX + '/admin/redaction/stats', params={'days': 0}).status_code == 422
