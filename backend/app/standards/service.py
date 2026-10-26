"""范本管理及基于本任务已存向量的选择；接口不返回向量。"""
import hashlib
import io
import json

from sqlalchemy import select,func,text

from app.audit import service as audit
from app.db import jobs
from app.db.base import utcnow
from app.db.models import StandardTemplate,StandardClause,TaskClauseVector,TaskFile
from app.parsing.docx import parse_docx
from app.review.clauses import first_level
from app.security import crypto
from app.security.errors import AppError,NotFoundError
from app.tasks.filecheck import check_ooxml

TEMPLATE_SELECT_MIN=0.70


def clauses(db,tid):
    return db.scalars(select(StandardClause).where(StandardClause.template_id==tid).order_by(StandardClause.seq)).all()


def load(db,tid,locked=False):
    row=db.get(StandardTemplate,tid,with_for_update=locked)
    if row is None:raise NotFoundError('TEMPLATE_NOT_FOUND','公司范本不存在或已删除')
    return row


def decoded(row):
    return {name:crypto.decrypt_text(getattr(row,name+'_enc')) if getattr(row,name+'_enc') else ''
            for name in ('title','text','stance','acceptable','escalate')}


def rehash(db,row):
    values=[decoded(c) for c in clauses(db,row.id)]
    row.content_hash=hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    row.updated_at=utcnow()


def create(db,data,values,user_id):
    db.execute(text('SELECT pg_advisory_xact_lock(7205004)'))
    if db.scalar(select(func.count()).select_from(StandardTemplate))>=50:
        raise AppError(422,'TEMPLATE_LIMIT','公司范本最多 50 份，请删除不再使用的范本')
    check_ooxml(io.BytesIO(data),'docx','公司范本')
    items=first_level(parse_docx(data,1).nodes)
    if not items:raise AppError(422,'TEMPLATE_CLAUSES','未识别到条款')
    if len(items)>200 or any(len(c.text)>5000 for c in items):
        raise AppError(422,'TEMPLATE_CLAUSES','范本最多 200 条，每条最多 5000 字')
    row=StandardTemplate(name_enc=crypto.encrypt_text(values['name']),doc_types=values['doc_types'],
                         stances=values['stances'],content_hash='0'*64,clause_count=len(items),created_by=user_id)
    db.add(row);db.flush()
    db.add_all(StandardClause(template_id=row.id,seq=i,title_enc=crypto.encrypt_text(c.title),
                             text_enc=crypto.encrypt_text(c.text)) for i,c in enumerate(items,1))
    db.flush();rehash(db,row)
    jobs.enqueue(db,'template_embed',{'template_id':str(row.id)})
    audit.record(db,'template_create',user_id=user_id,target_type='template',target_id=str(row.id),detail={'clauses':len(items)})
    return row


def update_clause(db,tid,cid,values,user_id):
    row=load(db,tid,True)
    clause=db.scalar(select(StandardClause).where(StandardClause.template_id==tid,StandardClause.id==cid))
    if clause is None:raise NotFoundError('TEMPLATE_CLAUSE_NOT_FOUND','范本条款不存在')
    for name,value in values.items():
        setattr(clause,name+'_enc',crypto.encrypt_text(value) if value else None)
    db.flush();rehash(db,row)
    audit.record(db,'template_clause_update',user_id=user_id,target_type='template',target_id=str(tid),detail={'clause_id':str(cid)})
    return clause


def view(db,row,detail=False):
    items=clauses(db,row.id)
    result=dict(id=row.id,name=crypto.decrypt_text(row.name_enc),doc_types=row.doc_types,stances=row.stances,
                enabled=row.enabled,clause_count=row.clause_count,ready=all(c.embedding is not None for c in items),
                created_at=row.created_at,updated_at=row.updated_at)
    if detail:result['clauses']=[dict(id=c.id,seq=c.seq,**decoded(c)) for c in items]
    return result


def choose(db,task,doc_type=None):
    if task.policy!='ai':return None
    queries=db.scalars(select(TaskClauseVector.embedding).where(TaskClauseVector.task_id==task.id)).all()
    if not queries:return None
    if doc_type is None:
        doc_type=db.scalar(select(TaskFile.doc_type).where(TaskFile.task_id==task.id,TaskFile.is_main.is_(True)))
    best=None;best_score=TEMPLATE_SELECT_MIN
    for row in db.scalars(select(StandardTemplate).where(StandardTemplate.enabled.is_(True)).order_by(StandardTemplate.id)):
        if doc_type not in row.doc_types or task.stance not in row.stances:continue
        if db.scalar(select(StandardClause.id).where(StandardClause.template_id==row.id,StandardClause.embedding.is_(None)).limit(1)):continue
        scores=[]
        for vector in queries:
            score=db.scalar(select(func.max(1-StandardClause.embedding.cosine_distance(vector))).where(StandardClause.template_id==row.id))
            if score is not None:scores.append(float(score))
        if len(scores)==len(queries) and sum(scores)/len(scores)>=best_score:
            best,best_score=row,sum(scores)/len(scores)
    return best


def suggestion(db,task):
    if task.status!='awaiting_confirm':return None
    row=choose(db,task)
    return dict(id=row.id,name=crypto.decrypt_text(row.name_enc)) if row else None


def snapshot(db,run):
    if run.template_name_snapshot_enc is None:return None
    row=db.get(StandardTemplate,run.template_id) if run.template_id else None
    return dict(name=crypto.decrypt_text(run.template_name_snapshot_enc),
                status='deleted' if row is None else 'modified' if row.content_hash!=run.template_hash else 'current')
