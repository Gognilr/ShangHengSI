"""A-03（SPEC 23.1，设计 docs/design/M4.md 第七节）：外发记录中出现对照表真实值、已记住的名称、
未替换的电话 / 账号 / 带币种金额的次数为 0。

对脱敏回归集和 fixtures 中的全部样本走真实接口 + 模拟模型（must_suspect 选替换，其余疑似遗漏忽略），
全部样本在同一个库中依次处理（前面任务记住的名称会作用于后面的任务）。然后对每一次外发，检查
llm_calls.request_text 和模拟服务实际收到的请求：
* 本任务对照表中的真实值、发送时已存在的已记住名称和自定义敏感词都不出现（按出口复检的比较规则，Q-19）；
* 按 9.3 第 4、5 步重扫，没有未替换的电话、邮箱、账号、证件号、带币种的金额；
* 样本标注的 must_replace 文字不出现。
需要 TEST_DATABASE_URL；含 OCR 样本，耗时较长。
"""
import pytest
from sqlalchemy import select

from app.db.models import RedactionTerm, Task
from app.redaction.recheck import recheck
from tests.acceptance_support import compact, fixture_samples, golden_samples, outbound_records, run_sample
from tests.review_support import env, llm, ready_task  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def alice(make_user, login):
    make_user("alice")
    client, csrf = login("alice")
    return client, {"X-CSRF-Token": csrf}


def _culprit(text, reason, values, terms) -> str:
    """诊断用：命中该位置的真实值或词条（样本全部虚构，可以写进断言消息）。"""
    pool = values if reason.category == "MAP" else terms if reason.category == "TERM" else []
    for v in pool:
        if any(r.position == reason.position for r in recheck(text, real_values=[v], terms=[])):
            return repr(v)
    return repr(text[reason.position:reason.position + 30])


def _must_replace_present(sample_name: str, needle: str, text: str) -> bool:
    # V2Q-58：只排除该样本该标注与固定要求编号的碰撞，正文短串仍严格检查。
    if sample_name == 'amounts_cn_forms.docx' and needle == '15':
        text = text.replace('[C-15]', '')
    return needle in text


def test_v2q58_unredacted_body_still_fails():
    assert not _must_replace_present('amounts_cn_forms.docx', '15', '[C-15] 质保期要求')
    with pytest.raises(AssertionError):
        assert not _must_replace_present('amounts_cn_forms.docx', '15', '[C-15] 正文金额15万欧元')
    assert _must_replace_present('other.docx', '15', '[C-15]')
    assert _must_replace_present('amounts_cn_forms.docx', 'C-15', '[C-15]')


