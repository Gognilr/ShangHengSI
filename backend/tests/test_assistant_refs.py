"""T-51：知识库参考的外发、编号与依据；参考资料选择随发送与重试保存。"""
import json
import uuid

import httpx
from sqlalchemy import select

from app.db.models import Task, TaskFile, LlmCall, LlmModel
from app.security import crypto
from tests.review_support import env  # noqa: F401
from tests.test_assistant_chat import conversation  # noqa: F401
from tests.test_kb import vector


def test_t51_worker_references_outbound_and_extras(conversation, db_sessionmaker, monkeypatch):
    from app.db.models import AssistantMessage, KbDoc, KbChunk
    from app.assistant.service import _queue_reply
    from app.assistant.job import handle_assist
    from app.admin.settings_store import set_setting
    from app.embedding import engine as embedding
    from tests.test_assistant_chat import install, REAL, BASE
    monkeypatch.setattr(embedding, 'embed', lambda texts, kind: [vector() for _ in texts])
    requests = install(monkeypatch, ['[[ORG_1]]按制度处理[KB1][KB7]，见[F1-P1]'])
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(conversation[2]))
        set_setting(db, 'assistant.kb_enabled', True, None)
        doc = KbDoc(name_enc=crypto.encrypt_text('虚构秘密知识库文件名'), status='ready',
                    content_hash='9'*64, created_by=task.owner_id)
        db.add(doc); db.flush()
        doc_id = doc.id
        db.add(KbChunk(doc_id=doc.id, seq=1, text_enc=crypto.encrypt_text(REAL+'应核对验收记录。'), embedding=vector()))
        user = AssistantMessage(task_id=task.id, role='user', status='done', content_redacted='说明验收要求',
                                params={'use_kb': True})
        db.add(user); db.flush()
        reply = _queue_reply(db, task, user, db.get(LlmModel, 1))
        mid = reply.id
        db.commit()
        handle_assist(db, {'task_id': str(task.id), 'message_id': mid})
        db.refresh(reply)
        assert reply.status == 'done' and reply.extras['kb_sent'] == 1 and reply.extras['kb_dropped'] == 0
        assert [r['ref'] for r in reply.extras['refs']] == ['KB1']
    # 引用只认本地登记的编号（V321-E-13）：[KB7] 未登记，不成锚点；依据原文只放行本会话本条回复
    c, h, tid, _ = conversation
    view = next(m for m in c.get(f'{BASE}/{tid}/messages', headers=h).json()['items'] if m['id'] == mid)
    assert {x['token']: x['ref'] for x in view['cites']} == {'[KB1]': 'KB1', view['sources'][0]['label']: 'F1-P1'}
    assert [(x['ref'], x['cited']) for x in view['sources']] == [('F1-P1', True), ('KB1', True)]
    assert '[F1-P1]' not in view['content'] and '[KB7]' in view['content']
    detail = c.get(f'{BASE}/{tid}/messages/{mid}/sources/KB1', headers=h).json()
    assert detail['title'] == '虚构秘密知识库文件名' and REAL in detail['text']
    assert c.get(f'{BASE}/{tid}/messages/{mid}/sources/KB7', headers=h).status_code == 404
    outbound = json.dumps(json.loads(requests[0].content)['messages'], ensure_ascii=False)
    assert '【公司知识库片段】[KB1]' in outbound
    for secret in (REAL, '虚构秘密知识库文件名'):
        assert secret not in outbound
    # 第二轮（V33-B-02）：编号在会话内固定，同一片段沿用 KB1、新片段往后编；全角括号段落号也换成位置
    requests = install(monkeypatch, ['本轮[KB1][KB2]，见【F1-P1】'])
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        db.add(KbChunk(doc_id=doc_id, seq=2, text_enc=crypto.encrypt_text('虚构第二段制度。'), embedding=vector()))
        user = AssistantMessage(task_id=task.id, role='user', status='done', content_redacted='再核对一次',
                                params={'use_kb': True})
        db.add(user); db.flush()
        mid2 = _queue_reply(db, task, user, db.get(LlmModel, 1)).id
        db.commit()
        handle_assist(db, {'task_id': tid, 'message_id': mid2})
    view = next(m for m in c.get(f'{BASE}/{tid}/messages', headers=h).json()['items'] if m['id'] == mid2)
    assert [(x['ref'], x['cited']) for x in view['sources']] == [('F1-P1', True), ('KB1', True), ('KB2', True)]
    assert '【F1-P1】' not in view['content']
    outbound = json.dumps(json.loads(requests[0].content)['messages'], ensure_ascii=False)
    assert '【公司知识库片段】[KB1]' in outbound and '【公司知识库片段】[KB2]' in outbound


