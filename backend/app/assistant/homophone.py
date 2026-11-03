"""同音建议只比较等长读音交集，不做模糊音；输入输出都只在本机。"""
import json
import re
from functools import cache

from app.redaction.dictionaries import RESOURCES_DIR, zh_words


@cache
def readings():
    return {k:frozenset(v) for k,v in json.loads((RESOURCES_DIR / 'hanzi_pinyin.json').read_text(encoding='utf-8')).items()}


def suggest(text, candidates, *, sounds=None, common=None):
    sounds = readings() if sounds is None else sounds
    common = zh_words() if common is None else common
    candidates = sorted({c for c in candidates if 2 <= len(c) <= 8 and re.fullmatch('[一-鿿]+', c)})
    out = []
    for target in candidates:
        for start in range(len(text) - len(target) + 1):
            value = text[start:start+len(target)]
            if value in candidates or value in common:
                continue
            if all(sounds.get(a, frozenset()) & sounds.get(b, frozenset()) for a,b in zip(value,target)):
                out.append({'start':start, 'end':start+len(target), 'from':value, 'to':target})
    return out
