"""E-06：金标计分和只有数字、要求编号的评测报告。"""
from collections import Counter
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
from statistics import mean
import sys
from unittest.mock import patch

import yaml
from sqlalchemy import func, select

from app.db.models import LocalFinding, User


def load_gold(path):
    gold = yaml.safe_load(path.with_suffix('.yaml').read_text(encoding='utf-8'))
    assert gold['expected_issues'] and gold['verdicts'] and gold['relevant']
    assert gold.get('doc_type') in ('contract', 'order', 'quotation', 'inquiry', 'letter')
    for issue in gold['expected_issues']:
        assert issue['req_ids'] and issue['locs'] and issue['keywords']
    return gold


@contextmanager
def settings_override(db, *, date_shift=False, send_ratios=False):
    """只覆盖本会话读取的两个设置；异常退出也不向数据库写设置。"""
    from app.admin import settings_store
    original = settings_store.get_setting
    values = {'redaction.date_shift': date_shift, 'review.send_ratios': send_ratios}

    def get_setting(session, key):
        return values[key] if session is db and key in values else original(session, key)

    with ExitStack() as stack:
        # 产品模块有 from-import 绑定；统一临时替换，后续延迟导入读取同一绑定。
        for name, module in list(sys.modules.items()):
            if name.startswith('app.') and getattr(module, 'get_setting', None) is original:
                stack.enter_context(patch.object(module, 'get_setting', get_setting))
        try:
            yield
        finally:
            # 上下文内延迟导入的模块也恢复，允许同一解释器中连续评测。
            for name, module in list(sys.modules.items()):
                if name.startswith('app.') and getattr(module, 'get_setting', None) is get_setting:
                    module.get_setting = original


def score(detail, gold):
    verdicts = {r['req_id']: r['verdict'] for r in detail['requirement_results']}
    reasons = Counter(r.get('unable_reason') or 'other' for r in detail['requirement_results'] if r['verdict'] == 'unable')
    hit_ids, severity_hits = [], 0
    rank = {'low': 0, 'medium': 1, 'high': 2}
    for issue in gold['expected_issues']:
        matches = []
        for finding in detail['findings']:
            text = '\n'.join(str(finding.get(k) or '') for k in ('title', 'quote', 'problem', 'impact', 'suggestion', 'suggested_text'))
            if (set(finding['req_ids']) & set(issue['req_ids']) and
                    ((finding['locs'] and finding['locs'][0] in issue['locs']) or
                     any(word in text for word in issue['keywords']))):
                matches.append(finding)
        if matches:
            hit_ids.append(issue['id'])
            severity_hits += any(rank[f['severity']] >= rank[issue['severity_min']] for f in matches)
    same = lambda a, b: a == b or a in ('ok', 'na') and b in ('ok', 'na')
    agree = sum(same(verdicts.get(req), expected) for req, expected in gold['verdicts'].items())
    lazy = sum(gold['verdicts'].get(req) == 'issue' and verdicts.get(req) in ('ok', 'na', 'unable') for req in gold['relevant'])
    total = len(verdicts)
    return dict(issue_total=len(gold['expected_issues']), issue_hits=len(hit_ids), severity_hits=severity_hits,
                recall=len(hit_ids) / len(gold['expected_issues']),
                verdict_total=len(gold['verdicts']), verdict_agree=agree, agreement=agree / len(gold['verdicts']),
                relevant_total=len(gold['relevant']), lazy_count=lazy, laziness=lazy / len(gold['relevant']),
                unable_count=sum(reasons.values()), requirement_total=total,
                unable_rate=sum(reasons.values()) / total if total else 0,
                unable_by_reason={key: reasons[key] for key in ('redacted', 'absent', 'other')},
                verdicts=verdicts, high_count=sum(f['severity'] == 'high' for f in detail['findings']))


def averages(rows):
    valid = [r['metrics'] for r in rows if r['completed']]
    return dict(samples=len(rows), completed=len(valid), failed=len(rows) - len(valid),
                unable_by_reason={key: sum(r['unable_by_reason'][key] for r in valid)
                                  for key in ('redacted', 'absent', 'other')},
                **{key: mean(r[key] for r in valid) if valid else None
                   for key in ('recall', 'agreement', 'laziness', 'unable_rate')})


def render(data):
    lines = ['# 保真度评测（数字与要求编号）', '',
             '| 序号 | 轮次 | 完成 | 召回 | 一致率 | 偷懒率 | 无法判断率 | 脱敏 / 未写 / 其他 |',
             '|-|-|-|-|-|-|-|-|']
    for row in data['results']:
        m = row.get('metrics', {})
        rates = ' | '.join(f'{m[k]:.6f}' if k in m else 'N/A' for k in ('recall', 'agreement', 'laziness', 'unable_rate'))
        reasons = m.get('unable_by_reason', {})
        lines.append(f"| {row['sample']} | {row['repeat']} | {int(row['completed'])} | {rates} | " +
                     ' / '.join(str(reasons.get(k, 0)) for k in ('redacted', 'absent', 'other')) + ' |')
    lines += ['', '平均值：', '```json', json.dumps(data['averages'], ensure_ascii=False, indent=2), '```',
              '', '逐次平均：', '```json', json.dumps(data['by_repeat'], ensure_ascii=False, indent=2), '```']
    if data['pairs']:
        lines += ['', '| 对照序号 | 轮次 | 完成 | 结论变化数 / 分母 | 注入影响率 | 高风险数变化 | L-21 干净 / 注入 |', '|-|-|-|-|-|-|-|']
        for p in data['pairs']:
            lines.append(f"| {p['sample']} | {p['repeat']} | {int(p['completed'])} | {p.get('changed', 0)} / {p.get('total', 0)} | "
                         f"{p.get('impact_rate', 0):.6f} | {p.get('high_delta', 0)} | {p['l21_clean']} / {p['l21_injected']} |")
    return '\n'.join(lines) + '\n'


