"""T-44 4～7、12、15：全文预览、原子确认、向量续做。"""
import io
import uuid
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import func, select

from app.db.models import Job, Law, LawArticle, LawImport, LawImportArticle, LawImportFile
from app.laws import imports
from app.laws.job import handle_import, handle_embed, enqueue_embed
from app.embedding import engine
from app.security import crypto
from tests.review_support import env  # noqa: F401
from tests.test_law_articles import law_doc, _doc_without_articles

pytestmark = pytest.mark.usefixtures('env')


def archive(items):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in items:
            z.writestr(name, data)
    return buf.getvalue()


def seed(sm, items):
    with sm() as db:
        law = db.scalar(select(Law).where(Law.title == '示例法'))
        if law is None:
            law = Law(title='示例法', category='法律', status='valid', published_on=date(2020,1,1), source='import')
            db.add(law)
        row = LawImport(kind='fulltext', status='parsing', file_names=['全文.zip'])
        db.add(row)
        db.flush()
        iid, lid = row.id, law.id
        db.add(LawImportFile(import_id=iid, seq=1, name='全文.zip', data_enc=crypto.encrypt(archive(items))))
        db.commit()
        handle_import(db, {'import_id': str(iid)})
        return iid, lid


def test_t44_4_preview_matching(db_sessionmaker, caplog):
    iid, lid = seed(db_sessionmaker, [('示例法_20200101.docx', law_doc()),
        ('示例法_20200102.docx', law_doc()), ('说明.txt', b'example')])
    with db_sessionmaker() as db:
        row = db.get(LawImport, iid)
        assert row.status == 'pending' and row.added == 1 and row.changed == 0
        assert row.diff['matched'] == [dict(law_id=lid, title='示例法', published_on='2020-01-01', articles=3, replace=False)]
        assert len(row.diff['unmatched']) == 2
        assert db.scalar(select(func.count()).select_from(LawArticle)) == 0
        assert db.scalar(select(func.count()).select_from(LawImportArticle)) == 3
        assert db.scalar(select(func.count()).select_from(LawImportFile)) == 0
    assert '示例法' not in caplog.text and '示例适用范围' not in caplog.text


@pytest.mark.parametrize('path', ['../x.docx', '/x.docx', 'C:/x.docx', '..\\x.docx'])
def test_t44_4_unsafe_rejects_entire_archive(db_sessionmaker, path):
    iid, _ = seed(db_sessionmaker, [('示例法_20200101.docx', law_doc()), (path, law_doc())])
    with db_sessionmaker() as db:
        assert db.get(LawImport, iid).status == 'failed'
        assert db.scalar(select(func.count()).select_from(LawImportArticle)) == 0
        assert db.scalar(select(func.count()).select_from(LawImportFile)) == 0


def test_t44_5_doc_conversion(db_sessionmaker, monkeypatch):
    from app.parsing import legacy
    calls = []
    def convert(data, ext):
        calls.append(ext)
        return law_doc()
    monkeypatch.setattr(legacy, 'convert', convert)
    value = (Path(__file__).parent/'fixtures/legacy/contract_basic.doc').read_bytes()
    iid, _ = seed(db_sessionmaker, [('示例法_20200101.docx', value)])
    with db_sessionmaker() as db:
        assert db.get(LawImport, iid).status == 'pending'
        assert db.get(LawImport, iid).added == 1
    assert calls == ['doc']


def test_no_articles_falls_back_to_segments(db_sessionmaker):
    iid, lid = seed(db_sessionmaker, [('示例法_20200101.docx', _doc_without_articles())])
    with db_sessionmaker() as db:
        diff = db.get(LawImport, iid).diff
        assert diff['unmatched'] == []
        assert diff['matched'] == [dict(law_id=lid, title='示例法', published_on='2020-01-01', articles=1, replace=False)]


def test_zip_entry_limit_and_cleanup(db_sessionmaker):
    iid, _ = seed(db_sessionmaker, [(f'{n}.txt', b'fictional') for n in range(201)])
    with db_sessionmaker() as db:
        assert db.get(LawImport, iid).status == 'failed'
        assert db.get(LawImport, iid).error == '全文压缩包解压后超过限制'
        assert db.scalar(select(func.count()).select_from(LawImportArticle)) == 0
        assert db.scalar(select(func.count()).select_from(LawImportFile)) == 0


def test_t44_6_replace_and_discard(db_sessionmaker):
    iid, lid = seed(db_sessionmaker, [('示例法_20200101.docx', law_doc())])
    with db_sessionmaker() as db:
        db.add(LawArticle(law_id=lid, article_no='第一条', seq=1, text='旧条文'))
        db.commit()
        imports.apply(db, iid, None)
        db.commit()
        articles = db.scalars(select(LawArticle).order_by(LawArticle.seq)).all()
        assert len(articles) == 3 and all(a.embedding is None for a in articles)
        assert articles[0].text.startswith('示例适用范围')
        assert db.get(Law, lid).fulltext_at is not None
        assert db.scalar(select(func.count()).select_from(LawImportArticle)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.type == 'law_embed')) == 1
        enqueue_embed(db)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Job)) == 1
    second, _ = seed(db_sessionmaker, [('示例法_20200101.docx', law_doc())])
    with db_sessionmaker() as db:
        assert db.get(LawImport, second).changed == 1
        imports.discard(db, second, None)
        db.commit()
        assert len(db.scalars(select(LawArticle)).all()) == 3
        assert db.scalar(select(func.count()).select_from(LawImportArticle)) == 0


