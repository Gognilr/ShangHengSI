"""V312Q-01：向量辅助逐个出现位置取上下文，保留结果只作用于该位置。"""
import numpy as np

from app.redaction import vector_assist
from app.redaction.engine import NodeIn, SuspectOut


def test_context_follows_occurrence_and_result_is_per_span(monkeypatch):
    seen = []
    monkeypatch.setattr(vector_assist, 'reference', lambda: (np.array([[1.0, 0.0]] * 5), np.array([True] * 5)))
    # 仅当上下文来自 F1（含“普通”）时判为普通词，模拟向量只看上下文
    monkeypatch.setattr(vector_assist.embedding, 'embed',
                        lambda texts, kind: seen.extend(texts) or [np.array([1.0, 0.0] if '普通' in t else [0.0, 1.0]) for t in texts])
    monkeypatch.setattr(vector_assist, 'THRESHOLD', 0.9)
    monkeypatch.setattr(vector_assist.keep_terms, 'eligible', lambda word, reason: True)
    vector_assist._DECIDED.clear()
    nodes = [NodeIn('F1-P1', '，' * 50 + '普通星火', None, 'paragraph'), NodeIn('F2-P1', '，' * 50 + '合同方星火签署', None, 'paragraph')]
    found = [SuspectOut('F1-P1', 52, 54, 'ner'), SuspectOut('F2-P1', 53, 55, 'ner')]
    for s in found:
        s.category = 'NAME'
    kept = vector_assist.keep_spans(nodes, found, set())
    assert kept == {('F1-P1', 52, 54)}
    assert any('合同方【星火】签署' in c for c in seen)
