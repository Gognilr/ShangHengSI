"""B-06：移除向量推理后，只读兼容旧任务的保留标记。"""
import json

from sqlalchemy import delete, select

from app.db.models import NerNodeResult, RedactionItem, RedactionSuspect, Task
from app.redaction import engine
from app.security import crypto
from tests.test_keep_terms import no_ner, task_with_word, WORD  # noqa: F401
from tests.review_support import env, ready_task  # noqa: F401


def test_legacy_keep_survives_read_and_is_replaced_on_recompute(
    env, no_ner, ready_task, make_user, db_sessionmaker, monkeypatch,
):
    task = task_with_word(ready_task, make_user('legacy-vector'))
    with db_sessionmaker() as db:
        row = db.scalar(select(RedactionSuspect).where(RedactionSuspect.task_id == task.id))
        node = db.scalar(select(NerNodeResult).where(NerNodeResult.task_id == task.id, NerNodeResult.loc == row.loc))
        # 使用真实节点缓存格式，模拟升级前已被向量辅助保留的位置。
        cache = json.loads(crypto.decrypt_text(node.data_enc))
        cache['vector_kept'] = [[row.char_start, row.char_end]]
        node.data_enc = crypto.encrypt_text(json.dumps(cache))
        db.execute(delete(RedactionItem).where(RedactionItem.task_id == task.id,
                                              RedactionItem.loc == row.loc))
        current = db.get(Task, task.id)
        assert next(v for v in engine.suspect_views(db, current) if v['text'] == WORD)['kept_by'] == 'vector'
        with monkeypatch.context() as patch:
            patch.setattr(engine, '_run', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('unexpected recompute')))
            engine.ensure_default_replacements(db, current)
        existing = engine._existing_parties(db, current)
        engine._run(db, current, engine._current_overrides(existing), existing)
        assert next(v for v in engine.suspect_views(db, current) if v['text'] == WORD)['kept_by'] is None
        assert engine._vector_kept_spans(db, current) == set()
