"""E-06：golden 全集的虚构值变体，本地脱敏与出口复检泄露率。"""
import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
from app.redaction.engine import NodeIn, Term, compute, outbound_text
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


def chinese_number(number):
    """测试数据用中文位权数字，不生成逐位拼写的假金额。"""
    digits = '零壹贰叁肆伍陆柒捌玖'
    def integer(n):
        if n < 10:
            return digits[n]
        for base, unit in ((100000000, '亿'), (10000, '万'), (1000, '仟'), (100, '佰'), (10, '拾')):
            if n >= base:
                head, rest = divmod(n, base)
                return integer(head) + unit + ('零' if rest and rest < base // 10 else '') + (integer(rest) if rest else '')
    whole = int(number)
    fraction = int((number - whole) * 100)
    text = integer(whole)
    if fraction:
        text += '元' + (digits[fraction // 10] + '角' if fraction // 10 else '零') + (digits[fraction % 10] + '分' if fraction % 10 else '')
    return text


def variants(value, category):
    _, t2s = _tables()
    s2t = {}
    for traditional, simplified in t2s.items():
        s2t.setdefault(simplified, traditional)
    forms = dict(original=value,
                 traditional=''.join(s2t.get(c, c) for c in value),
                 fullwidth=''.join(chr(ord(c) + 0xFEE0) if '!' <= c <= '~' else '\u3000' if c == ' ' else c for c in value),
                 zero_width='\u200b'.join(value), bidi='\u202e'.join(value),
                 reference_spaces=' '.join(value), reference_hyphens='-'.join(value),
                 cyrillic=value.translate(str.maketrans('ABCEHKMOPTXaceopxy', 'АВСЕНКМОРТХасеорху')))
    if category in ('PERSON', 'ORG', 'SWIFT') and re.search('[A-Za-z]', value):
        forms['lowercase'] = value.lower()
    if category in ('ACCOUNT', 'PHONE', 'IDNO', 'REFNO', 'POSTCODE'):
        compact = re.sub(r'[\s.\-]', '', value)
        if category == 'PHONE' and re.fullmatch(r'1\d{10}', compact):
            groups = [compact[:3], compact[3:7], compact[7:]]
        elif category == 'PHONE' and re.fullmatch(r'0\d{9,11}', compact):
            cut = 3 if len(compact) == 11 else 4
            groups = [compact[:cut], compact[cut:]]
        else:
            groups = [compact[i:i + 4] for i in range(0, len(compact), 4)]
        forms.update(spaces=' '.join(groups), hyphens='-'.join(groups),
                     chinese_digits=value.translate(str.maketrans('0123456789', '零一二三四五六七八九')))
    if category == 'PERSON' and re.fullmatch(r'[一-鿿]{2,3}', value):
        forms.update(name_spaces=' '.join(value), name_fullspaces='\u3000'.join(value))
    if category in ('AMOUNT', 'PRICE'):
        forms['spaces'] = re.sub(r'(?<=\d),(?=\d)', ' ', value)
        try:
            number = Decimal(value.replace(',', ''))
        except InvalidOperation:
            from app.redaction.detect import chinese_upper_value
            number = chinese_upper_value(value)
        if number is not None and 0 <= number < Decimal('1000000000000'):
            upper = chinese_number(number)
            # 金额原有后缀由原段落保留；原值自带万/元等单位时只恢复其本有元单位。
            if '元' in value and '元' not in upper:
                upper += '元整' if '整' in value else '元'
            forms['chinese_upper'] = upper
            forms['chinese_lower'] = upper.translate(str.maketrans('壹贰叁肆伍陆柒捌玖拾佰仟', '一二三四五六七八九十百千'))
    # 每个值只改一个 OCR 形近位置；名称中的普通字母不当成数字片段。
    if category in ('ACCOUNT', 'PHONE', 'IDNO', 'REFNO', 'POSTCODE', 'AMOUNT', 'PRICE', 'SWIFT'):
        match = re.search('[0158]', value)
        if match:
            i = match.start()
            forms['ocr'] = value[:i] + {'0': 'O', '1': 'l', '5': 'S', '8': 'B'}[value[i]] + value[i + 1:]
    # 不适用的变体不伪计为“已验证”，每一类只保留确有变化的写法。
    return {name: surface for name, surface in forms.items() if name == 'original' or surface != value}


def load_seeds():
    seeds = {}
    for path in sorted(GOLDEN.glob('*.yaml')):
        ann = yaml.safe_load(path.read_text(encoding='utf-8'))
        lines = path.with_suffix('.txt').read_text(encoding='utf-8').splitlines()
        nodes = [NodeIn(*line.split(' ', 1)) if ann.get('loc_prefixed') else NodeIn(f'F1-P{i}', line)
                 for i, line in enumerate(lines, 1) if line.strip()]
        for item in ann.get('must_replace', []):
            value, category = item['text'], item['category']
            target = next((i for i, node in enumerate(nodes) if value in node.text), None)
            seeds.setdefault((category, value), dict(category=category, value=value, nodes=nodes,
                             target=target, stance=ann['stance'], terms=[Term(**t) for t in ann.get('terms', [])], source=path.stem))
    return list(seeds.values())


def contains(text, values, codes):
    for code in sorted(set(codes), key=len, reverse=True):
        text = text.replace(code, '')
    text = re.sub(r'\[F\d+-[^\]]+\]', '', text)
    normalized = normalize_value(text)
    compact = lambda s: re.sub(r'[\s\-‐‑–—,，.。:：()（）]', '', s)
    return any((needle := normalize_value(value)) and
               (needle in normalized or compact(needle) in compact(normalized)) for value in values)


def evaluate(probe_dir=None, seeds=None):
    seeds = load_seeds() if seeds is None else seeds
    counts, failures, memo, caches = {}, [], {}, {}
    done = 0
    if probe_dir:
        seeds = []
        for path in sorted(probe_dir.glob('*.yaml')):
            p = yaml.safe_load(path.read_text(encoding='utf-8'))
            seeds.append(dict(category=p['category'], value=p['canonical'],
                              nodes=[NodeIn(f'F1-P{i}', t) for i, t in enumerate(p['paragraphs'], 1)],
                              target=0, stance='sales', terms=[], source=path.stem,
                              forms={p['variant']: p['surface']}))
    reference = {'cyrillic', 'lowercase', 'reference_spaces', 'reference_hyphens'}
    names = {'ORG', 'OUR', 'PERSON', 'ADDR'}
    for seed in seeds:
        category, value = seed['category'], seed['value']
        forms = seed.get('forms') or variants(value, category)
        original = seed['nodes']
        target = seed['target']
        if target is None:
            label = LABELS.get(category, '文件信息')
            original = [NodeIn('F1-P1', f'{label}：{value}。')]
            target = 0
        max_file = max(int(re.match(r'F(\d+)', n.loc)[1]) for n in original)
        for variant, surface in forms.items():
            tier = 'reference' if variant in reference else 'core'
            for mode in ('pure', 'mixed'):
                nodes, targets, needles = [], [], []
                for copy in range(3):
                    transformed = mode == 'pure' or copy > 0
                    for index, n in enumerate(original):
                        loc = re.sub(r'^F(\d+)', lambda m: 'F' + str(int(m[1]) + copy * max_file), n.loc)
                        text = n.text.replace(value, surface) if transformed else n.text
                        nodes.append(NodeIn(loc, strip_invisible(text), n.clause_no))
                        if index == target:
                            targets.append(loc)
                            needles.append((value, surface) if transformed else (value,))
                key = (seed['source'], tuple(n.text for n in nodes))
                if key not in memo:
                    result = compute(nodes, seed['stance'], main_prefix='F1-', terms=seed['terms'],
                                     ner_cache=caches.get(seed['source']))
                    caches[seed['source']] = result.ner_cache
                    parties = [PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in result.parties]
                    outbound = outbound_text(nodes, parties, result.redacted)
                    codes = [item.placeholder for item in result.items]
                    reasons = recheck(outbound, real_values=result.mapping.values(),
                                      terms=[t.text for t in seed['terms']], placeholders=codes)
                    memo[key] = result, codes, reasons
                result, codes, reasons = memo[key]
                row = counts.setdefault((tier, mode, category, variant), Counter())
                leaked = False
                for loc, values in zip(targets, needles):
                    remains = contains(result.redacted[loc], values, codes)
                    outcome = 'blocked' if remains and reasons else 'leaked' if remains else 'replaced'
                    row[outcome] += 1
                    leaked |= outcome == 'leaked'
                if leaked:
                    failures.append(dict(category=category, variant=variant, tier=tier, mode=mode,
                                         value=value, surface=surface, source=seed['source'],
                                         paragraphs=[n.text for n in nodes], targets=targets))
                done += 1
        if done % 50 < 20:
            print(f'groups {done}', flush=True)
    rows = [dict(tier=t, mode=m, category=c, variant=v,
                 **{k: n[k] for k in ('replaced', 'blocked', 'leaked')},
                 total=sum(n.values()), leak_rate=n['leaked'] / sum(n.values()))
            for (t, m, c, v), n in sorted(counts.items())]
    required = lambda r: r['tier'] == 'core' and r['category'] in A and (r['category'] not in names or r['mode'] == 'mixed')
    return dict(seed_count=len(seeds), groups=done, rows=rows,
                a_leaked=sum(r['leaked'] for r in rows if required(r)),
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
        report.write('# 本地泄露评测\n\n| 级别 | 模式 | 类别 | 变体 | 替换 | 拦截 | 泄露 | 泄露率 |\n|-|-|-|-|-|-|-|-|\n')
        for r in data['rows']:
            report.write(f"| {r['tier']} | {r['mode']} | {r['category']} | {r['variant']} | {r['replaced']} | {r['blocked']} | {r['leaked']} | {r['leak_rate']:.6f} |\n")
        report.write(f"\nA 类泄露数：{data['a_leaked']}\n")
        if args.failures:
            with args.failures.open('x', encoding='utf-8') as stream:
                json.dump(failures, stream, ensure_ascii=False, indent=2)
    print(f"本地评测完成：{data['groups']} 组，A 类泄露 {data['a_leaked']} 处", flush=True)
    return int(data['a_leaked'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
