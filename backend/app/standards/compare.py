"""worker范本对照与L-12；外发前复用追问脱敏并整节复检。"""
from types import SimpleNamespace
import numpy as np
from sqlalchemy import select,delete
from app.db.models import StandardTemplate,TaskClauseVector,LocalFinding,RedactionMap,RedactionTerm,Task,DocNode
from app.embedding import engine
from app.review.clauses import first_level
from app.security import crypto
from app.standards.service import clauses,decoded

CLAUSE_PAIR_MIN=0.70


def _cos(a,b):
    den=np.linalg.norm(a)*np.linalg.norm(b)
    return float(np.dot(a,b)/den) if den else -1


def limit_pairs(pairs):
    result=[];size=0
    for pair in sorted(pairs,key=lambda p:-p['score']):
        count=sum(len(str(pair.get(k) or '')) for k in ('loc','title','text','stance','acceptable','escalate'))+100
        if len(result)>=30 or size+count>10000:continue
        result.append(pair);size+=count
    return result


def start(db,ctx,run,nodes):
    ctx.template_candidates=[]
    ctx.template_stats=dict(total=0,sent=0,blocked=False)
    db.execute(delete(LocalFinding).where(LocalFinding.task_id==ctx.task.id,LocalFinding.check_code=='L-12'))
    row=db.get(StandardTemplate,run.template_id) if run.template_id else None
    if row is None or row.content_hash!=run.template_hash:
        db.commit();return
    stored=db.scalars(select(TaskClauseVector).where(TaskClauseVector.task_id==ctx.task.id)).all()
    if stored:
        source=[dict(loc=r.first_loc,locs=r.locs,vector=r.embedding,title=nodes[r.first_loc].text[:60] if r.first_loc in nodes else '') for r in stored]
    else:
        kinds=dict(db.execute(select(DocNode.loc,DocNode.kind).where(DocNode.task_id==ctx.task.id)).all())
        parsed=first_level([SimpleNamespace(loc=n.loc,kind=kinds.get(n.loc,'paragraph'),part='body',clause_no=n.clause_no,text=n.text) for n in nodes.values()])
        vectors=engine.embed([c.text for c in parsed],'passage') if parsed else []
        source=[dict(loc=c.first_loc,locs=c.locs,title=c.title,vector=v) for c,v in zip(parsed,vectors)]
    standards=clauses(db,row.id)
    for c in standards:
        if c.embedding is None:continue
        if max((_cos(s['vector'],c.embedding) for s in source),default=-1)<CLAUSE_PAIR_MIN:
            title=crypto.decrypt_text(c.title_enc)
            db.add(LocalFinding(task_id=ctx.task.id,check_code='L-12',severity='low',locs=[],
                   detail_enc=crypto.encrypt_text(f'公司范本中的『{title}』在本文件中未找到对应内容'),
                   ai_summary='公司范本中有条款在本文件中未找到对应内容'))
    for s in source:
        choices=[(_cos(s['vector'],c.embedding),c) for c in standards if c.embedding is not None]
        if not choices:continue
        score,c=max(choices,key=lambda p:p[0])
        if score>=CLAUSE_PAIR_MIN:
            ctx.template_candidates.append(dict(score=score,loc=s['loc'],locs=s['locs'],
                **decoded(c),document_title=s['title']))
    db.commit()


def prepare(db,ctx,chunk):
    from app.chat.service import _redact,_terms
    from app.redaction.engine import _load_full_map,write_full_map
    from app.redaction.outbound import escape_document_tags
    from app.redaction.recheck import recheck,recheck_context
    ctx.template_section=''
    pairs=[p for p in ctx.template_candidates if chunk is None or set(p['locs'])&set(chunk.locs)]
    chosen=limit_pairs(pairs)
    ctx.template_stats['total']+=len(pairs)
    if not chosen:return
    # 与追问同序：任务行先锁住，再锁对照表，避免新代号并发分配冲突。
    db.scalar(select(Task).where(Task.id==ctx.task.id).with_for_update())
    db.scalar(select(RedactionMap).where(RedactionMap.task_id==ctx.task.id).with_for_update())
    mapping=_load_full_map(db,ctx.task.id);terms=_terms(db)
    # 预置我方主体未在当前文件出现时，也按“我方”脱敏；多写法按既有#序号格式入图。
    for own in db.scalars(select(RedactionTerm).where(RedactionTerm.enabled.is_(True),RedactionTerm.is_our_party.is_(True))):
        value=crypto.decrypt_text(own.term_enc)
        if value not in mapping.values():
            key='我方' if '我方' not in mapping else f'我方#{len(mapping)+1}'
            mapping[key]=value
    output=[];suspect=False
    for i,p in enumerate(chosen,1):
        raw=f"范本条款：{p['text']}"
        raw+=''.join(f'\n{label}：{p[key]}' for key,label in [('stance','标准立场'),('acceptable','可接受范围'),('escalate','需上报的情形')] if p[key])
        redacted=_redact(raw,mapping,terms)
        suspect=suspect or bool(redacted.suspects)
        mapping.update(redacted.new_entries)
        # 本文件标题可能含原文，亦须走同一脱敏函数。
        title=_redact(p['document_title'],mapping,terms)
        mapping.update(title.new_entries);suspect=suspect or bool(title.suspects)
        output.append(f"[T{i}] 本文件位置：{p['loc']}（{title.text}）\n{redacted.text}")
    write_full_map(db,ctx.task.id,mapping)
    db.commit()
    bounded=[];size=len('【公司范本对照】\n')
    for block in output:
        if size+len(block)+1>10000:continue
        bounded.append(block);size+=len(block)+1
    if not bounded:return
    value='【公司范本对照】\n'+'\n'.join(bounded)
    rc=recheck_context(db,ctx.task.id)
    if suspect or recheck(value,real_values=rc.real_values,terms=rc.terms,placeholders=rc.placeholders):
        ctx.template_stats['blocked']=True
        return
    ctx.template_section=value
    ctx.template_stats['sent']+=len(bounded)
