"""V31-S-06：仅判断不确定项的离线稠密向量参照，不记录原文或查询向量。"""
import hashlib
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.embedding import engine as embedding
from app.redaction import keep_terms, parties

THRESHOLD = 0.5973327159881593
REFERENCE = Path(__file__).resolve().parents[2] / 'resources/uncertain_reference.npz'
# 确认页每处理一条不确定项都要整份重算；同一上下文的判断结果不变，按摘要在进程内记住，
# 不再重复计算向量（V31-U5F-01）。只存摘要与布尔值，不存原文或向量。
_DECIDED: OrderedDict[str, bool] = OrderedDict()
_DECIDED_MAX = 50000


@lru_cache(maxsize=1)
def reference():
    with np.load(REFERENCE, allow_pickle=False) as data:
        return data['vectors'], data['ordinary']


def keep_spans(nodes, found, existing: set[str]) -> set[tuple[str, int, int]]:
    """逐个出现位置判断：上下文取该位置前后各 40 字，结果只作用于该位置 (loc, 起, 止)。"""
    flat = '\n'.join(n.text for n in nodes)
    texts = {n.loc: n.text for n in nodes}
    offsets, at = {}, 0
    for n in nodes:
        offsets[n.loc] = at
        at += len(n.text) + 1
    words, contexts = [], []
    for item in found:
        word = texts[item.loc][item.char_start:item.char_end]
        if parties.normalize(word) in existing:
            continue
        if not (item.category == 'TERM' or item.category == 'NAME' and keep_terms.eligible(word, item.reason)):
            continue
        start = offsets[item.loc] + item.char_start
        words.append((item.loc, item.char_start, item.char_end))
        contexts.append(flat[max(0, start - 40):start] + '【' + word + '】' + flat[start + len(word):start + len(word) + 40])
    if not words:
        return set()
    digests = [hashlib.sha256(context.encode('utf-8')).hexdigest() for context in contexts]
    missing = [i for i, digest in enumerate(digests) if digest not in _DECIDED]
    if missing:
        vectors, ordinary = reference()
        queries = embedding.embed([contexts[i] for i in missing], 'passage')
        for i, query in zip(missing, queries):
            scores = np.clip(vectors @ query, -1, 1)
            nearest = np.argsort(-scores, kind='stable')[:5]
            _DECIDED[digests[i]] = bool(len(nearest) == 5 and ordinary[nearest].all()
                                        and float(scores[nearest[0]]) >= THRESHOLD)
            if len(_DECIDED) > _DECIDED_MAX:
                _DECIDED.popitem(last=False)
    for digest in digests:
        if digest in _DECIDED:
            _DECIDED.move_to_end(digest)
    return {span for span, digest in zip(words, digests) if _DECIDED.get(digest)}
