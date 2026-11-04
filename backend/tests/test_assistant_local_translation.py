"""本地译稿代表链路：未确认原文、零网关调用、加密存储与后续云端历史隔离。"""
import io
import json
import uuid

from docx import Document
from sqlalchemy import func, select

from app.assistant import local_translation as mt, prompt
from app.assistant.local_translation import handle_translate
from app.db.models import AssistantMessage, Job, LlmCall, Task, TaskFile
from tests.test_assistant_chat import BASE, conversation, send  # noqa: F401
from tests.review_support import env  # noqa: F401


def test_local_translation_encrypted_and_excluded_from_cloud(conversation, db_sessionmaker, monkeypatch):
    c, h, tid, _ = conversation
    monkeypatch.setenv('COMPOSE_PROFILES', 'mt')
    monkeypatch.setattr(mt, 'alive', lambda: True)
    with db_sessionmaker() as db:
        file = db.scalar(select(TaskFile).where(TaskFile.task_id == uuid.UUID(tid)))
        file.confirmed_at, file.parser_version = None, 'docx-test'
        fid = str(file.id)
        db.get(Task, uuid.UUID(tid)).status = 'awaiting_confirm'
        db.commit()
    translated = 'Qinglan shall pay RMB 100000 after acceptance.'
    seen = []

    def exchange(text, target, cancelled):
        seen.append(text)
        assert not cancelled()
        return translated

    monkeypatch.setattr(mt, 'exchange', exchange)
    result = send(conversation, '', template='translate', params={'engine': 'local', 'file_id': fid, 'target': '英文'})
    assert result.status_code == 201, result.text
    mid = result.json()['assistant']['id']
    with db_sessionmaker() as db:
        job = db.scalar(select(Job).where(Job.type == 'translate'))
        handle_translate(db, job.payload)
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 0
        row = db.get(AssistantMessage, mid)
        assert row.content_redacted == '' and row.local_result_enc
        assert translated not in json.dumps(row.extras) and translated.encode() not in row.local_result_enc
    assert '虚构星河贸易有限公司' in seen[0] and '[[ORG_1]]' not in seen[0]
    view = c.get(f'{BASE}/{tid}/messages').json()['items'][-1]
    assert view['content'] == translated and view['translation']['F1-P1'] == translated
    assert view['status'] == 'done' and '不外发' in view['translation_notice']
    document = c.get(f'{BASE}/{tid}/messages/{mid}/export?format=docx&variant=bilingual')
    assert document.status_code == 200
    doc = Document(io.BytesIO(document.content))
    assert translated in '\n'.join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    followup = send(conversation, '说明术语')
    assert followup.status_code == 201, followup.text
    with db_sessionmaker() as db:
        source = db.get(AssistantMessage, followup.json()['user']['id'])
        messages = prompt.build_messages(db, db.get(Task, uuid.UUID(tid)), source)
        assert translated not in json.dumps(messages) and '100000' not in json.dumps(messages)
