"""同步仅生成预览；正式法规与条文在管理员确认时原子写入。"""
import io
from datetime import date

from sqlalchemy import select

from app.admin.settings_store import set_setting
from app.audit import service as audit
from app.db.base import utcnow
from app.db.models import Law, LawImport, LawImportArticle
from app.laws import imports
from app.laws.articles import split
from app.parsing import docx, legacy
from app.tasks import filecheck
from app.lawsync import client

_STATUS={1:'repealed',2:'amended',3:'valid',4:'not_yet'}


def run(db):
    requests=0
    try:
        # 导入锁贯穿预览，防止同步过程中手工上传产生第二条未处理记录。
        imports.lock(db)
        if db.scalar(select(LawImport.id).where(LawImport.status.in_(['parsing','pending'])).limit(1)):
            return
        rows=[]
        page=1
        while True:
            requests+=1
            data=client.list_page(client.CATEGORY_CODES,(1,2,3,4),page,100)
            rows.extend(data['rows'])
            if data['total']>6000:
                raise client.SyncError('BAD_RESPONSE')
            if page*100>=data['total']:
                break
            if not data['rows'] or page>=60:
                raise client.SyncError('BAD_RESPONSE')
            page+=1
        diff=dict(added=[],changed=[],skipped=[],duplicates=0,fulltext=[],fulltext_failed=[])
        existing={(r.title,r.published_on):r for r in db.scalars(select(Law))}
        seen=set(); unchanged=0; skipped=0
        for r in rows:
            if r['sxx'] is None or r.get('published_on') is None:
                skipped+=1
                continue
            value=dict(title=r['title'].strip(),published_on=r['published_on'],effective_on=r.get('effective_on'),
                       category=r['category'],office=r.get('office'),status=_STATUS[r['sxx']],flk_id=r['bbbs'])
            key=(value['title'],date.fromisoformat(value['published_on']))
            if key in seen:
                diff['duplicates']+=1
                continue
            seen.add(key)
            old=existing.get(key)
            if old is None:
                diff['added'].append(value)
                continue
            # 内置同键保留内置来源及已有追溯信息，不由同步覆盖官方编号。
            fields=['status','category','office','effective_on'] + ([] if old.source=='builtin' else ['flk_id'])
            changes={}
            for field in fields:
                if field=='effective_on' and value[field] is None:
                    continue
                before=getattr(old,field)
                before=before.isoformat() if isinstance(before,date) else before
                if before!=value[field]: changes[field]=[before,value[field]]
            if changes:
                diff['changed'].append(dict(law_id=old.id,title=old.title,published_on=value['published_on'],changes=changes))
            else: unchanged+=1
        record=LawImport(kind='sync',status='parsing',file_names=[],added=len(diff['added']),
                         changed=len(diff['changed']),unchanged=unchanged,skipped=skipped)
        db.add(record); db.flush()
        # flxz 是“民法商法”等原始子类名；全文资格按已校验的官方码判断。
        fulltext_ids={r['bbbs'] for r in rows if r['flfgCodeId'] in client.CATEGORY_CODES and r['flfgCodeId']!=210}
        candidates=sorted((r for r in diff['added'] if r['status']=='valid' and
                           r['flk_id'] in fulltext_ids),key=lambda r:r['published_on'],reverse=True)[:200]
        for row in candidates:
            try:
                # 一次只取一部的地址，避免官方漏返时将其他法规全文错误绑定。
                requests+=1
                urls=client.download_urls([row['flk_id']])
                if len(urls)!=1: raise client.SyncError('BAD_RESPONSE')
                requests+=1
                value=client.download_file(urls[0])
                if value.startswith(filecheck.OLE_MAGIC):
                    filecheck.check_ole(io.BytesIO(value),'doc','法规全文')
                    value=legacy.convert(value,'doc')
                filecheck.check_ooxml(io.BytesIO(value),'docx','法规全文')
                articles=split(docx.parse_docx(value,1).nodes)
                if not articles: raise client.SyncError('BAD_RESPONSE')
                db.add_all(LawImportArticle(import_id=record.id,title=row['title'],
                           published_on=date.fromisoformat(row['published_on']),article_no=a.article_no,
                           seq=a.seq,text=a.text) for a in articles)
                diff['fulltext'].append(dict(title=row['title'],published_on=row['published_on'],articles=len(articles)))
            except Exception:
                # 单个公开全文失败不影响目录；不保存异常、地址或文件内容。
                diff['fulltext_failed'].append(dict(title=row['title'],published_on=row['published_on'],reason='全文获取或解析失败'))
        record.diff=diff
        record.status='pending'
        now=utcnow().isoformat()
        set_setting(db,'lawsync.last_run_at',now,None)
        set_setting(db,'lawsync.last_status',{'ok':True,'at':now},None)
        audit.record(db,'law_sync_run',detail=dict(requests=requests,result='OK',added=record.added,
                     changed=record.changed,fulltext=len(diff['fulltext'])))
        db.commit()
    except Exception as exc:
        db.rollback()
        code=str(exc) if isinstance(exc,client.SyncError) and str(exc) in ('BLOCKED','BAD_RESPONSE','NETWORK') else 'INTERNAL'
        now=utcnow().isoformat()
        # 失败也记尝试时间，按周/月退避，不在每小时检查中重复请求被拦站点。
        set_setting(db,'lawsync.last_run_at',now,None)
        set_setting(db,'lawsync.last_status',dict(ok=False,at=now,reason=code),None)
        audit.record(db,'law_sync_run',detail=dict(requests=requests,result=code,added=0,changed=0,fulltext=0))
        db.commit()
