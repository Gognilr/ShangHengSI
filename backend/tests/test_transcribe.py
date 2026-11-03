"""M-04：模拟转写结果，检查节点、重试与本人隔离。"""
import uuid
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.models import DocNode, Task, TaskFile
from app.parsing import transcribe
from app.security import crypto
from app.tasks import storage
from tests.test_assistant_sessions import session_client, create, BASE, mock_ner  # noqa: F401
from tests.test_tasks_api import files_dir  # noqa: F401
from tests.review_support import env  # noqa: F401


def test_transcribe_nodes_retry_and_owner(files_dir, session_client, db_sessionmaker, monkeypatch, tmp_path, make_user, login):
    c, headers, _ = session_client
    tid = uuid.UUID(create(c, headers)['id'])
    monkeypatch.setenv('CONVERT_SPOOL_DIR', str(tmp_path))
    manifest = tmp_path / 'manifest'
    manifest.write_text('fictional manifest')
    monkeypatch.setenv('ASR_MANIFEST_FILE', str(manifest))
    get_settings.cache_clear()
    (tmp_path / 'transcriber.alive').touch()
    with db_sessionmaker() as db:
        key, _ = storage.save_original(b'OggS-fictional')
        file = TaskFile(task_id=tid, seq=1, is_main=True, original_name='虚构.wav', ext='wav',
                        size_bytes=1, sha256='a'*64, storage_key='b'*32, media_storage_key=key,
                        media={'duration_ms':5000, 'stage':'queued', 'progress':0, 'error':None})
        db.add(file)
        db.get(Task, tid).status = 'parsing'
        db.commit()
        fid = file.id
    pcm = tmp_path / 'asr/pending' / f'{fid.hex}.wav'
    pcm.parent.mkdir(parents=True)
    pcm.write_bytes(b'fictional PCM')
    output = {'duration_ms':5000, 'segments':[
        {'start_ms':2000, 'end_ms':4000, 'speaker':2, 'text':'同意方案。', 'low':[[0,1]]},
        {'start_ms':0, 'end_ms':1800, 'speaker':1, 'text':'开始讨论。', 'low':[]}]}
    monkeypatch.setattr(transcribe, 'exchange', lambda *a: output)
    try:
        with db_sessionmaker() as db:
            transcribe.handle_transcribe(db, {'task_id':str(tid), 'file_id':str(fid)})
            rows = list(db.scalars(select(DocNode).where(DocNode.file_id == fid).order_by(DocNode.ord)))
            assert [r.loc for r in rows] == ['F1-U1', 'F1-U2']
            assert crypto.decrypt_text(rows[1].text_enc) == '同意方案。'
            assert rows[1].low_conf_spans == [[0,1]] and rows[1].speaker == 2
            assert db.get(Task, tid).status == 'awaiting_confirm'
            assert db.get(TaskFile, fid).parser_version.startswith('asr-firered-')
            transcribe.handle_transcribe(db, {'task_id':str(tid), 'file_id':str(fid)})
        assert not pcm.exists()
        assert c.post(f'{BASE}/{tid}/files/{fid}/retranscribe', headers=headers).status_code == 409
        with db_sessionmaker() as db:
            file = db.get(TaskFile, fid)
            file.parser_version = None
            file.media = {**file.media, 'stage':'failed'}
            db.get(Task, tid).status = 'parse_failed'
            db.commit()
        make_user('transcribe_outsider')
        other, csrf = login('transcribe_outsider')
        assert other.post(f'{BASE}/{tid}/files/{fid}/retranscribe', headers={'X-CSRF-Token':csrf}).status_code == 404
        response = c.post(f'{BASE}/{tid}/files/{fid}/retranscribe', headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['files'][0]['media']['stage'] == 'queued'
    finally:
        get_settings.cache_clear()


def test_transcribe_unavailable(files_dir, session_client, db_sessionmaker, monkeypatch, tmp_path):
    import pytest
    c, headers, _ = session_client
    tid = uuid.UUID(create(c, headers)['id'])
    monkeypatch.setenv('CONVERT_SPOOL_DIR', str(tmp_path))
    get_settings.cache_clear()
    try:
        with db_sessionmaker() as db:
            file = TaskFile(task_id=tid, seq=1, is_main=True, original_name='虚构.wav', ext='wav',
                            size_bytes=1, sha256='a'*64, storage_key='b'*32,
                            media={'duration_ms':5000, 'stage':'queued'})
            db.add(file)
            db.commit()
            with pytest.raises(RuntimeError, match='unavailable'):
                transcribe.handle_transcribe(db, {'task_id':str(tid), 'file_id':str(file.id)})
            assert file.media['error'] == 'unavailable'
            assert db.get(Task, tid).status == 'parse_failed'
    finally:
        get_settings.cache_clear()
