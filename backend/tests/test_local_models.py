"""统一检查命令：虚构清单、轻量检查替身、重型组只读心跳。"""
import json
import os
import time

from app import cli, local_models


def test_model_check_offline_states(tmp_path, monkeypatch, capsys):
    catalog = tmp_path / 'models.yaml'
    manifest = tmp_path / 'model-manifests/asr.sha256'
    manifest.parent.mkdir()
    manifest.write_text('a' * 64 + '  fake.onnx\n')
    catalog.write_text(json.dumps([
        dict(id='ner', purpose='脱敏', source='fictional@1'),
        dict(id='asr', purpose='转写', source='fictional@2', group='asr', files=['fake.onnx']),
    ]))
    monkeypatch.setenv('LOCAL_MODELS_FILE', str(catalog))
    monkeypatch.setenv('CONVERT_SPOOL_DIR', str(tmp_path))
    monkeypatch.setenv('COMPOSE_PROFILES', '')
    calls = []
    monkeypatch.setattr(local_models, '_light_check', lambda mid, smoke: calls.append(mid))
    assert cli.main(['model-check']) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]['verification'] == 'sha256_verified'
    assert rows[1]['status'] == 'disabled'
    assert rows[1]['verification'] == 'manifest_only'
    assert calls == ['ner']
    monkeypatch.setenv('COMPOSE_PROFILES', 'asr')
    assert cli.main(['model-check', 'asr']) == 1
    assert json.loads(capsys.readouterr().out)[0]['status'] == 'unavailable'
    alive = tmp_path / 'transcriber.alive'
    alive.touch()
    assert cli.main(['model-check', 'asr']) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row['status'] == 'enabled' and row['verification'] == 'container_verified'
    assert set(row) == {'id', 'purpose', 'version', 'status', 'verification'}
    os.utime(alive, (time.time() - 60,) * 2)
    assert cli.main(['model-check', 'asr']) == 1
    capsys.readouterr()
    assert calls == ['ner']  # 重型检查从未进入轻量加载路径。
