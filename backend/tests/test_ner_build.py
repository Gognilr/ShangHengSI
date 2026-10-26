"""离线发布入口先验证可信清单，失败时不能进入镜像构建。"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = ('model.onnx', 'tokenizer.json', 'tokenizer_config.json',
         'special_tokens_map.json', 'added_tokens.json', 'gliner_config.json')


@pytest.mark.parametrize('state', ['omitted', 'corrupt', 'valid'])
def test_release_requires_trusted_models_before_build(tmp_path, state):
    scripts = tmp_path / 'repo/deploy/scripts'
    scripts.mkdir(parents=True)
    for filename in ('build_release.sh', 'lib.sh'):
        shutil.copy(ROOT / 'deploy/scripts' / filename, scripts / filename)
    destination = tmp_path / 'repo/backend/models/ner'
    destination.mkdir(parents=True)
    source = tmp_path / 'external-models'
    source.mkdir()
    manifest = []
    for filename in FILES:
        content = filename.encode()
        (source / filename).write_bytes(content)
        manifest.append(hashlib.sha256(content).hexdigest() + '  ' + filename)
    (destination / 'MANIFEST.sha256').write_text('\n'.join(manifest) + '\n')
    # 外部自带的伪造清单不得替换仓库的可信清单。
    (source / 'MANIFEST.sha256').write_text('untrusted manifest\n')
    # E-04起发布入口还要求向量模型；提供独立的可信假文件，使原NER三个场景仍检查原来的行为。
    embedding_source = tmp_path / 'external-embedding'
    embedding_source.mkdir()
    embedding_destination = tmp_path / 'repo/backend/models/embedding'
    embedding_destination.mkdir()
    embedding_manifest = []
    for filename in ('model.onnx', 'tokenizer.json'):
        content = ('embedding-' + filename).encode()
        (embedding_source / filename).write_bytes(content)
        embedding_manifest.append(hashlib.sha256(content).hexdigest() + '  ' + filename)
    (embedding_destination / 'MANIFEST.sha256').write_text('\n'.join(embedding_manifest) + '\n')
    if state == 'corrupt':
        (source / 'model.onnx').write_bytes(b'corrupt')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    marker = tmp_path / 'docker-called'
    for name, body in [('git', 'exit 0'), ('docker', 'touch "$MARKER"\nexit 42')]:
        target = bin_dir / name
        target.write_text('#!/bin/sh\n' + body + '\n')
        target.chmod(0o755)
    args = ['bash', str(scripts / 'build_release.sh'), '2.0.0', '--out', str(tmp_path / 'out')]
    args.extend(['--embedding-models', str(embedding_source)])
    if state != 'omitted':
        args.extend(['--ner-models', str(source)])
    result = subprocess.run(args, capture_output=True, text=True,
        env={**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'], 'MARKER': str(marker)})
    if state == 'valid':
        assert result.returncode == 42, result.stderr
        assert marker.exists()
        for filename in FILES:
            assert (destination / filename).read_bytes() == filename.encode()
        assert (destination / 'MANIFEST.sha256').read_text() == '\n'.join(manifest) + '\n'
    else:
        assert result.returncode != 0
        assert not marker.exists()
        assert not (destination / 'model.onnx').exists()