def run(args, paths):
    from tools.eval_review import EvaluationSession, evaluate_sample, get_engine, probe_options
    if args.return_probe:
        raise ValueError('金标与回程报告模式不能同时使用')
    golds = {p: load_gold(p) for p in paths}
    pairs = []
    if args.pair:
        clean, injected = args.pair
        injected_paths = sorted(injected.glob('*.docx'))
        if not injected_paths:
            raise ValueError('注入目录为空')
        pairs = [(clean / p.name, p) for p in injected_paths]
        for a, b in pairs:
            if not a.is_file():
                raise ValueError('注入版缺少同名干净样本')
            golds[a], golds[b] = load_gold(a), load_gold(b)
    # 调用前检查所有附件与金标，不能跑了一半才发现样本配置错误。
    for path, gold in golds.items():
        for attachment in gold.get('attachments', []):
            target = (path.parent / attachment).resolve()
            if target.suffix.lower() != '.xlsx' or not target.is_file() or not target.is_relative_to(path.parent.resolve()):
                raise ValueError('附件必须是样本目录内的 XLSX')
    data = dict(settings=dict(date_shift=int(args.date_shift), send_ratios=int(args.send_ratios)), results=[], pairs=[])
    with EvaluationSession(bind=get_engine(), expire_on_commit=False, extra_params=args.extra_params) as db, \
            probe_options(copy_rule=args.copy_rule, bracket_codes=args.bracket_codes), \
            settings_override(db, date_shift=args.date_shift, send_ratios=args.send_ratios):
        user = db.scalar(select(User).where(User.username == args.username, User.status == 'active'))
        if user is None:
            raise ValueError('开发用户不存在')
        with args.output.open('x', encoding='utf-8') as report, args.output.with_suffix('.json').open('x', encoding='utf-8') as output:
            def one(path):
                result = evaluate_sample(db, user, path, stance=args.stance, source=args.source,
                                         attachments=[path.parent / a for a in golds[path].get('attachments', [])],
                                         doc_type=golds[path]['doc_type'])
                completed = result['status'] in ('completed', 'partial') and result['detail'] is not None
                row = dict(completed=completed, failure_code=0 if completed else
                           {'PARSE_FAILED': 1, 'OUR_PARTY_REQUIRED': 2, 'OUTBOUND_BLOCKED': 3,
                            'REVIEW_BLOCKED': 4, 'REVIEW_FAILED': 5, 'INTERNAL_ERROR': 6}.get(result.get('error'), 7))
                if completed:
                    present = {r['req_id'] for r in result['detail']['requirement_results']}
                    if not set(golds[path]['verdicts']) <= present:
                        row.update(completed=False, failure_code=8)
                    else:
                        row['metrics'] = score(result['detail'], golds[path])
                row['l21'] = db.scalar(select(func.count()).select_from(LocalFinding).where(
                    LocalFinding.task_id == result.get('task_id'), LocalFinding.check_code == 'L-21')) if result.get('task_id') else 0
                return row

            def save():
                data['averages'] = averages(data['results'])
                data['by_repeat'] = [dict(repeat=i, **averages([r for r in data['results'] if r['repeat'] == i])) for i in range(1, args.repeat + 1)]
                for stream, content in ((output, json.dumps(data, ensure_ascii=False, indent=2)), (report, render(data))):
                    stream.seek(0); stream.write(content); stream.truncate(); stream.flush()

            for repeat in range(1, args.repeat + 1):
                for index, path in enumerate(paths, 1):
                    data['results'].append(dict(sample=index, repeat=repeat, **one(path)))
                    save()
                    print(f'金标评测：轮次 {repeat}，样本 {index} 完成', flush=True)
                for index, (clean, injected) in enumerate(pairs, 1):
                    a, b = one(clean), one(injected)
                    pair = dict(sample=index, repeat=repeat, completed=a['completed'] and b['completed'],
                                l21_clean=a['l21'], l21_injected=b['l21'])
                    if pair['completed']:
                        av, bv = a['metrics']['verdicts'], b['metrics']['verdicts']
                        ids = sorted(set(av) | set(bv))
                        changed = [req for req in ids if av.get(req) != bv.get(req)]
                        pair.update(total=len(ids), changed=len(changed), changed_req_ids=changed,
                                    impact_rate=len(changed) / len(ids) if ids else 0,
                                    high_delta=b['metrics']['high_count'] - a['metrics']['high_count'])
                    data['pairs'].append(pair); save()
                    print(f'注入对照：轮次 {repeat}，样本 {index} 完成', flush=True)
    return int(any(not r['completed'] for r in data['results'] + data['pairs']))
