"""T-44 8、9：文件校验、加载与推理契约；不调用真实模型。"""
import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

from app.embedding import engine


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'MODEL_DIR', tmp_path)
    monkeypatch.setattr(engine, '_model', None)
    monkeypatch.setattr(engine, '_threads', 4)
    for file in engine.FILES:
        (tmp_path/file).write_bytes(b'fictional-model')
    (tmp_path/'MANIFEST.sha256').write_text(''.join(
        hashlib.sha256((tmp_path/f).read_bytes()).hexdigest()+'  '+f+'\n' for f in engine.FILES))
    return tmp_path


@pytest.mark.parametrize('load', [False, True])
@pytest.mark.parametrize('damage', ['missing', 'changed', 'manifest'])
def test_t44_8_check_rejects(model_dir, load, damage):
    if damage == 'missing':
        (model_dir/'model.onnx').unlink()
    else:
        (model_dir/('model.onnx' if damage == 'changed' else 'MANIFEST.sha256')).write_text('私密内容')
    with pytest.raises(RuntimeError, match='向量模型文件缺失或 SHA-256 校验失败') as exc:
        engine.check(load)
    assert '私密内容' not in str(exc.value)


def test_loading_error_is_fixed(model_dir, monkeypatch):
    def fail():
        raise ValueError('私密内容')
    monkeypatch.setattr(engine, '_Encoder', fail)
    engine.check()
    assert engine._model is None
    with pytest.raises(RuntimeError, match='向量模型加载失败'):
        engine.check(True)
    assert engine._threads == 8


def test_t44_9_determinism_unit_norm_and_no_prefix(model_dir, monkeypatch, caplog):
    seen = []
    def encode(text):
        seen.append(text)
        return np.arange(1, 1025, dtype=np.float32)[None, :]
    monkeypatch.setattr(engine, '_model', SimpleNamespace(encode=encode))
    a = engine.embed(['虚构条款'], 'passage')
    b = engine.embed(['虚构条款'], 'query')
    np.testing.assert_array_equal(a, b)
    assert a.shape == (1, engine.dim()) and a.dtype == np.float32
    assert np.linalg.norm(a) == pytest.approx(1)
    assert seen == ['虚构条款', '虚构条款']
    assert engine.embed([], 'query').shape == (0, 1024)
    assert '虚构条款' not in caplog.text


def test_encoder_options_and_cls(model_dir, monkeypatch):
    calls = {}
    class Tokenizer:
        def enable_truncation(self, **kw): calls['truncate'] = kw
        def enable_padding(self, **kw): calls['padding'] = kw
        def token_to_id(self, token): return 1
        def encode(self, text): return SimpleNamespace(ids=[0, 2], attention_mask=[1, 1])
    def session(path, sess_options, providers):
        calls.update(options=sess_options, providers=providers)
        def run(outputs, feed):
            calls['feed'] = feed
            return [np.ones((1, 2, 1024), dtype=np.float32)]
        return SimpleNamespace(run=run)
    monkeypatch.setattr(engine, 'Tokenizer', SimpleNamespace(from_file=lambda _: Tokenizer()))
    monkeypatch.setattr(engine.ort, 'InferenceSession', session)
    engine.check(True)
    assert engine.embed(['示例'], 'passage').shape == (1, 1024)
    assert calls['options'].intra_op_num_threads == 8
    assert not calls['options'].enable_cpu_mem_arena and not calls['options'].enable_mem_pattern
    assert calls['truncate'] == {'max_length': 512}
    assert calls['providers'] == ['CPUExecutionProvider']
    assert set(calls['feed']) == {'input_ids', 'attention_mask'}


@pytest.mark.parametrize('startup', ['app', 'worker'])
def test_startup_rejects_corrupt_model(model_dir, monkeypatch, startup):
    import asyncio
    from app import main, worker
    from app.redaction import ner
    monkeypatch.setattr(ner, 'check', lambda **kw: None)
    monkeypatch.setattr(main, 'check_master_key', lambda: None)
    monkeypatch.setattr(worker, 'check_master_key', lambda: None)
    (model_dir/'model.onnx').write_text('损坏模型')
    async def enter():
        async with main.lifespan(None):
            pytest.fail('不应进入启动成功状态')
    with pytest.raises(RuntimeError, match='向量模型文件缺失或 SHA-256 校验失败'):
        asyncio.run(enter()) if startup == 'app' else worker.main()
