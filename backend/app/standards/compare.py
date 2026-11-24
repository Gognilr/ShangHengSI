"""worker范本对照与L-12；外发前复用追问脱敏并整节复检。"""
from types import SimpleNamespace
import numpy as np
from sqlalchemy import select,delete
from app.content_drafts import template_clauses
from app.db.models import StandardTemplate,TaskClauseVector,LocalFinding,RedactionMap,Task,DocNode
from app.embedding import engine
from app.review.clauses import first_level
from app.security import crypto
from app.standards.service import clauses,decoded
from app.redaction.outbound import escape_document_tags
from app.redaction.placeholders import OUR_CODE

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


def _title(nodes,loc):
    # 取已随正文外发的脱敏文字：原文截断可能把名称切成半截，碎片不会再被对照表匹配（V2-E-09）。
    return escape_document_tags(nodes[loc].redacted_text[:60]) if loc in nodes else ''


def start(db,ctx,run,nodes):
    ctx.template_candidates=[]
    ctx.template_stats=dict(total=0,sent=0,blocked=False)
    db.execute(delete(LocalFinding).where(LocalFinding.task_id==ctx.task.id,LocalFinding.check_code=='L-12'))
    row=db.get(StandardTemplate,run.template_id) if run.template_id else None
    standards = clauses(db, row.id) if row and row.content_hash == run.template_hash else []
    shared = template_clauses(db, run)
    if not standards and not shared:
        db.commit();return
    stored=db.scalars(select(TaskClauseVector).where(TaskClauseVector.task_id==ctx.task.id)).all()
    if stored:
        source=[dict(loc=r.first_loc,locs=r.locs,vector=r.embedding,title=_title(nodes,r.first_loc)) for r in stored]
    else:
        kinds=dict(db.execute(select(DocNode.loc,DocNode.kind).where(DocNode.task_id==ctx.task.id)).all())
        parsed=first_level([SimpleNamespace(loc=n.loc,kind=kinds.get(n.loc,'paragraph'),part='body',clause_no=n.clause_no,text=n.text) for n in nodes.values()])
        vectors=engine.embed([c.text for c in parsed],'passage') if parsed else []
        source=[dict(loc=c.first_loc,locs=c.locs,title=_title(nodes,c.first_loc),vector=v) for c,v in zip(parsed,vectors)]
    for c in standards:
        if c.embedding is None:continue
        if max((_cos(s['vector'],c.embedding) for s in source),default=-1)<CLAUSE_PAIR_MIN:
            title=crypto.decrypt_text(c.title_enc)
            db.add(LocalFinding(task_id=ctx.task.id,check_code='L-12',severity='low',locs=[],
                   detail_enc=crypto.encrypt_text(f'公司范本中的『{title}』在本文件中未找到对应内容'),
                   ai_summary='公司范本中有条款在本文件中未找到对应内容'))
    for s in source:
        for items, is_shared in ((standards, False), (shared, True)):
            best = max(((_cos(s['vector'], c.embedding), c) for c in items if c.embedding is not None),
                       key=lambda pair: pair[0], default=None)
            if best is None or best[0] < CLAUSE_PAIR_MIN:
                continue
            score, clause = best
            values = (dict(text=crypto.decrypt_text(clause.text_enc), title="", stance="", acceptable="", escalate="")
                      if is_shared else decoded(clause))
            ctx.template_candidates.append(dict(score=score, loc=s['loc'], locs=s['locs'], shared=is_shared,
                                                **values, document_title=s['title']))
    db.commit()


def prepare(db,ctx,chunk):
    from app.chat.service import _redact,_terms
    from app.redaction.engine import _load_full_map,write_full_map,_load_terms
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
    allocator=None
    if ctx.task.kind=='assistant':
        # 会话用中性代号且不分配我方代号（V32-A-02）：预置我方名称按 ORG 续号，与问答共用同一套代号
        from app.redaction.placeholders import NeutralAllocator
        allocator=NeutralAllocator();allocator.seed(mapping)
    # 预置我方主体未在当前文件出现时，也按我方代号脱敏；多写法按既有#序号格式入图。
    for own in _load_terms(db, ctx.task.owner_id):
        if not own.is_our_party:
            continue
        value=own.text
        if value not in mapping.values():
            code=allocator.numbered('ORG',value) if allocator else OUR_CODE
            key=code if code not in mapping else f'{code}#{len(mapping)+1}'
            mapping[key]=value
    output=[];suspect=False
    for i,p in enumerate(chosen,1):
        shared = p.get("shared", False)
        raw = p["text"] if shared else f"范本条款：{p['text']}"
        raw+=''.join(f'\n{label}：{p[key]}' for key,label in [('stance','标准立场'),('acceptable','可接受范围'),('escalate','需上报的情形')] if p[key])
        redacted=_redact(raw,mapping,terms,allocator=allocator)
        suspect=suspect or bool(redacted.suspects)
        mapping.update(redacted.new_entries)
        # 先脱敏正文，再拼接固定标签，避免把标签里的普通词识别成疑似名称。
        passage = f"公司沉淀条文（供参考）：{redacted.text}" if shared else redacted.text
        output.append(f"[T{i}] 本文件位置：{p['loc']}（{p['document_title']}）\n{passage}")
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
