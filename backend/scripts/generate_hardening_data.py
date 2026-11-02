"""V31-P：仅从用户已批准、固定版本的本地原件生成数据，不访问网络。"""
import argparse
import hashlib
import json
import re
import unicodedata
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
args = parser.parse_args()
source = args.source
dest = Path(__file__).resolve().parents[1] / 'resources'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, text):
    path = dest / name
    path.write_text(text, encoding='utf-8', newline='\n')
    return f'`{name}`：{len(text.splitlines())} 行，SHA-256 `{sha(path)}`'


raw = (source / 'confusables.txt').read_text('utf-8')
version = re.search(r'# Version: (\S+)', raw)[1]
confusables = {}
for line in raw.splitlines():
    parts = line.split('#', 1)[0].split(';')
    if len(parts) < 2:
        continue
    a, b = [''.join(chr(int(c, 16)) for c in p.split()) for p in parts[:2]]
    if (len(a) == len(b) == 1 and b.isascii() and b.isalnum()
            and any(word in unicodedata.name(a, '') for word in ('LATIN', 'GREEK', 'CYRILLIC', 'FULLWIDTH'))):
        confusables[a] = b

with zipfile.ZipFile(source / 'Unihan.zip') as archive:
    variants = archive.read('Unihan_Variants.txt').decode('utf-8')
t2s = {}
for line in variants.splitlines():
    parts = line.split('\t')
    if len(parts) == 3 and parts[1] == 'kSimplifiedVariant':
        targets = parts[2].split()
        if len(targets) == 1:
            t2s[chr(int(parts[0][2:], 16))] = chr(int(targets[0].split('<')[0][2:], 16))

with zipfile.ZipFile(source / 'china_location.zip') as archive:
    locations = archive.read('china_location.txt').decode('utf-8')
parents = {0: ['中国']}
divisions = set()
for line in locations.splitlines():
    level = len(line) - len(line.lstrip('\t'))
    if level > 3:  # 不取村社、区号等其他数据。
        continue
    fields = line.strip().split('\t')
    names = [fields[0]]
    if level < 3 and len(fields) > 2:
        names += [s for s in re.split(r'[/、,，]', fields[2]) if s]
    names = list(dict.fromkeys(names))
    for name in names:
        for parent in parents.get(level, []):
            divisions.add((name, str(level + 1), parent))
    parents[level + 1] = names

outputs = []
for name, data in [('confusables_ascii.json', confusables), ('t2s_chars.json', t2s)]:
    outputs.append(write(name, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n'))
outputs.append(write('cn_admin_divisions.txt', ''.join('\t'.join(row) + '\n' for row in sorted(divisions))))
unicode_license = (source / 'unicode-license.txt').read_text('utf-8')
jio_license = (source / 'jionlp-LICENSE').read_text('utf-8')
commit = (source / 'jionlp-commit.txt').read_text('utf-8-sig').strip()
readme = f'''# V31-P 外部数据

读取日期：2026-11-02。只取数据，标准库脚本 `scripts/generate_hardening_data.py <原件目录>` 可重生成，无运行时下载。

| 原件 | 固定版本、来源 | SHA-256 |
|-|-|-|
| confusables.txt | Unicode UTS #39 {version}（文件头版本）；https://www.unicode.org/Public/security/latest/confusables.txt | `{sha(source / 'confusables.txt')}` |
| Unihan.zip | Unicode 17.0.0；https://www.unicode.org/Public/17.0.0/ucd/Unihan.zip | `{sha(source / 'Unihan.zip')}` |
| china_location.zip | JioNLP 提交 {commit}；https://raw.githubusercontent.com/dongrixinyu/JioNLP/{commit}/jionlp/dictionary/china_location.zip | `{sha(source / 'china_location.zip')}` |

形近字来源的版本目录返回 404，按设计指定 latest 入口读取后固定文件头版本与上述原件哈希，后续重生成使用同一原件，不自动更新。形近字仅收源为单个拉丁、希腊、西里尔、全角字符、目标为单个 ASCII 字母数字的条目（{len(confusables)} 条）；繁简仅唯一目标、单字对单字（{len(t2s)} 条）。两份 Unicode 数据均适用下附 Unicode License V3。

行政区划仅省、市、县、乡镇街道，保留名称及别名、上级名称；每行三列制表符：名称、级别（1～4）、上级名称。同一真实父子关系按各自别名展开，去重；不含村级、电话区号或其他词典。共 {len(divisions)} 行。NOTICE：数据来自 dongrixinyu/JioNLP（作者 dongrixinyu），本项目仅作上述数据裁剪，依 Apache-2.0 使用，未取代码。上游没有独立 NOTICE 文件，本段保留来源与修改说明，许可证全文附后。

产物：

''' + '\n\n'.join(outputs) + '\n\n## Unicode License V3\n\n```text\n' + unicode_license + '\n```\n\n## JioNLP Apache-2.0 全文\n\n```text\n' + jio_license + '\n```\n'
write('hardening_data.README.md', readme)
print(version, len(confusables), len(t2s), len(divisions))
