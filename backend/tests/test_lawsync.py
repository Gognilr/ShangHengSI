"""T-43：官方同步出口与待确认事务，全部响应为虚构数据。"""
import inspect
import io
from datetime import date, timedelta

import httpx
import pytest
from docx import Document
from sqlalchemy import select, func

from app.db.base import utcnow
from app.db.models import Law, LawImport, LawImportArticle, LawArticle, Job, AuditLog, LlmCall
from app.admin.settings_store import set_setting, get_setting


@pytest.fixture
def wire(monkeypatch):
    from app.lawsync import client
    clock = [0.0]
    calls = []
    monkeypatch.setattr(client.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(client.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    monkeypatch.setattr(client, '_last_request', None)
    original = httpx.Client
    def install(handler):
        def record(request):
            calls.append((clock[0], request))
            return handler(request)
        monkeypatch.setattr(client.httpx, 'Client', lambda **kw: original(**kw, transport=httpx.MockTransport(record)))
    return install, calls


def item(n=1, **kw):
    return dict(bbbs=f'{n:032x}', title=f'示例供货法规{n}', published_on='2026-01-01',
                effective_on=None, sxx=3, category='法律', office='示例机关', **kw)


def test_t43_signatures_and_input_rejection(wire):
    from app.lawsync import client
    assert client.API_HOST == 'flk.npc.gov.cn'
    assert client.FILE_HOST == 'flkoss.obs-bj2.cucloud.cn'
    assert list(inspect.signature(client.list_page).parameters) == ['category_codes','status_codes','page','page_size']
    assert list(inspect.signature(client.download_urls).parameters) == ['flk_ids']
    assert list(inspect.signature(client.download_file).parameters) == ['url']
    for url in ('https://example.com/prod/a.docx', 'http://flkoss.obs-bj2.cucloud.cn/prod/a',
                'https://flkoss.obs-bj2.cucloud.cn:444/prod/a', 'https://u:p@flkoss.obs-bj2.cucloud.cn/prod/a',
                'https://flkoss.obs-bj2.cucloud.cn/prod/../secret'):
        with pytest.raises(ValueError): client.download_file(url)
    with pytest.raises(ValueError): client.download_urls(['../etc'])
    with pytest.raises(ValueError): client.list_page('102', (1,), 1, 100)
    with pytest.raises(ValueError): client.list_page((True,), (1,), 1, 100)
    assert not wire[1]


@pytest.mark.parametrize('response,code', [
    (lambda: httpx.Response(302, headers={'location':'https://example.com/'}), 'BLOCKED'),
    (lambda: httpx.Response(200, text='<html>验证码</html>'), 'BLOCKED'),
    (lambda: httpx.Response(200, json={'code':200,'rows':[dict(item(),bbbs='bad')],'total':1}), 'BAD_RESPONSE'),
    (lambda: httpx.Response(200, json={'code':200,'rows':[dict(item(),title=123)],'total':1}), 'BAD_RESPONSE'),
])
def test_t43_untrusted_response(wire, response, code):
    from app.lawsync import client
    wire[0](lambda r: response())
    with pytest.raises(client.SyncError, match=code): client.list_page((102,), (1,2,3,4), 1, 100)
    assert len(wire[1]) == 1


def test_t43_spacing(wire):
    from app.lawsync import client
    wire[0](lambda r: httpx.Response(200,json={'code':200,'rows':[], 'total':0}))
    for _ in range(3): client.list_page((102,), (1,), 1, 100)
    assert [t for t,r in wire[1]] == [0,2,4]


def document():
    doc = Document()
    doc.add_paragraph('第一条　双方应按期供货。')
    out = io.BytesIO(); doc.save(out)
    return out.getvalue()


def test_t43_two_pages_preview_apply_and_audit(db_sessionmaker, monkeypatch):
    from app.lawsync import client, sync
    from app.laws import imports
    pages=[]
    def page(c,s,p,n):
        pages.append(p)
        return {'code':200,'rows':[item()] if p==1 else [dict(item(2),sxx=1),dict(item(3),sxx=None)], 'total':101}
    monkeypatch.setattr(client,'list_page',page)
    monkeypatch.setattr(client,'download_urls',lambda ids: ['https://flkoss.obs-bj2.cucloud.cn/prod/a.docx'])
    monkeypatch.setattr(client,'download_file',lambda url: document())
    with db_sessionmaker() as db:
        old=Law(title=item(2)['title'],published_on=date(2026,1,1),category='法律',status='valid',source='import')
        db.add(old); db.commit()
        sync.run(db)
        record=db.scalar(select(LawImport))
        assert pages == [1,2]
        assert (record.kind,record.status,record.added,record.changed)==('sync','pending',1,1)
        assert db.scalar(select(func.count()).select_from(LawImportArticle)) == 1
        assert db.scalar(select(func.count()).select_from(Law)) == 1
        assert old.status == 'valid'
        imports.apply(db,record.id,None); db.commit()
        rows=db.scalars(select(Law).order_by(Law.title)).all()
        assert rows[0].flk_id == item()['bbbs'] and rows[0].source == 'sync'
        assert rows[1].status=='repealed' and rows[1].flk_id == item(2)['bbbs']
        assert db.scalar(select(func.count()).select_from(LawArticle))==1
        assert db.scalar(select(Job).where(Job.type=='law_embed'))
        assert db.scalar(select(AuditLog).where(AuditLog.action=='law_sync_run'))
        assert db.scalar(select(func.count()).select_from(LlmCall))==0


@pytest.mark.parametrize('legacy,failed',[(False,False),(True,False),(False,True)])
def test_t43_fulltext_isolation(db_sessionmaker,monkeypatch,legacy,failed):
    from app.lawsync import client,sync
    from app.parsing import legacy as converter
    from app.tasks import filecheck
    monkeypatch.setattr(client,'list_page',lambda *a:{'code':200,'rows':[item(),item(2)],'total':2})
    monkeypatch.setattr(client,'download_urls',lambda ids:['https://flkoss.obs-bj2.cucloud.cn/prod/a'])
    calls=[]
    def download(url):
        calls.append(url)
        if failed and len(calls)==1: raise client.SyncError('NETWORK')
        return filecheck.OLE_MAGIC+b'fictional' if legacy else document()
    monkeypatch.setattr(client,'download_file',download)
    monkeypatch.setattr(filecheck,'check_ole',lambda *a,**k:None)
    monkeypatch.setattr(converter,'convert',lambda data,ext:document())
    with db_sessionmaker() as db:
        sync.run(db)
        r=db.scalar(select(LawImport))
        assert len(r.diff['fulltext']) == (1 if failed else 2)
        assert len(r.diff['fulltext_failed']) == (1 if failed else 0)
        assert r.status=='pending'


def test_t43_failure_alert_and_no_pending(db_sessionmaker,monkeypatch):
    from app.lawsync import client,sync
    from app.admin.alerts import admin_alerts
    monkeypatch.setattr(client,'list_page',lambda *a: (_ for _ in ()).throw(client.SyncError('BLOCKED')))
    with db_sessionmaker() as db:
        set_setting(db,'lawsync.enabled',True,None); db.commit()
        sync.run(db)
        assert get_setting(db,'lawsync.last_status')['ok'] is False
        assert not db.scalar(select(LawImport))
        assert not db.scalar(select(Law))
        assert 'LAWSYNC_FAILED' in [a['code'] for a in admin_alerts(db,'sys_admin')]
        assert 'LAWSYNC_FAILED' not in [a['code'] for a in admin_alerts(db,'biz_admin')]
        set_setting(db,'lawsync.enabled',False,None)
        assert 'LAWSYNC_FAILED' not in [a['code'] for a in admin_alerts(db,'sys_admin')]


def test_t43_schedule_disabled_pending_and_dedup(db_sessionmaker):
    from app.lawsync.job import check_schedule
    with db_sessionmaker() as db:
        check_schedule(db)
        assert not db.scalar(select(Job))
        set_setting(db,'lawsync.enabled',True,None)
        db.add(LawImport(kind='catalog',status='pending',file_names=[])); db.commit()
        check_schedule(db)
        assert not db.scalar(select(Job))
        db.query(LawImport).delete(); db.commit()
        check_schedule(db); check_schedule(db)
        assert len(db.scalars(select(Job)).all()) == 1


@pytest.mark.parametrize('reason',['BAD_RESPONSE','NETWORK'])
def test_t43_failure_rolls_back_directory(db_sessionmaker,monkeypatch,reason):
    from app.lawsync import client,sync
    def page(*args):
        if args[2]==1: return dict(code=200,rows=[item()],total=101)
        raise client.SyncError(reason)
    monkeypatch.setattr(client,'list_page',page)
    with db_sessionmaker() as db:
        sync.run(db)
        assert not db.scalar(select(LawImport))
        assert not db.scalar(select(LawImportArticle))
        assert get_setting(db,'lawsync.last_status')['reason']==reason



def test_official_wire_fields_and_download_shape(wire):
    import json
    from app.lawsync import client
    raw=dict(bbbs='1'*32,title='示例协议法',gbrq='2026-01-01',sxrq='2026-02-01',
             sxx=3,flxz='法律',zdjgName='示例机关')
    def respond(r):
        body=json.loads(r.content)
        if r.url.path.endswith('/list'):
            assert body == dict(searchRange=1,searchType=2,searchContent='',sxrq=[],gbrq=[],
                                gbrqYear=[],zdjgCodeId=[],flfgCodeId=[102],sxx=[3],pageNum=1,pageSize=100)
            return httpx.Response(200,json=dict(code=200,rows=[raw],total=1))
        assert body == [dict(bbbs='1'*32,format='docx')]
        return httpx.Response(200,json=dict(code=200,data=[dict(url='https://flkoss.obs-bj2.cucloud.cn/prod/a.docx',urlIn='http://internal/ignored')]))
    wire[0](respond)
    row=client.list_page((102,),(3,),1,100)['rows'][0]
    assert (row['published_on'],row['effective_on'],row['category'],row['office']) == ('2026-01-01','2026-02-01','法律','示例机关')
    assert client.download_urls(['1'*32]) == ['https://flkoss.obs-bj2.cucloud.cn/prod/a.docx']


@pytest.mark.parametrize('data', [None,[{}],[dict(url='https://example.com/a')],[dict(urlIn='https://flkoss.obs-bj2.cucloud.cn/prod/a')]])
def test_official_download_rejects_missing_or_untrusted_url(wire,data):
    from app.lawsync import client
    wire[0](lambda r:httpx.Response(200,json=dict(code=200,data=data)))
    with pytest.raises(client.SyncError,match='BAD_RESPONSE'):
        client.download_urls(['1'*32])
