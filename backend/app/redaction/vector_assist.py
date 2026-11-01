"""V31-S-06：仅判断不确定项的离线稠密向量参照，不记录原文或查询向量。"""
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.embedding import engine as embedding
from app.redaction import keep_terms, parties

THRESHOLD = 0.5973327159881593
REFERENCE = Path(__file__).resolve().parents[2] / 'resources/uncertain_reference.npz'


@lru_cache(maxsize=1)
def reference():
    with np.load(REFERENCE, allow_pickle=False) as data:
        return data['vectors'], data['ordinary']


def keep_words(nodes, found, existing: set[str]) -> list[str]:
    flat = '\n'.join(n.text for n in nodes)
    texts = {n.loc: n.text for n in nodes}
    words, contexts, seen = [], [], set(existing)
    for item in found:
        word = texts[item.loc][item.char_start:item.char_end]
        key = parties.normalize(word)
        if key in seen:
            continue
        seen.add(key)
        if not (item.category == 'TERM' or item.category == 'NAME' and keep_terms.eligible(word, item.reason)):
            continue
        start = flat.index(word)
        words.append(word)
        contexts.append(flat[max(0, start - 40):start] + '【' + word + '】' + flat[start + len(word):start + len(word) + 40])
    if not words:
        return []
    vectors, ordinary = reference()
    queries = embedding.embed(contexts, 'passage')
    kept = []
    for word, query in zip(words, queries):
        scores = np.clip(vectors @ query, -1, 1)
        nearest = np.argsort(-scores, kind='stable')[:5]
        if len(nearest) == 5 and ordinary[nearest].all() and float(scores[nearest[0]]) >= THRESHOLD:
            kept.append(word)
    return kept
