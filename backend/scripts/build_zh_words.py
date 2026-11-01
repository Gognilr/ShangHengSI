"""从已下载的固定上游数据生成中文词表；仅使用标准库，不在运行时联网。"""
import argparse
import hashlib
from pathlib import Path
import re

THU_COMMIT = 'a30ce79d895d01ab5132a5c74c29703ff7efb4cc'
# golden 若出现回退，在此剔除并同步 README 的剔除表。
EXCLUDED_WORDS = set()
EXCLUDED_LEGAL = set()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(source: Path, output: Path) -> None:
    words = set()
    for line in (source / 'dict.txt').read_text(encoding='utf-8').splitlines():
        word, freq, pos = line.split()
        if re.fullmatch(r'[一-鿿]{2,4}', word) and int(freq) >= 1000 and pos not in {
            'nr', 'nrfg', 'nrt', 'ns', 'nt', 'nz', 'j',
        }:
            words.add(word)
    legal = set()
    for name in ('THUOCL_law.txt', 'THUOCL_caijing.txt'):
        for line in (source / name).read_text(encoding='utf-8').splitlines():
            word = line.split()[0]
            if re.fullmatch(r'[一-鿿]{2,8}', word):
                legal.add(word)
    configs = (
        ('zh_words', words, EXCLUDED_WORDS, ['dict.txt'], 'jieba-LICENSE',
         'https://github.com/fxsjy/jieba/tree/v0.42.1',
         'jieba v0.42.1 主词典（不取 extra_dict）；2～4 个汉字、词频 ≥ 1000，'
         '词性排除 nr / nrfg / nrt / ns / nt / nz / j。'),
        ('zh_legal_finance', legal, EXCLUDED_LEGAL, ['THUOCL_law.txt', 'THUOCL_caijing.txt'],
         'THUOCL-LICENSE', f'https://github.com/thunlp/THUOCL/tree/{THU_COMMIT}',
         '法律与财经词表只取第一列，保留 2～8 个汉字；不取地名词库。'),
    )
    for name, terms, excluded, inputs, license_name, url, rule in configs:
        data = ''.join(t + '\n' for t in sorted(terms - excluded)).encode('utf-8')
        (output / f'{name}.txt').write_bytes(data)
        input_rows = '\n'.join(
            f'- `{f}`：SHA-256 `{digest((source / f).read_bytes())}`；'
            f'{len((source / f).read_text(encoding="utf-8").splitlines())} 行。'
            for f in inputs)
        excluded_text = '、'.join(sorted(excluded)) or '无（已有 golden 未出现回退）。'
        license_text = (source / license_name).read_text(encoding='utf-8')
        readme = f'''# 中文词库：{name}（V31-S-01）

来源：{url} （固定标签 / 提交）。下载与使用由 V31Q-15 授权。
原文件位于该版本的 {'jieba/' if name == 'zh_words' else 'data/'} 目录。

{input_rows}

生成口径：{rule}合并去重，按 Unicode 排序，UTF-8、LF，每行一个词、末尾换行。
生成：`python backend/scripts/build_zh_words.py --source <原文件与许可目录> --out backend/resources`。
结果：{len(terms - excluded)} 行，{len(data)} 字节；SHA-256 `{digest(data)}`。
只按 V31-S.md 1.2 过滤实体识别与疑似，不撤销规则、词条、签约方等已有替换。

## 剔除表

{excluded_text}

## 上游许可全文

```text
{license_text.rstrip()}
```
'''
        if name == 'zh_legal_finance':
            original = (source / 'THUOCL-README.md').read_text(encoding='utf-8')
            notice = original.split('## 开源协议')[1].split('## 作者')[0].strip()
            readme += f'\n## 上游 README 使用说明（原文）\n\n{notice}\n'
        (output / f'{name}.README.md').write_text(readme, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.out)
