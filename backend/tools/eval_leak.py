"""E-06：golden 全集的虚构值变体，本地脱敏与出口复检泄露率。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
from app.redaction.engine import NodeIn, compute, outbound_text
from app.redaction.outbound import PartyInfo
from app.redaction.normalize import _tables, normalize_value
from app.redaction.recheck import recheck
from app.parsing.model import strip_invisible

GOLDEN = Path(__file__).resolve().parents[1] / 'tests/fixtures/redaction_golden'
A = {'ORG', 'OUR', 'PERSON', 'PHONE', 'ACCOUNT', 'IDNO', 'AMOUNT', 'PRICE', 'ADDR'}
LABELS = {'ORG': '买方', 'OUR': '卖方', 'PERSON': '联系人', 'PHONE': '电话', 'EMAIL': '邮箱',
          'ACCOUNT': '收款账号', 'IDNO': '证件号码', 'AMOUNT': '合同总价', 'PRICE': '单价',
          'ADDR': '地址', 'REFNO': '合同编号', 'PROJECT': '项目名称',
          'IM': '微信号', 'POSTCODE': '邮编', 'SWIFT': 'SWIFT', 'URL': '网址'}


def variants(value):
    _, t2s = _tables()
    s2t = {}
    for traditional, simplified in t2s.items():
        s2t.setdefault(simplified, traditional)
    forms = dict(original=value,
                 traditional=''.join(s2t.get(c, c) for c in value),
                 fullwidth=''.join(chr(ord(c) + 0xFEE0) if '!' <= c <= '~' else '\u3000' if c == ' ' else c for c in value),
                 zero_width='\u200b'.join(value), bidi='\u202e'.join(value),
                 chinese_digits=value.translate(str.maketrans('0123456789', '零一二三四五六七八九')),
                 lowercase=value.lower(),
                 spaces=' '.join(value), hyphens='-'.join(value),
                 ocr=value.translate(str.maketrans('01l5S8B', 'OllSSBB')),
                 cyrillic=value.translate(str.maketrans('ABCEHKMOPTXaceopxy', 'АВСЕНКМОРТХасеорху')))
    # 不适用的变体不伪计为“已验证”，每一类只保留确有变化的写法。
    return {name: surface for name, surface in forms.items() if name == 'original' or surface != value}


def contains(text, values, codes):
    for code in sorted(set(codes), key=len, reverse=True):
        text = text.replace(code, '')
    text = re.sub(r'\[F\d+-[^\]]+\]', '', text)
    normalized = normalize_value(text)
    compact = lambda s: re.sub(r'[\s\-‐‑–—,，.。:：()（）]', '', s)
    return any((needle := normalize_value(value)) and
               (needle in normalized or compact(needle) in compact(normalized)) for value in values)


def evaluate(probe_dir=None):
    seeds = sorted({(item['category'], item['text']) for path in GOLDEN.glob('*.yaml')
                    for item in yaml.safe_load(path.read_text(encoding='utf-8')).get('must_replace', [])})
    counts, failures = {}, []
    done = 0
    memo = {}
    probes = [yaml.safe_load(p.read_text(encoding='utf-8')) for p in sorted(probe_dir.glob('*.yaml'))] if probe_dir else None
    cases = [(p['category'], p['canonical'], {p['variant']: p['surface']}, p['paragraphs']) for p in probes] if probes is not None else \
            [(category, value, variants(value), None) for category, value in seeds]
    for category, value, forms, fixed_paragraphs in cases:
        label = LABELS.get(category, '文件信息')
        for name, surface in forms.items():
            paragraphs = fixed_paragraphs or [f'{label}：{surface}。', f'本文件记载的{label}为{surface}，请依约办理。',
                          f'附件信息：{label} {surface}；本段内容全部虚构。']
            # ParsedNode.__post_init__ 的同一清理步骤，不能把产品不会入库的字符算作生产漏替换。
            nodes = [NodeIn(f'F1-P{i}', strip_invisible(text)) for i, text in enumerate(paragraphs, 1)]
            key = tuple(n.text for n in nodes)
            if key not in memo:
                result = compute(nodes, 'sales', main_prefix='F1-')
                parties = [PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in result.parties]
                outbound = outbound_text(nodes, parties, result.redacted)
                codes = [item.placeholder for item in result.items]
                # 与产品一致：仅使用实际识别产生的映射，不把金标预塞进 real_values。
                reasons = recheck(outbound, real_values=result.mapping.values(), terms=[], placeholders=codes)
                memo[key] = result, codes, reasons
            result, codes, reasons = memo[key]
            row = counts.setdefault((category, name), Counter())
            leaked = False
            for node in nodes:
                remains = contains(result.redacted[node.loc], (value, surface), codes)
                outcome = 'blocked' if remains and reasons else 'leaked' if remains else 'replaced'
                row[outcome] += 1
                leaked |= outcome == 'leaked'
            if leaked:
                failures.append(dict(category=category, variant=name, value=value, surface=surface,
                                     paragraphs=paragraphs))
            done += 1
        if done % 25 < 12:
            print(f'本地泄露评测：已完成 {done} 组', flush=True)
    rows = [dict(category=c, variant=v, **{k: n[k] for k in ('replaced', 'blocked', 'leaked')},
                 total=sum(n.values()), leak_rate=n['leaked'] / sum(n.values())) for (c, v), n in sorted(counts.items())]
    return dict(seed_count=len(probes) if probes is not None else len(seeds), groups=done, rows=rows,
                a_leaked=sum(r['leaked'] for r in rows if r['category'] in A),
                total_leaked=sum(r['leaked'] for r in rows)), failures


def main():
    parser = argparse.ArgumentParser(description='只用虚构 golden 的本地变体泄露评测；不调用模型')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--failures', type=Path, help='仓库外的虚构失败诊断，报告不含这些文字')
    parser.add_argument('--probe-dir', type=Path, help='单独复现带标注的变体 golden 子集')
    args = parser.parse_args()
    with args.output.open('x', encoding='utf-8') as report, args.output.with_suffix('.json').open('x', encoding='utf-8') as output:
        if args.probe_dir and not list(args.probe_dir.glob('*.yaml')):
            raise ValueError('变体标注目录为空')
        data, failures = evaluate(args.probe_dir)
        json.dump(data, output, ensure_ascii=False, indent=2)
        report.write('# 本地泄露评测\n\n| 类别 | 变体 | 替换 | 拦截 | 泄露 | 泄露率 |\n|-|-|-|-|-|-|\n')
        for r in data['rows']:
            report.write(f"| {r['category']} | {r['variant']} | {r['replaced']} | {r['blocked']} | {r['leaked']} | {r['leak_rate']:.6f} |\n")
        report.write(f"\nA 类泄露数：{data['a_leaked']}\n")
        if args.failures:
            with args.failures.open('x', encoding='utf-8') as stream:
                json.dump(failures, stream, ensure_ascii=False, indent=2)
    print(f"本地评测完成：{data['groups']} 组，A 类泄露 {data['a_leaked']} 处", flush=True)
    return int(data['a_leaked'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
