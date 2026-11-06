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


def test_merged_paragraphs_in_outbound_word_and_speaker_batch(env, session_client, db_sessionmaker, monkeypatch):
    """V31-U5F-05：同一发言人间隔不足 30 秒的句子合并成段；发给模型的文字、复检用文字与 Word 同一份合并稿，脱敏仍按句。"""
    import io
    from docx import Document
    from app.assistant import meeting
    from app.redaction.recheck import recheck
    from app.assistant.router import _marks
    assert meeting.join_sentences(['你好。', '再见。']) == '你好。再见。'
    assert meeting.join_sentences(['Hello.', 'World']) == 'Hello. World'
    # 句末无标点时补逗号，不能拼出跨句的金额、人名
    assert meeting.join_sentences(['预算三百', '万元整。']) == '预算三百，万元整。'
    assert meeting.join_sentences(['Call John', 'Smith later']) == 'Call John, Smith later'
    assert recheck(meeting.join_sentences(['预算三百', '万元整。']), real_values=[], terms=[]) == []
    assert _marks('[3000, 1000, 1000]') == [1000, 3000] and _marks('[-1]') is None and _marks('x') is None
    c, headers, _ = session_client
    tid = uuid.UUID(create(c, headers)['id'])
    sentences = [('F1-U1', 1000, 4000, 1, '张伟发言，预算三十五万。'), ('F1-U2', 6000, 9000, 1, '同意这个方案。'),
                 ('F1-U3', 50000, 52000, 1, '会后再核对。'), ('F1-U4', 52500, 54000, 2, '好的。')]
    with db_sessionmaker() as db:
        key, _ = storage.save_original(b'OggS-fictional')
        file = TaskFile(task_id=tid, seq=1, is_main=True, original_name='虚构.wav', ext='wav', size_bytes=1, sha256='a'*64,
                        storage_key='b'*32, media_storage_key=key, parser_version='asr-fictional',
                        media={'duration_ms':60000, 'stage':'done', 'marks':[7000]})
        db.add(file); db.flush()
        fid = file.id
        for i, (loc, a, b, spk, text) in enumerate(sentences, 1):
            db.add(DocNode(task_id=tid, file_id=fid, loc=loc, ord=i, kind='utterance', part='body',
                           text_enc=crypto.encrypt_text(text), t_start_ms=a, t_end_ms=b, speaker=spk))
        db.get(Task, tid).status = 'awaiting_confirm'
        db.commit()
    url = f'{BASE}/{tid}'
    assert c.put(url+'/participants', headers=headers, json={'names':['张伟']}).status_code == 200
    assert c.put(url+f'/files/{fid}/speakers', headers=headers, json={'1':'张伟'}).status_code == 200
    with db_sessionmaker() as db:
        task = db.get(Task, tid)
        sent = engine.build_outbound_text(db, task)
        lines = sent.split('\n')
        assert len(lines) == 3 and lines[0].startswith('[F1-U1] ') and '[F1-U2]' not in sent
        assert lines[0].endswith('。同意这个方案。') and '张伟' not in sent and '三十五万' not in sent
        assert recheck(sent, real_values=engine.load_real_values(db, tid), terms=[],
                       placeholders=engine.load_placeholders(db, tid)) == []
    # 导出的逐字稿 Word 也是合并稿；没有被拆开的句子
    doc = Document(io.BytesIO(c.get(url+f'/files/{fid}/transcript').content))
    body = [p.text for p in doc.paragraphs if p.text.startswith('【')]
    assert body == ['【张伟 00:00:01】张伟发言，预算三十五万。同意这个方案。', '【张伟 00:00:50】会后再核对。', '【发言人2 00:00:52】好的。']
    # 一个段落一起改发言人：一次请求，不重算；改后 U1、U2 与 U3 发言人不同，不再并段
    r = c.post(url+f'/files/{fid}/utterances-speaker', headers=headers, json={'locs':['F1-U1', 'F1-U2'], 'speaker':2})
    assert r.status_code == 200, r.text
    items = c.get(url+f'/files/{fid}/nodes').json()['items']
    assert [n['speaker'] for n in items] == [2, 2, 1, 2]
    assert c.post(url+f'/files/{fid}/utterances-speaker', headers=headers, json={'locs':['F1-U9'], 'speaker':2}).status_code == 404
    assert c.get(url).json()['files'][0]['media']['marks'] == [7000]
