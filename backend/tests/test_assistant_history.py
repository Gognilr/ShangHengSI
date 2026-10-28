"""T-51：历史检索只限本人、代号迁移及来源出口复检。"""
import json
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.db.models import Task, TaskFile, DocNode, TaskClauseVector, LlmCall
from app.redaction import engine
from app.security import crypto
from tests.review_support import env  # noqa: F401
from tests.test_assistant_chat import conversation  # noqa: F401
from tests.test_kb import vector


def sources(db, task, other):
    ids = []
    for kind, owner, deleted in [('review', task.owner_id, False), ('review', other.id, False),
                                 ('assistant', task.owner_id, False), ('review', task.owner_id, True)]:
        source = Task(kind=kind, owner_id=owner, title='虚构秘密标题', stance='sales', source='incoming',
                      policy='ai', status='reviewed' if kind == 'review' else 'active',
                      created_at=datetime(2026, 5, 12, tzinfo=timezone.utc))
        db.add(source); db.flush()
        f = TaskFile(task_id=source.id, seq=1, is_main=True, original_name='秘密历史.docx', ext='docx',
                     size_bytes=1, sha256='1'*64, storage_key=uuid.uuid4().hex, doc_type='contract',
                     original_deleted_at=datetime.now(timezone.utc) if deleted else None)
        db.add(f); db.flush()
        db.add(DocNode(task_id=source.id, file_id=f.id, loc='F1-P1', ord=1, kind='paragraph', part='body',
                       text_enc=crypto.encrypt_text('虚构来源买方按约验收交付物。')))
        engine.write_full_map(db, source.id, {'客户_A': '虚构来源买方'})
        db.add(TaskClauseVector(task_id=source.id, file_seq=1, first_loc='F1-P1', locs=['F1-P1'],
                                embedding=vector(), model='test'))
        ids.append(source.id)
    db.commit()
    return ids


def test_t51_7_8_9_history_owner_codes_and_labels(conversation, db_sessionmaker, make_user, monkeypatch, caplog):
    from app.assistant.history import retrieve
    from app.embedding import engine as embedding
    monkeypatch.setattr(embedding, 'embed', lambda texts, kind: [vector() for _ in texts])
    other = make_user('history_other')
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(conversation[2]))
        ids = sources(db, task, other)
        with caplog.at_level('INFO'):
            items = retrieve(db, task, '查询敏感原文')
        assert len(items) == 1 and items[0]['task_id'] == str(ids[0])
        assert items[0]['label'] == '合同，2026-05'
        assert '[[ORG_' in items[0]['text']
        for secret in ('虚构来源买方', '秘密历史', '虚构秘密标题', '客户_A'):
            assert secret not in str(items)
        assert '虚构来源买方' in engine.load_redaction_map(db, task.id).values()
        assert '查询敏感原文' not in caplog.text


def test_t51_8_source_extra_context_blocks_injection(conversation, db_sessionmaker, make_user, monkeypatch):
    from app.llm import gateway
    from app.assistant.history import retrieve
    from app.embedding import engine as embedding
    from tests.test_gateway_stream import Events
    monkeypatch.setattr(embedding, 'embed', lambda texts, kind: [vector() for _ in texts])
    requests = []
    monkeypatch.setattr(gateway, 'TRANSPORT', httpx.MockTransport(lambda req: requests.append(req) or httpx.Response(200, stream=Events(['完成']))))
    other = make_user('history_other')
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(conversation[2]))
        ids = sources(db, task, other)
        # 故意跳过引用脱敏：来源映射必须独立阻断，不能依赖会话映射已包含它。
        import pytest
        with pytest.raises(gateway.GatewayError) as exc:
            gateway.stream_model(db, purpose='assist', task_id=task.id, user_id=task.owner_id,
                                 messages=[{'role': 'user', 'content': '虚构来源买方'}], on_delta=lambda _: True,
                                 extra_task_ids=[ids[0]])
        assert exc.value.code == 'BLOCKED'
        assert requests == []
        call = db.scalar(select(LlmCall))
        assert call.status == 'blocked' and call.request_text is None


def test_t51_worker_references_outbound_and_extras(conversation, db_sessionmaker, make_user, monkeypatch):
    from app.db.models import AssistantMessage, KbDoc, KbChunk
    from app.assistant.service import _queue_reply
    from app.assistant.job import handle_assist
    from app.admin.settings_store import set_setting
    from app.embedding import engine as embedding
    from tests.test_assistant_chat import install, REAL
    monkeypatch.setattr(embedding, 'embed', lambda texts, kind: [vector() for _ in texts])
    requests = install(monkeypatch, ['[[ORG_1]]按制度处理'])
    other = make_user('history_other')
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(conversation[2]))
        ids = sources(db, task, other)
        set_setting(db, 'assistant.kb_enabled', True, None)
        doc = KbDoc(name_enc=crypto.encrypt_text('虚构秘密知识库文件名'), status='ready',
                    content_hash='9'*64, created_by=task.owner_id)
        db.add(doc); db.flush()
        db.add(KbChunk(doc_id=doc.id, seq=1, text_enc=crypto.encrypt_text(REAL+'应核对验收记录。'), embedding=vector()))
        user = AssistantMessage(task_id=task.id, role='user', status='done', content_redacted='说明验收要求',
                                params={'use_kb': True, 'use_history': True})
        db.add(user); db.flush()
        reply = _queue_reply(db, task, user)
        mid = reply.id
        db.commit()
        handle_assist(db, {'task_id': str(task.id), 'message_id': mid})
        db.refresh(reply)
        assert reply.status == 'done' and reply.extras['kb_sent'] == 1 and reply.extras['kb_dropped'] == 0
        assert reply.extras['history_sources'] == [{'task_id': str(ids[0]), 'month': '2026-05'}]
    outbound = json.dumps(json.loads(requests[0].content)['messages'], ensure_ascii=False)
    assert '【公司知识库片段】[KB1]' in outbound and '【我的历史合同条款】[H1] 合同，2026-05' in outbound
    for secret in (REAL, '虚构来源买方', '秘密历史', '虚构秘密标题', '虚构秘密知识库文件名'):
        assert secret not in outbound


