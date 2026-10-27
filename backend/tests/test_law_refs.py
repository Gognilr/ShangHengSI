"""T-45：本地法条检索、逐条预检和引用快照。"""
import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import Law, LawArticle, Task
from app.admin.settings_store import set_setting
from app.review.prompt import ReviewContext, build_user_message, fixed_prompt_text, system_prompt
from app.review.validate import validate_output


def vector(i=0):
    return [1.0 if n==i else 0.0 for n in range(1024)]


def ctx(task=None):
    return ReviewContext(task or SimpleNamespace(id=uuid.uuid4(),policy='ai',owner_id=uuid.uuid4()),
                         'sales','incoming',None,'contract',[],[],'','[F1-P1] 交付条款')


def test_t45_disabled_and_local_only(db_sessionmaker,monkeypatch):
    from app.laws import retrieve
    c=ctx()
    original=build_user_message(c)
    monkeypatch.setattr(retrieve.engine,'embed',lambda *a:pytest.fail('不得加载引擎'))
    with db_sessionmaker() as db:
        retrieve.prepare(db,c,None,{})
        assert build_user_message(c)==original and '【参考法条】' not in original
        set_setting(db,'laws.refs_enabled',True,None)
        c.task.policy='local_only'
        retrieve.prepare(db,c,None,{})
        assert build_user_message(c)==original


def test_t45_bounded_sorted_and_dropped():
    from app.laws.retrieve import select_refs
    from app.redaction.recheck import RecheckContext
    candidates=[dict(id=n,title='示例供货法',article_no=f'第{n}条',text='条文内容'*90,score=1-n/1000) for n in range(25)]
    candidates[0]['text']='示例公司依约供货'
    refs,stats=select_refs(candidates,RecheckContext(terms=['示例公司']))
    assert len(refs)<=20 and stats['chars']<=8000 and stats['dropped']==1
    assert refs[0]['id']==1
    assert [r['ref'] for r in refs]==[f'LAW{i+1}' for i in range(len(refs))]


@pytest.mark.parametrize('status,category,expected',[('valid','法律',1),('valid','司法解释',1),
    ('repealed','法律',0),('amended','法律',0),('valid','地方性法规',0)])
def test_t45_candidate_filter(db_sessionmaker,status,category,expected):
    from app.laws.retrieve import search
    with db_sessionmaker() as db:
        law=Law(title='示例供货法',category=category,status=status,source='import',published_on=date(2026,1,1))
        db.add(law);db.flush()
        db.add(LawArticle(law_id=law.id,seq=1,article_no='第一条',text='交付约定',embedding=vector()))
        db.commit()
        assert len(search(db,[vector()]))==expected


def test_t45_snapshot_unknown_ref_and_unverified(db_sessionmaker):
    from app.laws.retrieve import verify_findings
    c=ctx(); c.law_refs=[dict(ref='LAW2',title='示例供货法',article_no='第三条',text='当时的条文')]
    output=dict(requirement_results=[],findings=[dict(title='违约条款',problem='《中华人民共和国合同法》第一百零七条',
                                                    law_refs=['LAW2','LAW99'])])
    result=validate_output(json.dumps(output),c,{})
    assert result.findings[0].law_refs==[dict(title='示例供货法',article_no='第三条',text='当时的条文')]
    with db_sessionmaker() as db:
        verify_findings(db,result.findings)
        assert result.findings[0].law_unverified is True


@pytest.mark.parametrize('article,expected',[('第三条',False),('第三十三条',True)])
def test_t45_article_verification(db_sessionmaker,article,expected):
    from app.laws.retrieve import verify_findings
    from app.db.base import utcnow
    with db_sessionmaker() as db:
        law=Law(title='中华人民共和国示例供货合同法',category='法律',status='valid',source='import',
                published_on=date(2026,1,1),fulltext_at=utcnow())
        db.add(law);db.flush();db.add(LawArticle(law_id=law.id,seq=1,article_no='第三条',text='约定'))
        db.flush()
        f=SimpleNamespace(problem=f'《示例供货合同法》{article}',basis=None,suggestion=None,law_unverified=False)
        verify_findings(db,[f])
        assert f.law_unverified==expected


def test_t45_prompt_and_coverage(db_sessionmaker):
    from app.review.coverage import describe
    version,system=system_prompt()
    assert version!='2026.11-1' and 'law_refs' in system
    with db_sessionmaker() as db: assert '【参考法条】' in fixed_prompt_text(db)
    assert '本次附带相关法条 2 条（因出口复检丢弃 1 条）' in describe(dict(law_refs=dict(enabled=True,sent=2,dropped=1,chars=20)),{})


def test_t45_owner_guard(db_sessionmaker):
    from app.laws.retrieve import require_owner
    from app.security.errors import AppError
    with db_sessionmaker() as db:
        with pytest.raises(AppError) as exc: require_owner(db,uuid.uuid4(),uuid.uuid4())
        assert exc.value.status==404


