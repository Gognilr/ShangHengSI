"""虚构报表的真实审核链路与模拟网关；核对新增比率的外发与回执。"""
import json

from sqlalchemy import select

from app.admin.settings_store import set_setting
from app.db.models import LlmCall, Task
from app.redaction.recheck import recheck, recheck_context
from app.review import job, service
from tests.review_support import env, ready_task, llm  # noqa: F401
from tests.review_support import sample
from tests.test_checks_finance import fixture


def test_ratios_toggle_and_actual_receipt(make_user, ready_task, db_sessionmaker, llm):
    owner = make_user('ratios-review')
    task = ready_task(owner, [('虚构合同.docx', sample('contract_basic')), ('虚构财务.xlsx', fixture.make_workbook(False))])
    llm('normal')
    with db_sessionmaker() as db:
        task = db.get(Task, task.id)
        def user_message():
            from app.review.prompt import build_messages
            ctx = service.build_context(db, task, stance=task.stance, source=task.source, user_note=None, main_doc_type='contract')
            return build_messages(ctx)[1]['content']
        assert '【本地财务比率】' not in user_message()
        set_setting(db, 'review.send_ratios', True, owner.id)
        assert '【本地财务比率】' in user_message()
        assert '毛利率：本期 40.0%' in user_message()
        db.commit()
        run = service.create_run(db, task, owner, None)
        job.run_review(db, run.id)
        db.refresh(run)
        assert run.receipt['ratios_sent'] is True
        call = db.scalar(select(LlmCall).where(LlmCall.run_id == run.id, LlmCall.status == 'sent'))
        assert call is not None
        ctx = recheck_context(db, task.id)
        assert recheck(call.request_text, real_values=ctx.real_values, terms=ctx.terms, placeholders=ctx.placeholders, kept=ctx.kept) == []
        messages = json.loads(call.request_text)['messages']
        assert any('【本地财务比率】' in m['content'] for m in messages if m['role'] == 'user')
