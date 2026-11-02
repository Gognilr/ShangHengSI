"""E-05：原因兜底、分段合并、模拟审核到详情与本地统计。"""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import RequirementResult, ReviewRun
from app.main import API_PREFIX
from app.review import service
from app.review.validate import ChunkOutcome, merge_reviews, validate_output
from tests.review_support import env, llm, ready_task  # noqa: F401
from tests.test_review_job import custom_reply, new_run, run_job
from tests.test_review_validate import sample


def test_reason_defaults_and_merge():
    ctx, nodes = sample('contract_basic')
    rows = [dict(req_id=r.id, verdict='unable', unable_reason=reason)
            for r, reason in zip(ctx.requirements, ('redacted', 'absent', ['invalid']))]
    rows.append(dict(req_id=ctx.requirements[3].id, verdict='unable'))
    rows.append(dict(req_id=ctx.requirements[4].id, verdict='ok', unable_reason='redacted'))
    review = validate_output(json.dumps(dict(requirement_results=rows, findings=[])), ctx, nodes)
    assert [r.unable_reason for r in review.requirement_results[:6]] == ['redacted', 'absent', 'other', 'other', None, 'other']
    merged = merge_reviews(ctx, [ChunkOutcome(None, review)])
    assert [r.unable_reason for r in merged.requirement_results[:6]] == ['redacted', 'absent', 'other', 'other', None, 'other']


@pytest.mark.usefixtures('env')
def test_unable_persistence_detail_and_stats(make_user, login, ready_task, llm, db_sessionmaker):
    owner = make_user('unable_owner')
    client, _ = login(owner.username)
    task = ready_task(owner)

    def mutate(output, messages):
        for row, reason in zip(output['requirement_results'], ('redacted', 'absent', None, {'invalid': True})):
            row.update(verdict='unable', unable_reason=reason)
    llm(handler=custom_reply(mutate))
    run = new_run(db_sessionmaker, task, owner)
    run_job(db_sessionmaker, run.id)
    response = client.get(f'{API_PREFIX}/runs/{run.id}')
    assert response.status_code == 200
    detail = response.json()
    reasons = dict(redacted=1, absent=1, other=2)
    assert detail['coverage']['unable_by_reason'] == reasons
    assert '无法判断 4 条（信息已脱敏 1、文件未写 1、其他 2）' in detail['coverage']['summary']
    assert [r['unable_reason'] for r in detail['requirement_results'][:5]] == ['redacted', 'absent', 'other', 'other', None]
    with db_sessionmaker() as db:
        stored = list(db.scalars(select(RequirementResult).where(RequirementResult.run_id == run.id).order_by(RequirementResult.id)))
        assert [r.unable_reason for r in stored[:4]] == ['redacted', 'absent', 'other', 'other']
        total = len(stored)
        redacted_id = stored[0].req_id
        # 旧轮次无原因：详情归 other；超出近 N 天的结论不混入汇总。
        db.get(ReviewRun, run.id).queued_at = utcnow() - timedelta(days=31)
        stored[0].unable_reason = None
        db.commit()
        old = service.run_detail(db, db.get(ReviewRun, run.id))
        assert old.requirement_results[0].unable_reason == 'other'
    make_user('unable_biz', role='biz_admin')
    admin, _ = login('unable_biz')
    assert admin.get(API_PREFIX + '/admin/redaction/stats', params={'days': 30}).json()['unable_ratio'] == 0
    stats = admin.get(API_PREFIX + '/admin/redaction/stats', params={'days': 60}).json()
    assert stats['unable_ratio'] == 4 / total
    assert stats['unable_by_reason'] == dict(redacted=0, absent=1, other=3)
    assert stats['redacted_top_requirements'] == []
    with db_sessionmaker() as db:
        db.scalar(select(RequirementResult).where(RequirementResult.run_id == run.id, RequirementResult.req_id == redacted_id)).unable_reason = 'redacted'
        db.commit()
    stats = admin.get(API_PREFIX + '/admin/redaction/stats', params={'days': 60}).json()
    assert stats['unable_by_reason'] == reasons
    assert stats['redacted_top_requirements'] == [dict(req_id=redacted_id, count=1)]
    stranger = make_user('unable_stranger')
    other, _ = login(stranger.username)
    assert other.get(f'{API_PREFIX}/runs/{run.id}').status_code == 404
