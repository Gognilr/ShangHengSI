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
from tests.review_support import env, llm  # noqa: F401

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


def test_a03_no_real_values_in_outbound_records(db_sessionmaker, alice, llm):
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
            problems += [f"{sample.name} {source}：含 must_replace {n!r}" for n in sample.must_replace if n in text]
            # 长数字（账号等）换了分组写法也算出现
            problems += [f"{sample.name} {source}：含 must_replace {n!r}（去掉分隔符后）" for n in sample.must_replace
                         if sum(ch.isdigit() for ch in n) >= 8 and compact(n) in compact(text)]
    assert problems == []
