"""M-06：模板类型与两个 Word 导出；模型用模拟流。"""
import io
import json
import uuid

from docx import Document
from sqlalchemy import select

from app.db.models import DocNode, Task, TaskFile
from app.redaction import engine
from app.security import crypto
from tests.test_assistant_chat import conversation, install, run, BASE  # noqa: F401
from tests.review_support import env  # noqa: F401


def test_minutes_template_and_word_exports(conversation, db_sessionmaker, monkeypatch, make_user, login):
    c, headers, tid, _ = conversation
    with db_sessionmaker() as db:
        file = db.scalar(select(TaskFile).where(TaskFile.task_id == uuid.UUID(tid)))
        fid = str(file.id)
    body = {'template':'minutes', 'params':{'file_ids':[fid]}}
    assert c.post(f'{BASE}/{tid}/messages', headers=headers, json=body).status_code == 422
    with db_sessionmaker() as db:
        file = db.get(TaskFile, uuid.UUID(fid))
        file.ext, file.original_name = 'wav', '虚构会议.wav'
        file.media = {'stage':'done', 'duration_ms':5000}
        file.speakers_enc = crypto.encrypt_text(json.dumps({'1':'张伟'}, ensure_ascii=False))
        file.confirmed_at = None
        node = db.scalar(select(DocNode).where(DocNode.file_id == file.id))
        node.kind, node.loc = 'utterance', 'F1-U1'
        node.t_start_ms, node.t_end_ms, node.speaker = 1000, 4000, 1
        node.text_enc = crypto.encrypt_text('张伟说明讨论方案。')
        task = db.get(Task, uuid.UUID(tid))
        engine.redact_task(db, task, style='neutral', pin=True)
        task.status = 'awaiting_confirm'
        db.commit()
        code = next(k for k,v in engine.load_redaction_map(db, task.id).items() if v == '张伟')
    transcript_url = f'{BASE}/{tid}/files/{fid}/transcript?format=docx'
    before = c.get(transcript_url)
    assert before.status_code == 200, before.text
    doc = Document(io.BytesIO(before.content))
    assert '【张伟 00:00:01】张伟说明讨论方案。' in '\n'.join(p.text for p in doc.paragraphs)
    assert c.post(f'{BASE}/{tid}/confirm', headers=headers).status_code == 200
    content = ('## 会议概况\n时长00:00:05\n## 议题\n讨论方案\n## 讨论要点\n'+code+'说明方案\n'
               '## 决议\n继续核对\n## 待办\n| 事项 | 负责人 | 期限 |\n|---|---|---|\n| 核对 | '+code+' | 未明确 |\n'
               '## 需确认的事项\n期限未明确')
    requests = install(monkeypatch, chunks=(content,))
    response = c.post(f'{BASE}/{tid}/messages', headers=headers, json=body)
    assert response.status_code == 201, response.text
    mid = response.json()['assistant']['id']
    assert response.json()['user']['content'] == '把录音 1 整理为会议纪要'
    run(db_sessionmaker, tid)
    wire = requests[0].content.decode()
    assert '张伟' not in wire and '虚构会议.wav' not in wire
    assert '00:00:05' in wire and '不要改写代号' in wire
    response = c.get(f'{BASE}/{tid}/messages/{mid}/export?format=docx')
    assert response.status_code == 200, response.text
    doc = Document(io.BytesIO(response.content))
    assert '会议纪要' in [p.text for p in doc.paragraphs]
    assert doc.tables[0].cell(1,1).text == '张伟'
    assert c.get(transcript_url).status_code == 200
    make_user('minutes_other')
    other, _ = login('minutes_other')
    assert other.get(transcript_url).status_code == 404
