"""T-46 对照上限、脱敏、整节预检、L-12与来源。"""
import json
from types import SimpleNamespace

import pytest


def test_t46_pair_limits():
    from app.standards.compare import limit_pairs
    pairs=[dict(score=1-i/1000,loc='F1-P1',title='示例条款',text='示例'*100,stance='',acceptable='',escalate='') for i in range(40)]
    out=limit_pairs(pairs)
    assert len(out)<=30 and sum(len(p['text']) for p in out)<=10000
    assert [p['score'] for p in out]==sorted([p['score'] for p in out],reverse=True)


def test_t46_source_mapping():
    from app.review.validate import validate_output
    from app.review.prompt import ReviewContext
    ctx=ReviewContext(SimpleNamespace(),'sales','incoming',None,'contract',[],[],'','')
    for source,expected in [('范本对照','template'),('invented','model'),(None,'model')]:
        output=dict(requirement_results=[],findings=[dict(title='示例',problem='示例问题',source=source)])
        assert validate_output(json.dumps(output),ctx,{}).findings[0].source==expected


@pytest.fixture
def integration(db_sessionmaker,make_user,monkeypatch,tmp_path):
    from app.config import get_settings
    from app.security import crypto
    from app.redaction import ner
    key=tmp_path/'key';key.write_bytes(b'k'*32)
    monkeypatch.setenv('APP_MASTER_KEY_FILE',str(key))
    get_settings.cache_clear();crypto.get_master_key.cache_clear()
    monkeypatch.setattr(ner,'recognize',lambda text:[])
    yield make_user('compare_owner')
    get_settings.cache_clear();crypto.get_master_key.cache_clear()


def test_t46_l12_and_redacted_section(db_sessionmaker,integration,monkeypatch):
    from tests.test_standards import document,vector
    from app.standards import service,compare
    from app.db.models import Task,TaskClauseVector,StandardClause,LocalFinding,ReviewRun,RedactionMap
    from app.security import crypto
    from app.review.prompt import ReviewContext
    from app.review.validate import DocNodeView
    from app.checks.service import local_summary
    from app.redaction.engine import write_full_map,load_redaction_map
    with db_sessionmaker() as db:
        row=service.create(db,document(4),dict(name='虚构范本',doc_types=['contract'],stances=['sales']),integration.id)
        for i,c in enumerate(service.clauses(db,row.id)):
            c.embedding=vector(1 if i==0 else 0)
            c.text_enc=crypto.encrypt_text('示例客户有限公司应依约交付。')
        service.rehash(db,row)
        task=Task(owner_id=integration.id,title='示例',stance='sales',source='incoming',policy='ai',status='reviewing')
        db.add(task);db.flush();write_full_map(db,task.id,{})
        db.add(TaskClauseVector(task_id=task.id,file_seq=1,first_loc='F1-P1',locs=['F1-P1'],embedding=vector(),model='test'))
        run=ReviewRun(task_id=task.id,run_no=1,status='running',stance='sales',source='incoming',main_doc_type='contract',
                      created_by=integration.id,template_id=row.id,template_name_snapshot_enc=row.name_enc,template_hash=row.content_hash)
        db.add(run);db.commit()
        ctx=ReviewContext(task,'sales','incoming',None,'contract',[],[],'','')
        compare.start(db,ctx,run,{'F1-P1':DocNodeView('F1-P1','交付约定','交付约定')})
        findings=db.query(LocalFinding).filter_by(check_code='L-12').all()
        assert len(findings)==3 and all(f.severity=='low' and f.locs==[] for f in findings)
        assert all('公司范本中的『' in crypto.decrypt_text(f.detail_enc) for f in findings)
        assert 'L-12' not in local_summary(db,task)
        compare.prepare(db,ctx,None)
        assert '【公司范本对照】' in ctx.template_section and '示例客户有限公司' not in ctx.template_section
        assert '示例客户有限公司' in load_redaction_map(db,task.id).values()
        run.template_id=None;db.commit()
        compare.start(db,ctx,run,{})
        assert db.query(LocalFinding).filter_by(check_code='L-12').count()==0


def test_t46_section_blocked(db_sessionmaker,integration,monkeypatch):
    from app.standards import compare
    from app.db.models import Task
    from app.review.prompt import ReviewContext
    from app.chat import service as chat
    from app.redaction.engine import write_full_map
    from app.redaction.recheck import BlockReason
    with db_sessionmaker() as db:
        task=Task(owner_id=integration.id,title='示例',stance='sales',source='incoming',policy='ai',status='reviewing')
        db.add(task);db.flush();write_full_map(db,task.id,{});db.commit()
        ctx=ReviewContext(task,'sales','incoming',None,'contract',[],[],'','')
        ctx.template_candidates=[dict(score=1,loc='F1-P1',locs=['F1-P1'],document_title='交付',title='交付',text='示例文本',stance='',acceptable='',escalate='')]
        import app.redaction.recheck as recheck
        monkeypatch.setattr(recheck,'recheck',lambda *a,**kw:[BlockReason('TERM',0)])
        compare.prepare(db,ctx,None)
        assert ctx.template_section=='' and ctx.template_stats['blocked'] is True


def test_t46_own_preset_and_log_privacy(db_sessionmaker,integration,caplog):
    from app.standards import compare
    from app.db.models import Task,RedactionTerm
    from app.review.prompt import ReviewContext
    from app.redaction.engine import write_full_map,load_redaction_map,term_hash
    from app.security.crypto import encrypt_text
    from app.redaction.restore import restore
    own='示例我方制造有限公司'
    with db_sessionmaker() as db:
        task=Task(owner_id=integration.id,title='示例',stance='sales',source='incoming',policy='ai',status='reviewing')
        db.add(task);db.flush();write_full_map(db,task.id,{})
        db.add(RedactionTerm(kind='custom',category='ORG',is_our_party=True,term_hash=term_hash(own),term_enc=encrypt_text(own)))
        db.commit()
        ctx=ReviewContext(task,'sales','incoming',None,'contract',[],[],'','')
        ctx.template_candidates=[dict(score=1,loc='F1-P1',locs=['F1-P1'],document_title='交付',title='交付',text=own+'依约交付。',stance='',acceptable='',escalate='')]
        compare.prepare(db,ctx,None)
        assert '我方依约交付。' in ctx.template_section and own not in ctx.template_section
        mapping=load_redaction_map(db,task.id)
        assert own in restore(ctx.template_section,mapping)
        assert own not in caplog.text
