"""V2-B-06：先验证原始推理对拍，不能用过滤后的输出掩盖不一致。"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/ner'


def corpus():
    source = ROOT / 'docs/v2/reports/ner-selection/prepare_corpus.py'
    spec = importlib.util.spec_from_file_location('ner_prepare', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch('app.redaction.ner.recognize', return_value=[]):
        return module.build(ROOT)['records']


def test_reference_parity():
    from app.redaction import ner
    reference = json.loads((FIXTURES / 'reference_predictions.json').read_text(encoding='utf-8-sig'))
    records = corpus()
    assert len(records) == len(reference) == 391
    largest = 0.0
    for index, record in enumerate(records):
        actual = {(e.start, e.end, e.label): e.score for e in ner.raw_recognize(record['input']) if e.score >= 0.3}
        expected = {(e['start'], e['end'], e['label']): e['score'] for e in reference[record['id']]}
        assert actual.keys() == expected.keys(), record['id']
        delta = max((abs(actual[k] - expected[k]) for k in actual), default=0)
        largest = max(largest, delta)
        assert delta <= 1e-4, (record['id'], delta)
        if index % 50 == 0:
            print('parity nodes', index, flush=True)
    print('parity max score difference', largest)


def test_frozen_holdout():
    from app.redaction import ner
    samples = json.loads((FIXTURES / 'holdout/samples.json').read_text(encoding='utf-8-sig'))['samples']
    assert len(samples) >= 8
    total = hits = 0
    misses = []
    for sample in samples:
        text = sample['text']
        predictions = ner.recognize(text)
        for surface, label in sample['entities']:
            assert text.count(surface) == 1
            start = text.index(surface)
            total += 1
            hit = any((e.start, e.end, e.label) == (start, start + len(surface), label)
                      and e.score >= ner.MEDIUM for e in predictions)
            hits += hit
            if not hit:
                misses.append((sample['id'], surface, label))
        for surface in sample['must_keep']:
            start = text.index(surface)
            assert not any(e.score >= ner.HIGH and e.start < start + len(surface) and start < e.end
                           for e in predictions), sample['id']
    print('holdout hits/total', hits, total, 'misses', misses)
    assert total >= 40
    assert hits / total >= 0.9


def test_reference_quality_and_filtered_false_positives():
    from app.redaction import ner
    reference = json.loads((FIXTURES / 'reference_predictions.json').read_text(encoding='utf-8-sig'))
    false = total = hits = 0
    for record in corpus():
        predictions = [ner.Entity(**p) for p in reference[record['id']]
                       if p['score'] >= ner.MEDIUM and ner.keep_entity(record['input'], ner.Entity(**p))]
        expected = {tuple(e) for e in record['expected']}
        false += sum((e.start, e.end, e.label) not in expected for e in predictions)
        if record['origin'] == 'narrative':
            for a, b, label in expected:
                if label in ('PERSON', 'ORG'):
                    total += 1
                    hits += any((e.start, e.end, e.label) == (a, b, label) for e in predictions)
        for a, b in record['keep']:
            assert not any(e.score >= ner.HIGH and e.start < b and a < e.end for e in predictions), record['id']
    print('narrative hits/total', hits, total, 'filtered false positives', false)
    assert hits / total >= 0.9
    assert false <= 20


def test_engine_masking_and_note(monkeypatch):
    from app.redaction import ner
    from app.redaction.engine import NodeIn, compute
    seen = []

    def predict(text):
        seen.append(text)
        return [ner.Entity(text.index('沈柏宁'), text.index('沈柏宁') + 3, 'PERSON', 0.99)]

    monkeypatch.setattr(ner, 'recognize', predict)
    text = '电话：13800138000，沈柏宁复核。'
    result = compute([NodeIn('NOTE', text)], 'other')
    assert '13800138000' not in seen[0]
    assert len(seen[0]) == len(text)
    assert any(i.detected_by == 'ner' and i.category == 'PERSON' for i in result.items)
    assert '沈柏宁' not in result.redacted['NOTE']


def test_chat_confidence_and_rule_precedence(monkeypatch):
    from app.redaction import ner
    from app.chat.service import _redact

    def predict(text):
        assert '13800138000' not in text
        return [ner.Entity(text.index('沈柏宁'), text.index('沈柏宁') + 3, 'PERSON', 0.99),
                ner.Entity(text.index('陆清禾'), text.index('陆清禾') + 3, 'PERSON', 0.7)]

    monkeypatch.setattr(ner, 'recognize', predict)
    result = _redact('电话：13800138000，沈柏宁通知陆清禾。', {}, [])
    assert '沈柏宁' not in result.text
    assert any(s['text'] == '陆清禾' and s['reason'] == 'ner' for s in result.suspects)


@pytest.mark.parametrize('surface,label', [('甲方', 'ORG'), ('Mr.', 'PERSON'), ('1', 'PERSON'),
                                          ('12 / 34-5', 'ADDR'), (' / ', 'ADDR')])
def test_filter(surface, label):
    from app.redaction import ner
    assert not ner.keep_entity(surface, ner.Entity(0, len(surface), label, 0.99))


@pytest.mark.parametrize('changed', [False, True])
def test_check_rejects_missing_or_changed(monkeypatch, tmp_path, changed):
    from app.redaction import ner
    monkeypatch.setattr(ner, 'MODEL_DIR', tmp_path)
    if changed:
        import hashlib
        for name in ner.FILES:
            (tmp_path / name).write_bytes(b'original')
        digest = hashlib.sha256(b'original').hexdigest()
        (tmp_path / 'MANIFEST.sha256').write_text(''.join(f'{digest}  {name}\n' for name in ner.FILES))
        (tmp_path / 'model.onnx').write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='缺失或 SHA-256'):
        ner.check()
