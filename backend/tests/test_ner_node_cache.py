"""V2Q-49：节点缓存纯函数等价、失效、加密持久化及生命周期。"""
import io
import json

import pytest
from docx import Document
from sqlalchemy import select, delete

from app.db import models
from app.redaction import engine, ner
from app.security import crypto
from tests.review_support import env, ready_task  # noqa: F401
from tests.test_redaction_api import alice, url  # noqa: F401
from tests.test_suspects import load


def business(result):
    return result.items, result.suspects, result.redacted, result.mapping


def test_golden_and_note_cache_are_identical(monkeypatch):
    nodes, annotation = load('persons_narrative')
    nodes.append(engine.NodeIn('NOTE', '沈柏宁复核新岛研究院的意见。'))
    original = engine.compute(nodes, annotation['stance'])
    def forbidden(_):
        raise AssertionError('缓存命中不得调用模型')
    monkeypatch.setattr(ner, 'recognize', forbidden)
    cached = engine.compute(nodes, annotation['stance'], ner_cache=original.ner_cache)
    assert business(original) == business(cached)
    assert original.ner_cache == cached.ner_cache


def test_same_compute_equal_keys_only_infer_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ner, 'recognize', lambda text: calls.append(text) or [])
    result = engine.compute([engine.NodeIn('F1-P1', '虚构记录需复核。'),
                             engine.NodeIn('NOTE', '虚构记录需复核。')], 'other')
    assert len(calls) == 1
    assert result.ner_cache['F1-P1'] == result.ner_cache['NOTE']


@pytest.mark.parametrize('changed', ['manifest', 'stopwords'])
def test_manifest_and_stopwords_invalidate_all_nodes(monkeypatch, tmp_path, changed):
    manifest, words = tmp_path / 'MANIFEST.sha256', tmp_path / 'ner_stopwords.txt'
    manifest.write_bytes(b'original manifest')
    words.write_bytes(b'original words')
    monkeypatch.setattr(ner, 'MODEL_DIR', tmp_path)
    # key辅助允许测试独立指定词表路径，不改变产品的既有词表加载与过滤。
    monkeypatch.setattr(ner, 'STOPWORDS_FILE', words)
    calls = []
    monkeypatch.setattr(ner, 'recognize', lambda text: calls.append(text) or [])
    nodes = [engine.NodeIn('F1-P1', '虚构记录。'), engine.NodeIn('NOTE', '另一虚构记录。')]
    old = engine.compute(nodes, 'other')
    (manifest if changed == 'manifest' else words).write_bytes(b'changed bytes')
    calls.clear()
    new = engine.compute(nodes, 'other', ner_cache=old.ner_cache)
    assert len(calls) == 2
    assert all(old.ner_cache[k]['key'] != new.ner_cache[k]['key'] for k in old.ner_cache)


def fixture_task(ready_task, user):
    doc = Document()
    for value in ('买方：江北市虚构医院', '卖方：澄川虚构信息技术有限公司',
                  '星际探针需要复核。', '联系人：周示例', '固定的虚构记录。'):
        doc.add_paragraph(value)
    data = io.BytesIO()
    doc.save(data)
    return ready_task(user, [('虚构缓存合同.docx', data.getvalue())], stance='purchase',
                      user_note='补充虚构记录。')


