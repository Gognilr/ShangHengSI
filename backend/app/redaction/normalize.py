"""只用于出口比对；每个输出字符都保留原文下标，不改入库文本。"""
import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.parsing.model import INVISIBLE

RESOURCE = Path(__file__).resolve().parents[2] / 'resources'
NUMBERS = str.maketrans('〇零一二三四五六七八九', '00123456789')


@dataclass
class NormText:
    text: str
    index_map: list[int]


@lru_cache
def _tables():
    return tuple(json.loads((RESOURCE / name).read_text('utf-8'))
                 for name in ('confusables_ascii.json', 't2s_chars.json'))


def normalize_with_map(text: str, *, preserve_case: bool = False) -> NormText:
    confusables, t2s = _tables()
    chunks, indices = [], []
    for i, ch in enumerate(text):
        if ord(ch) in INVISIBLE:
            continue
        normalized = unicodedata.normalize('NFKC', ch)
        normalized = ''.join(t2s.get(confusables.get(c, c), confusables.get(c, c)) for c in normalized)
        normalized = normalized.translate(NUMBERS)
        if not preserve_case:
            normalized = normalized.casefold()
        chunks.append(normalized)
        indices.extend([i] * len(normalized))
    return NormText(''.join(chunks), indices)


def normalize_value(value: str, *, preserve_case: bool = False) -> str:
    return normalize_with_map(value, preserve_case=preserve_case).text