def test_history_not_selected_no_engine(conversation, db_sessionmaker, monkeypatch):
    from app.assistant.history import prepare
    from app.db.models import AssistantMessage
    from app.embedding import engine as embedding
    import pytest
    monkeypatch.setattr(embedding, 'embed', lambda *args: pytest.fail('未勾选参考资料不应计算向量'))
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(conversation[2]))
        refs, extras = prepare(db, task, AssistantMessage(content_redacted='普通问题', params={}))
        assert refs == {'kb': [], 'history': []} and extras['kb_sent'] == 0


def test_reference_choices_public_send_and_retry(conversation, db_sessionmaker, monkeypatch):
    from app.db.models import AssistantMessage
    from app.assistant.job import handle_assist
    from app.embedding import engine as embedding
    from tests.test_assistant_chat import BASE, install
    c, h, tid, _ = conversation
    monkeypatch.setattr(embedding, 'embed', lambda texts, kind: [vector() for _ in texts])
    response = c.post(f'{BASE}/{tid}/preview', headers=h,
                      json={'message': '核对验收要求', 'use_kb': True, 'use_history': True})
    assert response.status_code == 200
    response = c.post(f'{BASE}/{tid}/messages', headers=h,
                      json={'message': '核对验收要求', 'use_kb': True, 'use_history': True})
    assert response.status_code == 201, response.text
    mid = response.json()['assistant']['id']
    install(monkeypatch, ['片段'], httpx.ReadTimeout('fictional'))
    with db_sessionmaker() as db:
        row = db.get(AssistantMessage, mid)
        assert row.params['use_kb'] is True and row.params['use_history'] is True
        handle_assist(db, {'task_id': tid, 'message_id': mid})
        db.refresh(row)
        assert row.status == 'failed'
    retry = c.post(f'{BASE}/{tid}/messages/{mid}/retry', headers=h)
    assert retry.status_code == 201, retry.text
    assert retry.json()['assistant']['params']['use_kb'] is True
    assert retry.json()['assistant']['params']['use_history'] is True


def test_nonstream_source_extra_context_blocks(conversation, db_sessionmaker, make_user, monkeypatch):
    import pytest
    from app.llm import gateway
    other = make_user('nonstream_other')
    requests = []
    monkeypatch.setattr(gateway, 'TRANSPORT', httpx.MockTransport(lambda req: requests.append(req) or httpx.Response(503)))
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(conversation[2]))
        ids = sources(db, task, other)
        with pytest.raises(gateway.GatewayError) as exc:
            gateway.call_model(db, purpose='assist', task_id=task.id, user_id=task.owner_id,
                               messages=[{'role': 'user', 'content': '虚构来源买方'}],
                               extra_task_ids=[ids[0]], json_mode=True)
        assert exc.value.code == 'BLOCKED' and requests == []


def test_compare_public_history_checks_sources(conversation, db_sessionmaker, make_user, monkeypatch):
    from app.llm import gateway
    from app.embedding import engine as embedding
    from app.db.models import AssistantMessage
    from app.assistant.job import handle_assist
    from tests.test_assistant_chat import BASE
    from tests.test_assistant_compare import compare_input, data
    c, h, tid, _ = conversation
    body = compare_input(db_sessionmaker, tid)
    body['use_history'] = True
    other = make_user('compare_other')
    with db_sessionmaker() as db:
        task = db.get(Task, uuid.UUID(tid))
        ids = sources(db, task, other)
    monkeypatch.setattr(embedding, 'embed', lambda texts, kind: [vector() for _ in texts])
    seen = []
    original = gateway.call_model
    def checked(*args, **kwargs):
        seen.extend(kwargs.get('extra_task_ids', ()))
        return original(*args, **kwargs)
    monkeypatch.setattr(gateway, 'call_model', checked)
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(data())}}]})
    monkeypatch.setattr(gateway, 'TRANSPORT', httpx.MockTransport(handle))
    response = c.post(f'{BASE}/{tid}/messages', headers=h, json=body)
    assert response.status_code == 201, response.text
    mid = response.json()['assistant']['id']
    with db_sessionmaker() as db:
        handle_assist(db, {'task_id': tid, 'message_id': mid})
        assert db.get(AssistantMessage, mid).status == 'done'
    assert seen == [ids[0]] and len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload['stream'] is False and '【我的历史合同条款】[H1]' in str(payload)
    assert '虚构来源买方' not in str(payload)