def test_a03_no_real_values_in_outbound_records(db_sessionmaker, alice, llm, monkeypatch):
    c, h = alice
    requests = llm("normal")
    samples = golden_samples() + fixture_samples()
    assert len(samples) >= 20
    task_of = {run_sample(c, h, db_sessionmaker, s): s for s in samples}

    with db_sessionmaker() as db:
        assert db.scalar(select(Task.id).where(Task.status != "reviewed")) is None
        assert db.scalar(select(RedactionTerm.id).where(RedactionTerm.kind == "learned")) is not None
    records = outbound_records(db_sessionmaker, requests)
    # 每个样本恰好发送一次，且全部 sent（被出口复检拦截的会是 blocked）
    assert sorted(r.task_id for r in records) == sorted(task_of)
    assert {r.status for r in records} == {"sent"}
    assert sum(len(r.real_values) for r in records) > 200 and any(r.terms for r in records)

    problems = []
    for r in records:
        sample = task_of[r.task_id]
        for source, text in (("llm_calls", r.request_text), ("request", r.wire_text)):
            assert sample.name and text
            for reason in recheck(text, real_values=r.real_values, terms=r.terms):
                problems.append(f"{sample.name} {source}：{reason.category} @ {reason.position} "
                                f"{_culprit(text, reason, r.real_values, r.terms)}")
            problems += [f"{sample.name} {source}：含 must_replace {n!r}" for n in sample.must_replace
                         if _must_replace_present(sample.name, n, text)]
            # 长数字（账号等）换了分组写法也算出现
            problems += [f"{sample.name} {source}：含 must_replace {n!r}（去掉分隔符后）" for n in sample.must_replace
                         if sum(ch.isdigit() for ch in n) >= 8 and compact(n) in compact(text)]
    assert problems == []

    # V31Q-18/20：golden 标注的历史映射复现，仍经过真实确认接口和模拟网关。
    import uuid
    import yaml
    from app.db.models import LlmCall, LlmModel
    from app.llm import gateway
    from app.main import API_PREFIX
    from app.redaction import engine, ner
    from tests.acceptance_support import GOLDEN
    from tests.test_assistant_sessions import document, parse
    for path in sorted(GOLDEN.glob('*.yaml')):
        case = yaml.safe_load(path.read_text(encoding='utf-8')).get('history_keep')
        if not case:
            continue
        word = case['text']
        assert word in path.with_suffix('.txt').read_text(encoding='utf-8')
        # 此处验证历史映射生命周期，识别本身由上面的真实 golden 全集覆盖。
        with monkeypatch.context() as local:
            local.setattr(ner, 'recognize', lambda text: [])
            tid = c.post(API_PREFIX + '/assistant/sessions', headers=h).json()['id']
            base = API_PREFIX + '/assistant/sessions/' + tid
            assert c.post(base + '/files', headers=h, files={'files': ('虚构术语.docx', document(word))}).status_code == 200
            parse(db_sessionmaker, tid)
            endpoint = API_PREFIX + '/tasks/' + tid + '/redaction'
            candidate = c.get(endpoint).json()['suspects'][0]
            assert c.post(endpoint + f"/suspects/{candidate['id']}", headers=h, json={'decision': 'ignore'}).status_code == 200
            with db_sessionmaker() as db:
                task = db.get(Task, uuid.UUID(tid))
                mapping = engine._load_full_map(db, task.id)
                mapping[case['code']] = word
                engine.write_full_map(db, task.id, mapping)
                db.commit()
            response = c.post(base + '/confirm', headers=h)
            assert response.status_code == 200, response.text
            with db_sessionmaker() as db:
                task = db.get(Task, uuid.UUID(tid))
                assert engine.load_redaction_map(db, task.id)[case['code']] == word
                values = engine.load_real_values(db, task.id)
                assert word not in values
                before = len(requests)
                gateway.call_model(db, model_id=db.scalar(select(LlmModel.id)), purpose='assist',
                                   messages=[{'role': 'user', 'content': word}], user_id=task.owner_id, task_id=task.id)
                assert len(requests) == before + 1
                assert recheck(word, real_values=values, terms=[]) == []
                assert db.scalar(select(LlmCall.status).order_by(LlmCall.id.desc()).limit(1)) == 'sent'


def test_v31q20_another_current_replacement_still_blocks(db_sessionmaker, make_user, ready_task):
    """唯一追加反例：同一文字在本轮另一处仍被替换，不能从复检值中豁免。"""
    from app.db.models import DocNode, RedactionItem, RedactionSuspect
    from app.redaction import engine
    from app.security.crypto import encrypt_text
    word = 'Recarburizer Blend'
    task = ready_task(make_user('current_duplicate'))
    with db_sessionmaker() as db:
        nodes = list(db.scalars(select(DocNode).where(DocNode.task_id == task.id, DocNode.part == 'body').order_by(DocNode.ord)))
        nodes[0].text_enc = nodes[1].text_enc = encrypt_text(word)
        db.add(RedactionSuspect(task_id=task.id, loc=nodes[0].loc, char_start=0, char_end=len(word),
                               reason='capitalized', decision='ignore'))
        db.add(RedactionItem(task_id=task.id, loc=nodes[1].loc, char_start=0, char_end=len(word),
                            category='NAME', placeholder='[名称_7]', detected_by='uncertain'))
        engine.write_full_map(db, task.id, {'[名称_7]': word})
        db.flush()
        values = engine.load_real_values(db, task.id)
        assert word in values
        assert any(r.category == 'MAP' for r in recheck(word, real_values=values, terms=[]))