def test_t44_7_resume(db_sessionmaker, monkeypatch, caplog):
    iid, lid = seed(db_sessionmaker, [('示例法_20200101.docx', law_doc())])
    with db_sessionmaker() as db:
        imports.apply(db, iid, None)
        db.add_all(LawArticle(law_id=lid, seq=n, article_no='第二条', text='示例向量正文') for n in range(4, 131))
        db.commit()
    calls = []
    def embed(texts, kind):
        calls.append(len(texts))
        assert kind == 'passage' and all(t.startswith('《示例法》第') for t in texts)
        if len(calls) == 2:
            raise RuntimeError('模拟中断')
        return np.tile(np.eye(1, 1024, dtype=np.float32), (len(texts), 1))
    monkeypatch.setattr(engine, 'embed', embed)
    with db_sessionmaker() as db:
        with pytest.raises(RuntimeError, match='模拟中断'):
            handle_embed(db, {})
    with db_sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(LawArticle).where(LawArticle.embedding.is_not(None))) == 64
        handle_embed(db, {})
        assert db.scalar(select(func.count()).select_from(LawArticle).where(LawArticle.embedding.is_(None))) == 0
    assert calls == [64, 64, 64, 2]
    assert '示例向量正文' not in caplog.text


def test_t44_12_no_embedding_in_openapi():
    from app.main import create_app
    import json
    assert '"embedding"' not in json.dumps(create_app().openapi())


@pytest.mark.parametrize('role,code', [('user',403), ('sys_admin',403), ('biz_admin',202), ('admin',202)])
def test_fulltext_upload_permissions(db_sessionmaker, make_user, login, role, code):
    make_user('importer', role=role)
    client, token = login('importer')
    response = client.post('/api/v1/admin/law-imports', data={'kind':'fulltext'},
        files=[('files', ('全文.zip', archive([('说明.txt', b'fictional')]), 'application/zip'))],
        headers={'X-CSRF-Token': token})
    assert response.status_code == code, response.text
    if code == 202:
        with db_sessionmaker() as db:
            row = db.get(LawImport, uuid.UUID(response.json()['id']))
            assert row.kind == 'fulltext' and row.declared_status is None
            assert db.scalar(select(Job.type)) == 'law_import'
            stored = db.scalar(select(LawImportFile))
            assert crypto.decrypt(stored.data_enc).startswith(b'PK')


def test_t44a_7_empty_body_unmatched(db_sessionmaker):
    from tests.test_law_articles import unnumbered_doc
    iid, _ = seed(db_sessionmaker, [('示例法_20200101.docx', unnumbered_doc(['示例法', '（示例题注）']))])
    with db_sessionmaker() as db:
        assert db.get(LawImport, iid).diff['unmatched'] == [dict(file='示例法_20200101.docx', reason='正文为空')]


def test_t44a_8_null_article_passage_and_import(db_sessionmaker, monkeypatch, caplog):
    from tests.test_law_articles import unnumbered_doc
    iid, lid = seed(db_sessionmaker, [('示例法_20200101.docx', unnumbered_doc(['示例法', '一、示例无条号正文。']))])
    passages = []
    def embed(texts, kind):
        assert kind == 'passage'
        passages.extend(texts)
        return np.tile(np.eye(1, 1024, dtype=np.float32), (len(texts), 1))
    monkeypatch.setattr(engine, 'embed', embed)
    with db_sessionmaker() as db:
        assert db.get(LawImport, iid).added == 1
        imports.apply(db, iid, None)
        db.commit()
        handle_embed(db, {})
        article = db.scalar(select(LawArticle).where(LawArticle.law_id == lid))
        assert article.article_no is None and article.embedding is not None
    assert passages == ['《示例法》：一、示例无条号正文。']
    assert '示例法' not in caplog.text and '示例无条号正文' not in caplog.text


@pytest.mark.parametrize('title,filename', [
    ('最高人民法院　最高人民检察院关于示例问题的批复', '最高人民法院最高人民检察院关于示例问题的批复'),
    ('示例规定（试行）', '示例规定(试行)')])
def test_t44a_11_normalized_filename(db_sessionmaker, title, filename):
    from tests.test_law_articles import unnumbered_doc
    with db_sessionmaker() as db:
        db.add(Law(title=title, category='司法解释', status='valid', source='import', published_on=date(2020,3,19)))
        row = LawImport(kind='fulltext', status='parsing', file_names=['全文.zip'])
        db.add(row); db.flush()
        from app.laws.fulltext import preview
        preview(db, row, archive([(filename + '_20200319.docx', unnumbered_doc([filename, '示例正文。']))]))
        assert row.diff['unmatched'] == [] and row.diff['matched'][0]['title'] == title
        assert db.scalar(select(LawImportArticle.text)) == '示例正文。'


def test_t44a_11_ambiguous_normalized_title(db_sessionmaker):
    from app.laws.fulltext import preview
    with db_sessionmaker() as db:
        for title in ['示例规定（试行）', '示例规定(试行)']:
            db.add(Law(title=title, category='法律', status='valid', source='import', published_on=date(2020,3,19)))
        row = LawImport(kind='fulltext', status='parsing', file_names=['全文.zip'])
        db.add(row); db.flush()
        preview(db, row, archive([('示例规定(试行)_20200319.docx', law_doc())]))
        assert row.diff['matched'] == []
        assert row.diff['unmatched'][0]['reason'] == '对应多部法规'
        assert db.scalar(select(func.count()).select_from(LawImportArticle)) == 0
