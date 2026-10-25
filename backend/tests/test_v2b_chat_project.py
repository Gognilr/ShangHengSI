"""V2Q-47：追问项目整段优先，预览/发送都由服务端重新计算。"""
import json
import io

import pytest
from docx import Document
from sqlalchemy import select

from app.chat.service import _redact, _load_full_map
from app.db.models import LlmCall
from app.redaction.restore import restore
from tests.review_support import env, llm, ready_task  # noqa: F401
from tests.test_ner_api import alice  # noqa: F401
from tests.test_user_note_redaction import complete, task_url
from tests.acceptance_support import settle_suspects


NAME = '江北市虚构医院'
PROJECT = NAME + '新增信息化改造项目'
MESSAGE = f'项目名称：{PROJECT}；{NAME}负责验收。'


@pytest.mark.parametrize('known', [False, True])
def test_new_project_containing_known_party_is_complete_and_reversible(known):
    mapping = {'我方': NAME}
    if known:
        mapping['项目_7'] = PROJECT
    result = _redact(MESSAGE, mapping, [])
    code = '项目_7' if known else '[项目_Q1]'
    assert result.text == f'项目名称：{code}；我方负责验收。'
    assert result.new_entries == ({} if known else {code: PROJECT})
    assert restore(result.text, {**mapping, **result.new_entries}) == MESSAGE
    assert not result.suspects


def test_partial_crossing_keeps_existing_priority_and_only_used_entries():
    text = '项目名称：' + PROJECT
    # 已有自定义词横跨项目前缀与项目值，按授权保持先到先得，不能删掉半个区间。
    mapping = {'[敏感词_Q1]': '项目名称：江北市虚构医院'}
    result = _redact(text, mapping, [])
    assert result.text == '[敏感词_Q1]新增信息化改造项目'
    assert result.new_entries == {}


def test_project_does_not_override_other_category_priority():
    text = '项目名称：合同总价：100元采购项目'
    result = _redact(text, {}, [])
    # V2Q-50：只同步本条旧期望，项目整段优先于内部金额；未用代号不得入表。
    assert len(result.new_entries) == 1
    code, value = next(iter(result.new_entries.items()))
    assert code.startswith('[项目_Q') and value == text[5:]
    assert result.text == f'项目名称：{code}' and '采购项目' not in result.text
    assert restore(result.text, result.new_entries) == text


def test_project_containing_address_has_same_priority_in_body_and_chat():
    from app.redaction.engine import NodeIn, compute
    text = '项目名称：东川市长江路88号仓库改造项目'
    body = compute([NodeIn('F1-P1', text)], 'other')
    assert len(body.items) == 1 and body.items[0].category == 'PROJECT'
    chat = _redact(text, {}, [])
    assert len(chat.new_entries) == 1
    code, value = next(iter(chat.new_entries.items()))
    assert code.startswith('[项目_Q') and value == text[5:]
    assert chat.text == f'项目名称：{code}'
    assert restore(body.redacted['F1-P1'], body.mapping) == restore(chat.text, chat.new_entries) == text


@pytest.mark.usefixtures('env')
def test_project_preview_and_send_do_not_expose_tail(alice, ready_task, db_sessionmaker, llm):
    user, client, headers = alice
    doc = Document()
    doc.add_paragraph(f'买方：{NAME}')
    doc.add_paragraph('卖方：澄川虚构信息技术有限公司')
    data = io.BytesIO()
    doc.save(data)
    task = ready_task(user, files=[('虚构采购合同.docx', data.getvalue())], stance='purchase')
    settle_suspects(client, headers, task.id, [])
    complete(db_sessionmaker, client, headers, task, llm)
    preview = client.post(task_url(task, '/chat/preview'), headers=headers, json={'message': MESSAGE})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body['redacted'] == '项目名称：[项目_Q1]；我方负责验收。'
    assert body['suspects'] == []
    requests = llm('normal')
    sent = client.post(task_url(task, '/chat'), headers=headers, json={'message': MESSAGE, 'decisions': []})
    assert sent.status_code == 200, sent.text
    assert requests
    assert '新增信息化改造项目' not in json.dumps(json.loads(requests[0].content), ensure_ascii=False)
    with db_sessionmaker() as db:
        mapping = _load_full_map(db, task.id)
        assert mapping['[项目_Q1]'] == PROJECT
        assert restore(body['redacted'], mapping) == MESSAGE
        calls = list(db.scalars(select(LlmCall).where(LlmCall.task_id == task.id, LlmCall.purpose == 'chat')))
        assert len(calls) == 1 and calls[0].status == 'sent'
        assert '新增信息化改造项目' not in calls[0].request_text
