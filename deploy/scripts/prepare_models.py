"""构建机标准库工具：以仓库清单校验并复制离线模型，输出镜像与冒烟命令。"""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


def main():
    repo, source = map(Path, sys.argv[1:3])
    profiles = set(os.environ.get('COMPOSE_PROFILES', 'asr').split(','))
    # models.yaml 使用 YAML 的 JSON 子集，构建机无需额外安装解析库。
    models = json.loads((repo / 'deploy/models.yaml').read_text(encoding='utf-8'))
    copied = set()
    for model in models:
        if model.get('group') and model['group'] not in profiles:
            continue
        if not (repo / 'deploy/licenses/models' / model['id'] / 'LICENSE').is_file():
            raise ValueError('缺少模型许可证正文')
        if model['dir'] and model['dir'] not in copied:
            destination = repo / model['dir']
            origin = source / model.get('group', model['id'])
            entries = [line.split() for line in (destination / 'MANIFEST.sha256').read_text(encoding='ascii').splitlines()]
            for digest, name in entries:
                file = origin / name
                with file.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        raise ValueError('来源模型 SHA-256 校验失败')
            for digest, name in entries:
                target = destination / name
                if (origin / name).resolve() != target.resolve():
                    shutil.copyfile(origin / name, target)
                with target.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        raise ValueError('复制后模型 SHA-256 校验失败')
            copied.add(model['dir'])
        if model['id'] == 'mt':
            archive = source / 'mt/llama.cpp.tar.gz'
            with archive.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != '1688a167a8edd9499cdd0c19b8bd044f0fa184b466981977bbf2222d884953a4':
                    raise ValueError('llama.cpp 固定源码校验失败')
            vendor = repo / 'translator/vendor'
            vendor.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive, vendor / 'llama.cpp.tar.gz')
        print(model['image'] + '\t' + model['smoke'])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('离线模型准备失败：' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
