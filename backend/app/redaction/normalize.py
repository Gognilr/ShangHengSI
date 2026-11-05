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


@lru_cache(maxsize=8192)
def normalize_value(value: str, *, preserve_case: bool = False) -> str:
    """单个值（对照表真实值、代号、词条）的归一化。出口复检逐段调用时同一批值会反复出现，
    长文档的脱敏自检要调用上百万次（V31-U5F-03 测试机实测），按值缓存；只在进程内、有上限，不写日志、不持久化。"""
    return normalize_with_map(value, preserve_case=preserve_case).text
