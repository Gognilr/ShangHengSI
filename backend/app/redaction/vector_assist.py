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
    return [word for word, digest in zip(words, digests) if _DECIDED.get(digest)]
