"""从已下载的固定上游数据生成中文词表；仅使用标准库，不在运行时联网。"""
import argparse
import hashlib
from pathlib import Path
import re

THU_COMMIT = 'a30ce79d895d01ab5132a5c74c29703ff7efb4cc'
# golden 若出现回退，在此剔除并同步 README 的剔除表。
EXCLUDED_WORDS = set()
EXCLUDED_LEGAL = set()
# B-02：单字只收虚词与构词成分，不靠逐字拆开解释姓名。
PLAIN_SINGLE = frozenset('的了是在不和与及或等其此该本我你他她它们对于由从被把将这那应可按使到全各方人者员组站以为经向给让所之并且而也都仍仅再已未无有一套级虚')


def build_plain(source: Path, output: Path) -> None:
    rows = set()
    for line in (source / 'dict.txt').read_text(encoding='utf-8').splitlines():
        word, freq, pos = line.split()
        if (re.fullmatch(r'[一-鿿]{1,8}', word) and not pos.startswith(('nr', 'ns', 'nt', 'nz'))
                and (word in PLAIN_SINGLE if len(word) == 1 else int(freq) >= 5)):
            rows.add((word, pos))
    data = ''.join(f'{word}\t{pos}\n' for word, pos in sorted(rows)).encode('utf-8')
    (output / 'zh_plain_words.txt').write_bytes(data)
    license_text = (source / 'jieba-LICENSE').read_text(encoding='utf-8')
    (output / 'zh_plain_words.README.md').write_text(f'''# 普通词库（V321-B-02）

来源：https://github.com/fxsjy/jieba/tree/v0.42.1 ，只取 jieba/dict.txt；沿用 V31Q-15 来源，用户 2026-11-13 同意下载固定版本词典及许可。
输入 SHA-256：`{digest((source / 'dict.txt').read_bytes())}`。
口径：1～8 个汉字；排除 nr / ns / nt / nz 及其子类；多字词频 ≥ 5（首次 50，按基准只校准一次）。普通简称 j 仍收，如“研发”；专名词性仍不收。
单字只收白名单与上游词典的交集：{''.join(sorted(PLAIN_SINGLE))}。保留词性，每行“词\\t词性”，UTF-8、LF、Unicode 排序。
结果：{len(rows)} 行；SHA-256 `{digest(data)}`。
生成：`python scripts/build_zh_words.py --plain-only --source <词典及许可目录> --out resources`（Docker 内）。
旧 zh_words.txt 及其用途不变。本词库只解释不确定候选及标签人名取值，不改变出口复检，也不撤销已有强识别。

## 上游许可全文

```text
{license_text.rstrip()}
```
''', encoding='utf-8', newline='\n')


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
    suffixes = tuple((output / 'industry_words.txt').read_text(encoding='utf-8').splitlines()) + ('公司', '集团', '股份', '控股', '银行', '证券', '保险', '药业', '化纤')
    for name in ('THUOCL_law.txt',):
        for line in (source / name).read_text(encoding='utf-8').splitlines():
            word = line.split()[0]
            if re.fullmatch(r'[一-鿿]{2,8}', word) and not word.endswith(suffixes):
                legal.add(word)
    configs = (
        ('zh_words', words, EXCLUDED_WORDS, ['dict.txt'], 'jieba-LICENSE',
         'https://github.com/fxsjy/jieba/tree/v0.42.1',
         'jieba v0.42.1 主词典（不取 extra_dict）；2～4 个汉字、词频 ≥ 1000，'
         '词性排除 nr / nrfg / nrt / ns / nt / nz / j。'),
        ('zh_legal_finance', legal, EXCLUDED_LEGAL, ['THUOCL_law.txt'],
         'THUOCL-LICENSE', f'https://github.com/thunlp/THUOCL/tree/{THU_COMMIT}',
         '只取法律词表第一列，保留 2～8 个汉字；不取财经与地名词库。剔除末尾为 industry_words.txt 中行业词或公司、集团、股份、控股、银行、证券、保险、药业、化纤的词。'),
    )
    for name, terms, excluded, inputs, license_name, url, rule in configs:
        data = ''.join(t + '\n' for t in sorted(terms - excluded)).encode('utf-8')
        (output / f'{name}.txt').write_bytes(data)
        input_rows = '\n'.join(
            f'- `{f}`：SHA-256 `{digest((source / f).read_bytes())}`；'
            f'{len((source / f).read_text(encoding="utf-8").splitlines())} 行。'
            for f in inputs)
        excluded_text = '、'.join(sorted(excluded)) or '无（已有 golden 未出现回退）。'
        if name == 'zh_legal_finance':
            excluded_text = ('不取财经词表；法律词中末尾等于 industry_words.txt 的行业词，或公司、集团、股份、控股、银行、证券、保险、药业、化纤的词均剔除，不逐词列举。'
                             '额外 golden 回退剔除：' + excluded_text)
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
    parser.add_argument('--plain-only', action='store_true', help='只生成 B-02 普通词库，保留旧词库')
    args = parser.parse_args()
    if args.plain_only:
        build_plain(args.source, args.out)
    else:
        build(args.source, args.out)
