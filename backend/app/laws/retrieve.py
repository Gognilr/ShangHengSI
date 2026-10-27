"""本地法条检索、消息级预检与引用核实；不记录查询、向量或正文。"""
import re
import threading
from types import SimpleNamespace

from sqlalchemy import select

from app.admin.settings_store import get_setting
from app.db.models import DocNode, Law, LawArticle, Task, TaskClauseVector
from app.embedding import engine
from app.laws.index import canonical, PREFIX, LAW_CATEGORIES
from app.redaction.outbound import escape_document_tags
from app.redaction.recheck import recheck, recheck_context
from app.review.clauses import first_level
from app.security.errors import NotFoundError

LAW_MIN_SCORE = 0.56
_cache_lock = threading.Lock()
_requirement_cache = {}
_CITATION = re.compile(r'《([^》]{1,300})》(第[零〇一二三四五六七八九十百千万\d]+条(?:之[一二三四五六七八九十]+)?)')


def require_owner(db,task_id,user_id):
    task=db.scalar(select(Task).where(Task.id==task_id,Task.owner_id==user_id))
    if task is None: raise NotFoundError('TASK_NOT_FOUND','任务不存在')
    return task


def search(db,vectors):
    found={}
    for vector in vectors:
        distance=LawArticle.embedding.cosine_distance(vector)
        rows=db.execute(select(LawArticle,Law.title,(1-distance).label('score')).join(Law)
            .where(Law.status=='valid',Law.category.in_(LAW_CATEGORIES+('司法解释',)),LawArticle.embedding.is_not(None),
                   distance<=1-LAW_MIN_SCORE).order_by(distance,LawArticle.id).limit(5)).all()
        for article,title,score in rows:
            if article.id not in found or score>found[article.id]['score']:
                found[article.id]=dict(id=article.id,title=title,article_no=article.article_no,text=article.text,score=score)
    return sorted(found.values(),key=lambda r:(-r['score'],r['id']))


def select_refs(candidates,check,limit=20):
    refs=[]; chars=0; dropped=0
    for candidate in sorted(candidates,key=lambda r:(-r['score'],r['id'])):
        value=f"《{candidate['title']}》{candidate['article_no'] or ''}：{candidate['text']}"
        value=escape_document_tags(value)
        if recheck(value,real_values=check.real_values,terms=check.terms,placeholders=check.placeholders):
            dropped+=1
            continue
        if len(refs)>=limit or chars+len(value)>8000:
            continue
        refs.append({**candidate,'ref':f'LAW{len(refs)+1}'})
        chars+=len(value)
    return refs,dict(enabled=True,sent=len(refs),dropped=dropped,chars=chars)


def section(refs):
    if not refs: return ''
    return '【参考法条】\n'+'\n'.join(escape_document_tags(
        f"[{r['ref']}]《{r['title']}》{r['article_no'] or ''}：{r['text']}") for r in refs)


def prepare(db,ctx,chunk,nodes):
    ctx.law_refs=[]
    if ctx.task.policy!='ai' or not get_setting(db,'laws.refs_enabled'):
        return
    locs=set(nodes) if chunk is None else set(chunk.locs)
    stored=db.scalars(select(TaskClauseVector).where(TaskClauseVector.task_id==ctx.task.id,
                                                    TaskClauseVector.model==engine.model_id())).all()
    vectors=[r.embedding for r in stored if set(r.locs)&locs]
    if not stored:
        kinds=dict(db.execute(select(DocNode.loc,DocNode.kind).where(DocNode.task_id==ctx.task.id)).all())
        values=[SimpleNamespace(loc=n.loc,part='body',kind=kinds.get(n.loc,'paragraph'),clause_no=n.clause_no,text=n.text)
                for n in nodes.values() if n.loc in locs]
        vectors=engine.embed([c.text[:2000] for c in first_level(values)],'passage') if values else []
    requirements=[r for r in ctx.requirements if r.source!='note']
    key=(engine.model_id(),tuple((r.id,r.text) for r in requirements))
    with _cache_lock:
        if key not in _requirement_cache:
            # 只缓存当前一组要求，版本/文字变化立即替换，避免无界保留历史说明。
            _requirement_cache.clear()
            _requirement_cache[key]=engine.embed([r.text for r in requirements],'query') if requirements else []
        vectors=list(vectors)+list(_requirement_cache[key])
    notes=[r.text for r in ctx.requirements if r.source=='note']
    if notes: vectors+=list(engine.embed(notes,'query'))
    ctx.law_refs,stats=select_refs(search(db,vectors),recheck_context(db,ctx.task.id))
    ctx.law_stats['enabled']=True
    for name in ('sent','dropped','chars'): ctx.law_stats[name]+=stats[name]


def question_refs(db,task,question):
    if task.policy!='ai' or not get_setting(db,'laws.refs_enabled'): return ''
    refs,_=select_refs(search(db,engine.embed([question],'query')),recheck_context(db,task.id),5)
    return section(refs)


def verify_findings(db,findings):
    for finding in findings:
        citations=_CITATION.findall('\n'.join(v or '' for v in (finding.problem,finding.basis,finding.suggestion)))
        for title,article in citations:
            name=canonical(title)
            versions=db.scalars(select(Law).where(Law.title.in_([name,PREFIX+name]),
                Law.status.in_(['valid','amended','not_yet'])).order_by(Law.published_on.desc().nullslast(),Law.id.desc())).all()
            if not versions:
                finding.law_unverified=True
                continue
            valid=next((v for v in versions if v.status=='valid'),None)
            if valid is not None and valid.fulltext_at is not None:
                if not db.scalar(select(LawArticle.id).where(LawArticle.law_id==valid.id,
                                                           LawArticle.article_no==article).limit(1)):
                    finding.law_unverified=True
