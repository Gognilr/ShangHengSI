"""T-44 10、11、15：条款边界、失败隔离与删除级联。"""
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import delete, select

from app.db.models import Task, TaskClauseVector
from app.embedding import engine
from app.review.clauses import first_level
from app.tasks import pipeline
from tests.test_pipeline import files_dir, make_task, sample  # noqa: F401


def test_first_level_boundaries():
    def node(loc, text, kind='paragraph', clause_no=None, part='body'):
        return SimpleNamespace(loc=loc, text=text, kind=kind, clause_no=clause_no, part=part)
    clauses = first_level([node('F1-H1', '页眉', part='header'), node('F1-P1', '前言'),
        node('F1-P2', '第一节', 'heading'), node('F1-P3', '约定', clause_no='1'),
        node('F1-P4', '内容', clause_no='1.1'), node('F2-P1', '附件')])
    assert [(c.file_seq, c.first_loc, c.title, c.text, c.locs) for c in clauses] == [
        (1, 'F1-P1', '前言', '前言', ['F1-P1']),
        (1, 'F1-P2', '第一节', '第一节', ['F1-P2']),
        (1, 'F1-P3', '约定', '约定\n内容', ['F1-P3', 'F1-P4']),
        (2, 'F2-P1', '附件', '附件', ['F2-P1'])]


@pytest.mark.parametrize('fails', [False, True])
def test_t44_10_pipeline_success_even_embedding_failure(db_sessionmaker, make_task, monkeypatch, fails, caplog):
    from app.review.service import chunk_boundaries
    task = make_task([('虚构合同.docx', sample('contract_basic'))], policy='local_only')
    texts = []
    def embed(values, kind):
        texts.extend(values)
        if fails:
            raise RuntimeError('私密异常内容')
        return np.tile(np.eye(1, 1024, dtype=np.float32), (len(values), 1))
    monkeypatch.setattr(engine, 'embed', embed)
    with db_sessionmaker() as db:
        # 重解析时失败也必须去掉旧缓存。
        db.add(TaskClauseVector(task_id=task.id, file_seq=1, first_loc='F1-P1', locs=['F1-P1'],
                               embedding=np.eye(1,1024)[0], model='old'))
        db.commit()
        pipeline.parse_task(db, task.id)
        assert db.get(Task, task.id).status == 'completed_local'
        rows = db.scalars(select(TaskClauseVector).where(TaskClauseVector.task_id == task.id)).all()
        if fails:
            assert rows == []
        else:
            assert len(rows) == len(chunk_boundaries(db, task.id))
            assert {r.first_loc for r in rows} == chunk_boundaries(db, task.id)
            assert all(r.model == engine.model_id() and r.first_loc == r.locs[0] for r in rows)
        assert all(len(t) <= 2000 for t in texts)
        db.execute(delete(Task).where(Task.id == task.id))
        db.commit()
        assert db.scalar(select(TaskClauseVector.id).limit(1)) is None
    assert '私密异常内容' not in caplog.text
    assert not any(t and t in caplog.text for t in texts)
