"""bge-m3 INT8 离线推理；不下载文件，不记录输入和向量。"""
import hashlib
import threading
from pathlib import Path
from typing import Literal

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_DIR = Path(__file__).resolve().parents[2] / 'models/embedding'
FILES = ('model.onnx', 'tokenizer.json')
_lock = threading.RLock()
_model = None
_threads = 4


def dim() -> int:
    return 1024


def model_id() -> str:
    return 'bge-m3-int8@5617a9f'


def check(load: bool = False):
    global _threads
    try:
        lines = (MODEL_DIR / 'MANIFEST.sha256').read_text(encoding='ascii').splitlines()
        manifest = {name: digest for digest, name in (line.split() for line in lines)}
        if len(lines) != len(FILES) or set(manifest) != set(FILES):
            raise ValueError
        for name in FILES:
            digest = hashlib.sha256()
            with (MODEL_DIR / name).open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != manifest[name]:
                raise ValueError
    except (OSError, ValueError):
        raise RuntimeError('向量模型文件缺失或 SHA-256 校验失败') from None
    if load:
        _threads = 8
        _get_model()


class _Encoder:
    def __init__(self):
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = _threads
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        self.session = ort.InferenceSession(str(MODEL_DIR / 'model.onnx'), sess_options=options,
                                            providers=['CPUExecutionProvider'])
        self.tokenizer = Tokenizer.from_file(str(MODEL_DIR / 'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id('<pad>'), pad_token='<pad>')

    def encode(self, text):
        encoded = self.tokenizer.encode(text)
        feed = {'input_ids': np.array([encoded.ids], dtype=np.int64),
                'attention_mask': np.array([encoded.attention_mask], dtype=np.int64)}
        return self.session.run(None, feed)[0][:, 0, :]


def _get_model():
    global _model
    with _lock:
        if _model is None:
            check()
            try:
                _model = _Encoder()
            except Exception:
                raise RuntimeError('向量模型加载失败') from None
        return _model


def embed(texts: list[str], kind: Literal['query', 'passage']) -> np.ndarray:
    if kind not in ('query', 'passage'):
        raise ValueError('不支持的向量用途')
    if not texts:
        return np.empty((0, dim()), dtype=np.float32)
    # 选型确认：不加前缀、每批一段；锁同时保护共享 tokenizer 与并发推理峰值。
    with _lock:
        model = _get_model()
        rows = []
        for text in texts:
            vector = np.asarray(model.encode(text), dtype=np.float32).reshape(1, dim())
            norm = np.linalg.norm(vector)
            if not np.isfinite(vector).all() or not np.isfinite(norm) or norm == 0:
                raise RuntimeError('向量模型输出无效')
            rows.append(vector / norm)
        return np.concatenate(rows).astype(np.float32)
