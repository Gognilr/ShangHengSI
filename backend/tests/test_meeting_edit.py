"""一份模拟逐字稿贯通名单、同音纠正、改名、改字与回放。"""
import uuid

from sqlalchemy import select

from app.db.models import DocNode, Task, TaskFile
from app.db.base import utcnow
from app.redaction import engine
from app.security import crypto
from app.tasks import storage
from tests.test_assistant_sessions import session_client, create, BASE, mock_ner  # noqa: F401
from tests.test_tasks_api import files_dir  # noqa: F401
from tests.review_support import env  # noqa: F401


def test_meeting_edit_and_outbound(env, session_client, db_sessionmaker, make_user, login, monkeypatch):
    from app.assistant.homophone import suggest
    assert suggest('章伟发言，往返车程。', ['张伟', '王芳']) == [{'start':0,'end':2,'from':'章伟','to':'张伟'}]
    c, headers, _ = session_client
    tid = uuid.UUID(create(c, headers)['id'])
    with db_sessionmaker() as db:
        key, _ = storage.save_original(b'OggS-fictional')
        file = TaskFile(task_id=tid, seq=1, is_main=True, original_name='虚构.wav', ext='wav',
                        size_bytes=1, sha256='a'*64, storage_key='b'*32, media_storage_key=key,
                        parser_version='asr-fictional', media={'duration_ms':5000, 'stage':'done'})
        db.add(file); db.flush()
        fid = file.id
        db.add(DocNode(task_id=tid, file_id=fid, loc='F1-U1', ord=1, kind='utterance', part='body',
                       text_enc=crypto.encrypt_text('章伟发言，预算三十五万。'), t_start_ms=1000, t_end_ms=4000, speaker=1))
        db.get(Task, tid).status = 'awaiting_confirm'
        db.commit()
    url = f'{BASE}/{tid}'
    assert c.get(url+f'/files/{fid}/nodes').json()['items'][0]['speaker_code'] == '发言人1'
    r = c.put(url+'/participants', headers=headers, json={'names':['张伟']})
    assert r.status_code == 200, r.text
    r = c.put(url+f'/files/{fid}/speakers', headers=headers, json={'1':'张伟'})
    assert r.status_code == 200, r.text
    values = c.get(url+f'/files/{fid}/suggestions').json()
    assert values[0]['to'] == '张伟'
    r = c.post(url+f'/files/{fid}/suggestions/apply', headers=headers,
               json={'items':[{k:v for k,v in values[0].items() if k != 'from'}]})
    assert r.status_code == 200, r.text
    r = c.patch(url+f'/files/{fid}/utterances/F1-U1', headers=headers, json={'speaker':2})
    assert r.status_code == 200, r.text
    c.put(url+f'/files/{fid}/speakers', headers=headers, json={'2':'张伟'})
    with db_sessionmaker() as db:
        task = db.get(Task, tid)
        sent = engine.build_outbound_text(db, task)
        assert '张伟' not in sent and '三十五万' not in sent
        assert '[[PER_' in sent and '00:00:01' in sent
        assert c.get(url+f'/files/{fid}/nodes').json()['items'][0]['speaker_code'] in sent
        from app.redaction.recheck import recheck
        assert recheck(sent, real_values=engine.load_real_values(db, tid), terms=[],
                       placeholders=engine.load_placeholders(db, tid)) == []
        assert crypto.decrypt_text(db.scalar(select(DocNode.text_enc).where(DocNode.file_id == fid))) == '张伟发言，预算三十五万。'
    r = c.get(url+f'/files/{fid}/audio')
    assert r.content == b'OggS-fictional' and r.headers['cache-control'] == 'no-store'
    make_user('meeting_outsider')
    other, csrf = login('meeting_outsider')
    for path in ('audio', 'suggestions', 'nodes'):
        assert other.get(url+f'/files/{fid}/{path}').status_code == 404
    response = c.post(url+'/confirm', headers=headers)
    assert response.status_code == 200, response.text
    from tests.test_assistant_chat import install, run
    requests = install(monkeypatch, chunks=('已整理讨论要点。',))
    response = c.post(url+'/messages', headers=headers, json={'message':'整理讨论要点', 'params':{'file_ids':[str(fid)]}})
    assert response.status_code == 201, response.text
    run(db_sessionmaker, str(tid))
    assert len(requests) == 1
    wire = requests[0].content.decode()
    assert all(value not in wire for value in ('张伟', '三十五万', '虚构.wav', 'OggS-fictional'))
    from app.db.models import LlmCall
    with db_sessionmaker() as db:
        call = db.scalar(select(LlmCall).where(LlmCall.task_id == tid))
        assert call.status == 'sent' and '张伟' not in call.request_text
    assert c.patch(url+f'/files/{fid}/utterances/F1-U1', headers=headers, json={'text':'修改'}).status_code == 409
    assert c.put(url+'/participants', headers=headers, json={'names':[]}).status_code == 409
