"""日期格式、坐标持久化与回程闸门；全部为虚构内容。"""
from datetime import date, timedelta
import io

import docx
import pytest

from app.redaction import dates
from app.redaction.engine import NodeIn, compute
from app.redaction.restore import check
from tests.review_support import env, ready_task  # noqa: F401


@pytest.mark.parametrize('raw,expected', [
    ('2026年3月5号', '2026年5月14号'), ('2026-03-05', '2026-05-14'),
    ('2026/3/5', '2026/5/14'), ('5 March 2026', '14 May 2026'),
    ('March 5, 2026', 'May 14, 2026'), ('日期20260305', '日期20260514'),
    ('二〇二六年三月五日', '二〇二六年五月十四日'),
])
def test_date_rendering_and_weekday(raw, expected):
    shifted, pairs = dates.shift_text(raw, 70)
    assert shifted == expected
    before, after = dates.find_dates(raw)[0], dates.find_dates(shifted)[0]
    assert after.value - before.value == timedelta(days=70)
    assert after.value.weekday() == before.value.weekday()
    assert list(pairs.values()) == [raw[before.start:before.end]]


def test_date_boundaries_pairs_and_return_gate():
    from app.checks.dates import check_dates
    from app.checks.model import CheckNode
    text = '签约 2026-03-05，交付 2026年3月5日。日期2026-02-30，编号 X-20260305；20260305；05/06/2026；30 天内。'
    shifted, mapping = dates.shift_text(text, 70)
    assert '2026-05-14' in shifted and '2026年5月14日' in shifted
    assert all(s in shifted for s in ('2026-02-30', 'X-20260305', '；20260305；', '05/06/2026', '30 天内'))
    pairs = {k.split('#D')[0]: v for k, v in mapping.items()}
    checked = check('[2026-05-14]，2025-01-01，2026年5月15日', {}, dates=pairs, clause=True)
    assert checked.text == '[2026-03-05]，2025-01-01，2026年5月15日'
    assert checked.restored == 1 and [m.kind for m in checked.marks] == ['date', 'date']
    assert not check('2025-01-01', {}).marks
    assert dates.shift_text(text, 0) == (text, {})
    assert dates.find_dates('日期2026-03/05') == []
    assert [r.code for r in check_dates([CheckNode('F1-P1', '签约日期20260305，交付日期20260301', 1, None)], date(2026, 3, 1))] == ['L-08']


def test_switch_off_and_assistant_keep_existing_output():
    nodes = [NodeIn('F1-P1', '签约日期2026-03-05，交付2026年4月5日。')]
    before = compute(nodes, 'sales')
    off = compute(nodes, 'sales', date_shift_days=0)
    assert before.redacted == off.redacted and before.mapping == off.mapping
    neutral = compute(nodes, 'sales', style='neutral', date_shift_days=70)
    assert not any(it.category == 'DATE' for it in neutral.items)


def test_persisted_shift_and_preview_use_same_coordinate(db_sessionmaker, make_user, ready_task, monkeypatch):
    from app.admin.settings_store import set_setting
    from app.db.models import Task
    from app.redaction import engine
    from app.chat.service import redact_question, _redact_and_record
    owner = make_user('date_owner')
    with db_sessionmaker() as db:
        set_setting(db, 'redaction.date_shift', True, owner.id)
        db.commit()
    document = docx.Document()
    document.add_paragraph('卖方：清原虚构有限公司')
    document.add_paragraph('签约日期：2026-03-05。')
    stream = io.BytesIO(); document.save(stream)
    monkeypatch.setattr(engine.secrets, 'choice', lambda choices: 10)
    task = ready_task(owner, [('日期合同.docx', stream.getvalue())])
    with db_sessionmaker() as db:
        task = db.get(Task, task.id)
        assert engine._load_full_map(db, task.id)['@date_shift_days'] == '70'
        assert '文中日期已整体平移' in engine.build_outbound_text(db, task)
        monkeypatch.setattr(engine.secrets, 'choice', lambda choices: -10)
        engine.redact_task(db, task)
        assert engine._load_full_map(db, task.id)['@date_shift_days'] == '70'
        assert not any(k.startswith('@') for k in engine.load_redaction_map(db, task.id))
        assert '70' not in engine.load_real_values(db, task.id)
        assert not any(k.startswith('@') for k in engine.load_placeholders(db, task.id))
        preview, changed = redact_question(db, task, '交付日期2026-04-05可以吗？')
        sent = _redact_and_record(db, task, '交付日期2026-04-05可以吗？')
        assert changed and preview == sent and '2026-06-14' in sent
        assert check(sent, engine.load_redaction_map(db, task.id), dates=engine.load_date_pairs(db, task.id)).text == '交付日期2026-04-05可以吗？'
