"""从获准下载的 Unihan 17.0.0 生成 GB2312 汉字读音；只在开发容器离线运行。"""
import argparse
import hashlib
import re
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

SOURCE_SHA256 = 'f7a48b2b545acfaa77b2d607ae28747404ce02baefee16396c5d2d7a8ef34b5e'


def generate(source: Path, license_path: Path, output: Path) -> None:
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Unihan 17.0.0 文件校验失败')
    chars = set()
    for high in range(0xb0, 0xf8):
        for low in range(0xa1, 0xff):
            try:
                chars.add(bytes([high, low]).decode('gb2312'))
            except UnicodeDecodeError:
                pass
    assert len(chars) == 6763
    readings = defaultdict(set)
    with zipfile.ZipFile(source) as archive:
        for line in archive.read('Unihan_Readings.txt').decode('utf-8').splitlines():
            if not line.startswith('U+'):
                continue
            point, field, value = line.split('\t', 2)
            ch = chr(int(point[2:], 16))
            if ch not in chars or field not in ('kMandarin', 'kHanyuPinyin'):
                continue
            values = (value.split() if field == 'kMandarin' else
                      [v for block in value.split() for v in block.split(':', 1)[1].split(',')])
            for sound in values:
                sound = unicodedata.normalize('NFD', sound.lower()).replace('u\u0308', 'v')
                sound = ''.join(c for c in sound if unicodedata.category(c) != 'Mn')
                sound = re.sub('[1-5]', '', sound)
                if re.fullmatch('[a-z]+', sound):
                    readings[ch].add(sound)
    if chars != readings.keys():
        raise ValueError('GB2312 汉字读音不完整')
    header = ['# Unicode Unihan 17.0.0，kMandarin / kHanyuPinyin；GB2312 一级二级 6763 字。',
              '# 来源：https://www.unicode.org/Public/17.0.0/ucd/Unihan.zip',
              '# SHA-256: ' + SOURCE_SHA256,
              '# 生成：backend/scripts/gen_hanzi_pinyin.py；无声调，ü 写作 v，保留全部来源读音。',
              '# Copyright © 1991-2025 Unicode, Inc.', '#']
    header += ['# ' + line for line in license_path.read_text(encoding='utf-8').splitlines()]
    lines = header + [ch + ' ' + ' '.join(sorted(readings[ch])) for ch in sorted(chars)]
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('汉字条数', len(readings), '资源字节', output.stat().st_size)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--license', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    generate(args.source, args.license, args.out)
