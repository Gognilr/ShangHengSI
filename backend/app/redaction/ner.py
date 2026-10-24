"""离线 GLiNER markerV0 推理（V2-B 6.2/6.3）。不下载文件，不记录输入。"""
import hashlib
import json
import re
import threading
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_DIR = Path(__file__).resolve().parents[2] / 'models/ner'
MEDIUM = 0.4
HIGH = 0.95
LABELS = ('PERSON', 'ORG', 'ADDR')
FILES = ('model.onnx', 'tokenizer.json', 'tokenizer_config.json',
         'special_tokens_map.json', 'added_tokens.json', 'gliner_config.json')
_SPLIT = re.compile(r"[一-鿿]|[A-Za-zÀ-ÖØ-öø-ÿĀ-ſ]+(?:['’\-][A-Za-zÀ-ÖØ-öø-ÿĀ-ſ]+)*|\d+(?:\.\d+)?|[^\s]")
_lock = threading.Lock()
_model = None
_threads = 4


@dataclass(frozen=True)
class Entity:
    start: int
    end: int
    label: Literal['PERSON', 'ORG', 'ADDR']
    score: float


def check(*, load: bool = False) -> None:
    """app 只核验；worker 在进入主循环前校验并加载。"""
    global _threads
    try:
        lines = (MODEL_DIR / 'MANIFEST.sha256').read_text(encoding='ascii').splitlines()
        manifest = dict((name, digest) for digest, name in (line.split() for line in lines))
        if set(manifest) != set(FILES) or len(lines) != len(FILES):
            raise ValueError
        for name in FILES:
            digest = hashlib.sha256()
            with (MODEL_DIR / name).open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != manifest[name]:
                raise ValueError
    except (OSError, ValueError):
        raise RuntimeError('实体识别模型文件缺失或 SHA-256 校验失败') from None
    if load:
        _threads = 8
        _get_model()


class _Recognizer:
    def __init__(self):
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = _threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(MODEL_DIR / 'model.onnx'), sess_options=options,
                                            providers=['CPUExecutionProvider'])
        self.tokenizer = Tokenizer.from_file(str(MODEL_DIR / 'tokenizer.json'))
        config = json.loads((MODEL_DIR / 'tokenizer_config.json').read_text())
        # 官方 tokenizer 的超大哨兵值表示无最大长度；不能转换为 Rust usize。
        if config['model_max_length'] < 10**9:
            self.tokenizer.enable_truncation(max_length=config['model_max_length'])
        else:
            self.tokenizer.no_truncation()
        self.width = json.loads((MODEL_DIR / 'gliner_config.json').read_text())['max_width']

    def window(self, text: str) -> list[Entity]:
        words = list(_SPLIT.finditer(text))
        if not words:
            return []
        prompt = ['<<ENT>>', 'person', '<<ENT>>', 'organization', '<<ENT>>', 'street address', '<<SEP>>']
        encoding = self.tokenizer.encode(prompt + [m.group() for m in words], is_pretokenized=True)
        mask, previous, seen = [], None, 0
        for word in encoding.word_ids:
            if word is None:
                mask.append(0)
            elif word != previous:
                seen += 1
                mask.append(max(0, seen - len(prompt)))
            else:
                mask.append(0)
            previous = word
        count = len(words)
        spans = np.array([(i, i + j) for i in range(count) for j in range(self.width)], dtype=np.int64)
        feed = {
            'input_ids': np.array([encoding.ids], dtype=np.int64),
            'attention_mask': np.array([encoding.attention_mask], dtype=np.int64),
            'words_mask': np.array([mask], dtype=np.int64),
            'text_lengths': np.array([[count]], dtype=np.int64),
            'span_idx': spans[None, :, :],
            'span_mask': (spans[:, 1] < count)[None, :],
        }
        logits = self.session.run(None, feed)[0][0]
        # float32 sigmoid，与官方解码相同：先按0.01取候选，再按分数贪心排除重叠。
        probs = 1 / (1 + np.exp(-np.clip(logits, -80, 80)))
        candidates = []
        for start, width, label in zip(*np.where(probs > 0.01)):
            end = start + width
            if end < count:
                candidates.append(Entity(words[start].start(), words[end].end(), LABELS[label],
                                         float(probs[start, width, label])))
        accepted = []
        for entity in sorted(candidates, key=lambda e: -e.score):
            if not any(e.start < entity.end and entity.start < e.end for e in accepted):
                accepted.append(entity)
        return sorted(accepted, key=lambda e: e.start)


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                check()
                try:
                    _model = _Recognizer()
                except Exception:
                    raise RuntimeError('实体识别模型加载失败') from None
    return _model


def raw_recognize(text: str) -> list[Entity]:
    """对拍用原始解码，保留0.01以上结果，不应用产品过滤。"""
    if not text.strip():
        return []
    output = {}
    model = _get_model()
    for offset in range(0, len(text), 288):
        for e in model.window(text[offset:offset + 320]):
            entity = Entity(e.start + offset, e.end + offset, e.label, e.score)
            key = (entity.start, entity.end, entity.label)
            if key not in output or output[key].score < entity.score:
                output[key] = entity
    return sorted(output.values(), key=lambda e: (e.start, e.end, e.label))


def recognize(text: str) -> list[Entity]:
    return [e for e in raw_recognize(text) if keep_entity(text, e)]


def mask(text: str, occupied: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in occupied:
        chars[start:end] = ' ' * (end - start)
    return ''.join(chars)


@lru_cache(maxsize=1)
def _stopwords():
    path = Path(__file__).resolve().parents[2] / 'resources/ner_stopwords.txt'
    return {line.strip().casefold() for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.startswith('#')}


def keep_entity(text: str, entity: Entity) -> bool:
    surface = text[entity.start:entity.end].strip()
    return (len(surface) >= 2 and surface.casefold() not in _stopwords()
            and re.fullmatch(r'[\d\s\-/.]+', surface) is None
            and (entity.label != 'ADDR' or re.search(r'[^\W\d_]', surface) is not None))