def test_kb_not_selected_no_engine(conversation, db_sessionmaker, monkeypatch):
    from app.assistant.refs import prepare
    from app.db.models import AssistantMessage
    from app.embedding import engine as embedding
    import pytest
    monkeypatch.setattr(embedding, 'embed', lambda *args: pytest.fail('未勾选参考资料不应计算向量'))
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(conversation[2]))
        refs, extras = prepare(db, task, AssistantMessage(content_redacted='普通问题', params={}))
        assert refs == {'kb': []} and extras['kb_sent'] == 0


def test_reference_choices_public_send_and_retry(conversation, db_sessionmaker, monkeypatch):
    from app.db.models import AssistantMessage
    from app.assistant.job import handle_assist
    from app.embedding import engine as embedding
    from tests.test_assistant_chat import BASE, install
    c, h, tid, _ = conversation
    monkeypatch.setattr(embedding, 'embed', lambda texts, kind: [vector() for _ in texts])
    response = c.post(f'{BASE}/{tid}/preview', headers=h,
                      json={'message': '核对验收要求', 'use_kb': True})
    assert response.status_code == 200
    response = c.post(f'{BASE}/{tid}/messages', headers=h,
                      json={'message': '核对验收要求', 'use_kb': True})
    assert response.status_code == 201, response.text
    mid = response.json()['assistant']['id']
    install(monkeypatch, ['片段'], httpx.ReadTimeout('fictional'))
    with db_sessionmaker() as db:
        row = db.get(AssistantMessage, mid)
        assert row.params['use_kb'] is True
        handle_assist(db, {'task_id': tid, 'message_id': mid})
        db.refresh(row)
        assert row.status == 'failed'
    retry = c.post(f'{BASE}/{tid}/messages/{mid}/retry', headers=h)
    assert retry.status_code == 201, retry.text
    assert retry.json()['assistant']['params']['use_kb'] is True


def test_translation_waiting_requires_known_cloud_mode(conversation, db_sessionmaker):
    from app.db.models import AssistantMessage, Job
    from tests.test_assistant_chat import BASE
    c, h, tid, _ = conversation
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        task.purpose, task.status = 'translate', 'awaiting_confirm'
        file = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id))
        file.confirmed_at = None
        fid = str(file.id)
        db.add(AssistantMessage(task_id=task.id, role='assistant', status='stopped', template='translate',
                                params={}, content_redacted='虚构旧译文'))
        db.commit()
    unknown = c.get(BASE + '/history?type=translate').json()['items'][0]
    assert (unknown['translation_engine'], unknown['translation_status']) == (None, 'stopped')
    assert c.get(BASE + '/waiting').json()['translate'] == 0

    selected = c.patch(f'{BASE}/{tid}', headers=h, json={'translation_engine': 'cloud'})
    assert selected.status_code == 200, selected.text
    assert c.get(f'{BASE}/{tid}').json()['translation_engine'] == 'cloud'
    assert c.get(BASE + '/waiting').json()['translate'] == 1
    response = c.post(f'{BASE}/{tid}/messages', headers=h, json={
        'template': 'translate', 'params': {'file_id': fid, 'target': '英文', 'engine': 'cloud'}
    })
    assert response.status_code in (409, 422), response.text
    with db_sessionmaker() as db:
        assert not db.scalar(select(Job.id).where(Job.type.in_(('assist', 'translate'))))
        assert not db.scalar(select(LlmCall.id))