@pytest.mark.usefixtures('env')
def test_persisted_cache_hits_and_only_changed_node_recomputed(db_sessionmaker, alice, ready_task, monkeypatch):
    user, _, _ = alice
    calls = []
    def recognize(text):
        calls.append(text)
        return [ner.Entity(0, 4, 'ORG', 0.7)] if text.startswith('星际探针') else []
    monkeypatch.setattr(ner, 'recognize', recognize)
    task = fixture_task(ready_task, user)
    model = models.NerNodeResult
    with db_sessionmaker() as db:
        rows = list(db.scalars(select(model).where(model.task_id == task.id)))
        assert len(rows) == 6 and any(r.loc == 'NOTE' for r in rows)
        before = json.loads(crypto.decrypt_text(next(r for r in rows if r.loc == 'F1-P3').data_enc))
        assert set(before) == {'key', 'entities'}
        assert before['entities'] == [[0, 4, 'ORG', 0.7]]
        assert '星际探针' not in json.dumps(before, ensure_ascii=False)
        old = {r.loc: (r.data_enc, r.updated_at) for r in rows}
        calls.clear()
        current = db.get(models.Task, task.id)
        engine.apply_party_changes(db, current, [])
        assert calls == []
        assert {r.loc: (r.data_enc, r.updated_at) for r in rows} == old
        engine.add_user_term(db, current, loc='F1-P3', char_start=0, char_end=4, category='CUSTOM')
        assert len(calls) == 1
        assert calls[0].startswith(' ' * 4)
        payload = json.loads(crypto.decrypt_text(db.scalar(select(model.data_enc).where(
            model.task_id == task.id, model.loc == 'F1-P3'))))
        assert set(payload) == {'key', 'entities'} and len(payload['key']) == 64
        assert payload['entities'] == []
        assert '星际探针' not in json.dumps(payload, ensure_ascii=False)
        assert payload['key'].encode() not in db.scalar(select(model.data_enc).where(
            model.task_id == task.id, model.loc == 'F1-P3'))
        db.rollback()
        assert {r.loc: (r.data_enc, r.updated_at) for r in db.scalars(
            select(model).where(model.task_id == task.id))} == old
        current.user_note_enc = None
        engine.apply_party_changes(db, current, [])
        assert db.scalar(select(model).where(model.task_id == task.id, model.loc == 'NOTE')) is None
        db.commit()


@pytest.mark.usefixtures('env')
@pytest.mark.parametrize('bad', ['ciphertext', 'json', 'shape', 'bounds', 'score'])
def test_bad_cache_is_recomputed_without_content_logs(db_sessionmaker, alice, ready_task, monkeypatch, caplog, bad):
    user, _, _ = alice
    monkeypatch.setattr(ner, 'recognize', lambda _: [])
    task = fixture_task(ready_task, user)
    model = models.NerNodeResult
    with db_sessionmaker() as db:
        row = db.scalar(select(model).where(model.task_id == task.id, model.loc == 'F1-P3'))
        payload = json.loads(crypto.decrypt_text(row.data_enc))
        if bad in ('bounds', 'score'):
            payload['entities'] = [[0, 100000 if bad == 'bounds' else 2, 'PERSON',
                                     float('nan') if bad == 'score' else 0.7]]
        row.data_enc = (b'broken' if bad == 'ciphertext' else crypto.encrypt_text(
            'not json' if bad == 'json' else '{"key":42,"entities":"bad"}' if bad == 'shape' else json.dumps(payload)))
        db.commit()
        calls = []
        monkeypatch.setattr(ner, 'recognize', lambda text: calls.append(text) or [])
        engine.apply_party_changes(db, db.get(models.Task, task.id), [])
        assert len(calls) == 1
        assert set(json.loads(crypto.decrypt_text(row.data_enc))) == {'key', 'entities'}
        assert '星际探针' not in caplog.text and 'broken' not in caplog.text


@pytest.mark.usefixtures('env')
def test_cache_cascades_and_other_users_cannot_operate_task(db_sessionmaker, alice, ready_task, make_user, login):
    user, _, _ = alice
    task = fixture_task(ready_task, user)
    model = models.NerNodeResult
    make_user('other-cache-owner', role='admin')
    client, csrf = login('other-cache-owner')
    assert client.get(url(task, 'redaction')).status_code == 404
    assert client.put(url(task, 'parties'), headers={'X-CSRF-Token': csrf}, json={'items': []}).status_code == 404
    with db_sessionmaker() as db:
        assert db.scalar(select(model.id).where(model.task_id == task.id)) is not None
        db.execute(delete(models.Task).where(models.Task.id == task.id))
        db.commit()
        assert db.scalar(select(model.id).where(model.task_id == task.id)) is None