def test_t45_question_limit_and_original_query(db_sessionmaker,monkeypatch):
    from app.laws import retrieve
    queries=[]
    monkeypatch.setattr(retrieve.engine,'embed',lambda texts,kind: queries.append((texts,kind)) or [vector()])
    monkeypatch.setattr(retrieve,'search',lambda *args:[dict(id=i,title='示例供货法',article_no='第三条',
                                                          text='应依约交付。',score=0.9) for i in range(10)])
    task=SimpleNamespace(id=None,policy='ai')
    with db_sessionmaker() as db:
        assert retrieve.question_refs(db,task,'问题原文')==''
        set_setting(db,'laws.refs_enabled',True,None)
        section=retrieve.question_refs(db,task,'问题原文')
        assert section.count('[LAW')==5 and queries==[(['问题原文'],'query')]


@pytest.mark.parametrize('role,expected',[('user',403),('sys_admin',403),('biz_admin',200),('admin',200)])
def test_t45_law_setting_permission(make_user,login,role,expected):
    make_user('law_setting',role=role)
    c,csrf=login('law_setting')
    assert c.get('/api/v1/admin/settings/laws').status_code==expected
    response=c.put('/api/v1/admin/settings/laws',json=dict(refs_enabled=True),headers={'X-CSRF-Token':csrf})
    assert response.status_code==expected


@pytest.mark.parametrize('category', ['宪法相关法', '民法商法', '行政法', '经济法',
                                    '社会法', '生态环境法', '刑法', '诉讼与非诉讼程序法'])
def test_e08_law_subcategories_in_retrieval(db_sessionmaker, category):
    from app.laws.retrieve import search
    with db_sessionmaker() as db:
        law=Law(title='示例法',category=category,status='valid',source='import',published_on=date(2020,1,1))
        db.add(law); db.flush()
        article=LawArticle(law_id=law.id,seq=1,article_no='第一条',text='交付约定',embedding=vector())
        db.add(article); db.commit()
        assert [row['id'] for row in search(db,[vector()])]==[article.id]
        assert law.category==category
        law.status='repealed'; db.commit()
        assert search(db,[vector()])==[]


def test_t44a_10_unnumbered_is_not_first_article(db_sessionmaker):
    from app.laws.retrieve import verify_findings
    from app.db.base import utcnow
    with db_sessionmaker() as db:
        law = Law(title='示例批复', category='司法解释', status='valid', source='import', fulltext_at=utcnow())
        db.add(law); db.flush()
        db.add(LawArticle(law_id=law.id, seq=1, article_no=None, text='一、示例内容。'))
        db.flush()
        finding = SimpleNamespace(problem='《示例批复》第一条', basis=None, suggestion=None, law_unverified=False)
        verify_findings(db, [finding])
        assert finding.law_unverified is True


def test_t44a_9_null_reference_snapshot_and_report(monkeypatch):
    import io
    from docx import Document
    from app.laws.retrieve import section
    from app.export import report_docx
    ref = dict(title='示例批复', article_no=None, text='一、示例正文。')
    c = ctx(); c.law_refs = [dict(ref='LAW1', **ref)]
    result = validate_output(json.dumps(dict(requirement_results=[], findings=[
        dict(title='示例问题', problem='需确认示例事项。', law_refs=['LAW1'])])), c, {})
    assert result.findings[0].law_refs == [ref]
    assert '[LAW1]《示例批复》：一、示例正文。' in section(c.law_refs)
    # 复用结果页快照路径并调用实际报告构建函数，其他段落用最小替身隔离。
    finding = SimpleNamespace(seq=1, severity='low', category='legal', locs=[], missing=True,
        quote=None, title='示例问题', problem='需确认。', impact=None, basis=None, suggestion=None,
        suggested_text=None, law_refs=[ref], law_unverified=False, source='model',
        cite_status='missing', handling_status='pending', handling_note=None, location=None)
    detail = SimpleNamespace(template=None, coverage=None, findings=[finding],
        sanctions=SimpleNamespace(level='attention', reasons=[]), requirement_results=[])
    monkeypatch.setattr(report_docx, 'run_detail', lambda *args: detail)
    monkeypatch.setattr(report_docx, '_files', lambda *args: ([], None))
    monkeypatch.setattr(report_docx, '_basic', lambda *args: None)
    monkeypatch.setattr(report_docx, '_local', lambda *args: None)
    monkeypatch.setattr(report_docx, '_filename', lambda *args: '示例报告.docx')
    _, data = report_docx.build_run_report(SimpleNamespace(get=lambda *args: None), SimpleNamespace(task_id=None), None)
    doc = Document(io.BytesIO(data))
    rendered = '\n'.join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    assert '依据：《示例批复》' in rendered and '一、示例正文。' in rendered
    assert 'None' not in rendered and 'null' not in rendered
    from pathlib import Path
    component = (Path(__file__).resolve().parents[2] / 'frontend/src/components/FindingCard.vue').read_text(encoding='utf-8')
    assert "law.article_no ?? ''" in component and '{{ law.text }}' in component
