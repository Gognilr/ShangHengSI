"""从已批准的固定 Unihan 原件离线生成会议同音纠正用字典。"""
import argparse
import hashlib
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

from gen_hanzi_pinyin import SOURCE_SHA256


def generate(source, output):
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Unihan 17.0.0 校验失败')
    readings = defaultdict(set)
    with zipfile.ZipFile(source) as archive:
        for line in archive.read('Unihan_Readings.txt').decode('utf-8').splitlines():
            if not line.startswith('U+'):
                continue
            point, field, value = line.split('\t', 2)
            if field not in ('kMandarin', 'kHanyuPinyin', 'kXHC1983'):
                continue
            values = value.split() if field == 'kMandarin' else [v for block in value.split()
                       for v in block.split(':', 1)[1].split(',')]
            for sound in values:
                sound = unicodedata.normalize('NFD', sound.lower()).replace('u\u0308', 'v')
                sound = ''.join(c for c in sound if unicodedata.category(c) != 'Mn')
                sound = re.sub('[1-5]', '', sound)
                if re.fullmatch('[a-z]+', sound):
                    readings[chr(int(point[2:], 16))].add(sound)
    output.write_text(json.dumps({k:sorted(v) for k,v in sorted(readings.items())},
                                ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(len(readings), hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    generate(args.source, args.output)
