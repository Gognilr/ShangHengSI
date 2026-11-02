"""财务表格的分级合计和报表勾稽；只在标签与参与值明确时计算。"""
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

from app.checks.formula import numeric
from app.checks.model import CheckResult
from app.checks.numbers import pct_diff
from app.redaction.tables import table_cell, _col_number

FIN_COLUMNS = ('期末余额', '期初余额', '年初余额', '本期金额', '上期金额', '本年累计', '上年同期', '期末数', '年初数', '行次')


def merged_header(rows, row):
    """仅相邻两行均以文字为主时合并；横向空格沿用上行左侧标签。"""
    labels = {c: n.text.strip() for c, n in rows[row].items()}
    other = rows.get(row + 1, {})
    def header(cells):
        texts = [n.text for n in cells.values() if n.text.strip()]
        return texts and sum(numeric(t) is None for t in texts) * 2 > len(texts)
    if header(rows[row]) and header(other) and all(numeric(n.text) is None for n in other.values()):
        carry = ''
        for col in sorted(set(labels) | set(other)):
            carry = labels.get(col, '') or carry
            lower = other[col].text.strip() if col in other else ''
            labels[col] = ' '.join(dict.fromkeys(x for x in (carry, lower) if x))
        return labels, row + 1
    return labels, row


def _normalize(text):
    text = re.sub(r'\s+', '', text).replace('（', '(').replace('）', ')')
    text = re.sub(r'^[一二三四五六七八九十\d]+[、.．]', '', text)
    return re.sub(r'^(其中|加|减)[:：]', '', text)


@lru_cache(maxsize=1)
def _labels():
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / 'resources/fin_labels.yaml').read_text(encoding='utf-8-sig'))
    return {_normalize(alias): key for key, aliases in raw.items() for alias in [key, *aliases]}


def _tables(nodes):
    grouped = {}
    for n in nodes:
        cell = table_cell(n.loc)
        if cell:
            name, col, row = cell
            grouped.setdefault(name, {}).setdefault(row, {})[_col_number(col)] = n
    return grouped


def check_finance(nodes, *, reports_out=None):
    results, reports = [], []
    for name, rows in _tables(nodes).items():
        candidates = []
        for row in sorted(r for r in rows if r <= 10):
            labels, end = merged_header(rows, row)
            # 普通数字明细不当表头；财务列和月份列、合计列都可使用。
            if any(re.search('|'.join(FIN_COLUMNS[:-1]) + r'|合计|总计|Total|数量|单价|金额|amount|price', t, re.I) for t in labels.values()) and all(numeric(n.text) is None for n in rows[row].values()):
                candidates.append((row, end, labels))
        if not candidates:
            continue
        _, header, columns = candidates[0]
        text_cols = [c for c in sorted(columns) if any(
            c in cells and cells[c].text.strip() and numeric(cells[c].text) is None
            for r, cells in rows.items() if r > header)]
        label_col = next((c for c, title in sorted(columns.items()) if "项目" in title),
                         text_cols[0] if text_cols else min(columns))
        data = {r: cells for r, cells in rows.items() if r > header}
        seq = next(iter(rows[header].values())).file_seq
        def report(code, target, actual, expected, description, severity='high', tolerance=Decimal('.01')):
            if abs(actual - expected) > tolerance:
                results.append(CheckResult(code, severity, [target.loc], seq,
                    f'{target.loc} {description}：显示值 {actual}，计算值 {expected}',
                    f'{target.loc} {description}不一致，差异约 {pct_diff(actual, expected)}'))
        # 明细行与小计独立累积；报表的“资产合计”等不在这里猜层级。
        for col in columns:
            if col == label_col or '行次' in columns[col]:
                continue
            detail, subtotals = [], []
            for row, cells in sorted(data.items()):
                label = cells.get(label_col)
                target = cells.get(col)
                if not label or not target:
                    continue
                value = numeric(target.text)
                if label.text.strip().endswith('小计'):
                    if value is not None and detail and all(v is not None for v in detail):
                        report('L-14', target, value, sum(detail, Decimal(0)), '小计与明细之和')
                    subtotals.append(value); detail = []
                elif re.fullmatch(r'合计|总计|Total', label.text.strip(), re.I):
                    if value is not None and subtotals and all(v is not None for v in subtotals):
                        report('L-14', target, value, sum(subtotals, Decimal(0)), '总计与小计之和')
                    detail = []; subtotals = []
                else:
                    detail.append(value)
        # 行合计仅比较明确相同币种（含表头统一币种）；不混加单价、数量与金额。
        from app.checks.amounts import _CURRENCY_TOKEN
        from app.redaction.detect import _CURRENCY
        for col, title in columns.items():
            if not re.fullmatch(r'合计|小计|总计|Total', title.strip(), re.I):
                continue
            for row, cells in data.items():
                target = cells.get(col)
                left = [n for c, n in sorted(cells.items()) if label_col < c < col and '行次' not in columns.get(c, '')]
                if not target or len(left) < 2:
                    continue
                if any(re.search(r'数量|单价|qty|price', columns.get(c, ''), re.I) for c in cells if label_col < c < col):
                    continue
                def currencies(text):
                    return {_CURRENCY.get(m.group().upper(), _CURRENCY.get(m.group(), m.group())) for m in _CURRENCY_TOKEN.finditer(text)}
                common = set().union(*(currencies(t) for t in columns.values()))
                sets = [currencies(n.text) or common for n in [*left, target]]
                values = [numeric(n.text) for n in left]
                actual = numeric(target.text)
                if actual is not None and all(v is not None for v in values) and all(len(s) == 1 and s == sets[0] for s in sets):
                    report('L-14', target, actual, sum(values, Decimal(0)), '行合计与左侧数值之和')
        labels = {}
        for row, cells in data.items():
            if label_col in cells:
                canonical = _labels().get(_normalize(cells[label_col].text))
                if canonical:
                    # 重复标签不任取一行。
                    labels.setdefault(canonical, []).append(row)
        mapped = {key: rows[rr[0]] for key, rr in labels.items() if len(rr) == 1}
        kind = ('balance' if '资产总计' in mapped and ('负债合计' in mapped or '负债和所有者权益总计' in mapped) else
                'profit' if '营业收入' in mapped and '净利润' in mapped else
                'cash' if '现金及现金等价物净增加额' in mapped else None)
        if not kind:
            continue
        reports.append((kind, name, columns, mapped))
        for col, title in columns.items():
            if col == label_col or '行次' in title:
                continue
            def equation(target, terms):
                cells = [mapped.get(k, {}).get(col) for k in [target, *[k for k, sign in terms]]]
                if not all(cells): return
                values = [numeric(n.text) for n in cells]
                if any(v is None for v in values): return
                actual, *parts = values
                expected = sum((v * sign for v, (_, sign) in zip(parts, terms)), Decimal(0))
                tolerance = Decimal(1) if all(v == v.to_integral_value() for v in [actual, expected]) else Decimal('.01')
                report('L-13', cells[0], actual, expected, f'{title} {target}勾稽', tolerance=tolerance)
            if kind == 'balance':
                equation('资产总计', [('负债和所有者权益总计', 1)])
                equation('负债和所有者权益总计', [('负债合计', 1), ('所有者权益合计', 1)])
                equation('资产总计', [('流动资产合计', 1), ('非流动资产合计', 1)])
            elif kind == 'profit':
                equation('利润总额', [('营业利润', 1), ('营业外收入', 1), ('营业外支出', -1)])
                equation('净利润', [('利润总额', 1), ('所得税费用', -1)])
                first, last = labels.get('营业收入', [0])[0], labels.get('营业利润', [0])[0]
                terms = [('营业收入', 1), ('营业成本', -1), ('税金及附加', -1), ('销售费用', -1), ('管理费用', -1), ('研发费用', -1), ('财务费用', -1), ('其他收益', 1), ('投资收益', 1), ('公允价值变动收益', 1), ('信用减值损失', 1), ('资产减值损失', 1), ('资产处置收益', 1)]
                unknown = [cells[label_col] for r, cells in data.items() if first < r < last and label_col in cells and _normalize(cells[label_col].text) not in _labels()]
                if unknown:
                    text = f'{name} 营业收入与营业利润之间含未识别行，未做营业利润勾稽'
                    if not any(r.ai_summary == text for r in results): results.append(CheckResult('L-INFO', 'low', [n.loc for n in unknown], seq, text, text))
                else:
                    equation('营业利润', terms)
            else:
                equation('现金及现金等价物净增加额', [('经营活动产生的现金流量净额', 1), ('投资活动产生的现金流量净额', 1), ('筹资活动产生的现金流量净额', 1), ('汇率变动对现金及现金等价物的影响', 1)])
                equation('期末现金及现金等价物余额', [('期初现金及现金等价物余额', 1), ('现金及现金等价物净增加额', 1)])
    def period(title):
        if re.search(r'期末|本期|本年累计', title): return 'current'
        if re.search(r'期初|年初|上期|上年同期', title): return 'prior'
        return None
    balances = [r for r in reports if r[0] == 'balance']
    for _, name, cols, mapped in balances:
        current = [c for c, t in cols.items() if period(t) == 'current']
        prior = [c for c, t in cols.items() if period(t) == 'prior']
        if len(current) != 1: continue
        for kind, other, ocols, omap in reports:
            if kind not in ('cash', 'profit'): continue
            oc = [c for c, t in ocols.items() if period(t) == 'current']
            if len(oc) != 1: continue
            akey, bkey = ('货币资金', '期末现金及现金等价物余额') if kind == 'cash' else ('未分配利润', '净利润')
            a, b = mapped.get(akey, {}).get(current[0]), omap.get(bkey, {}).get(oc[0])
            if not a or not b: continue
            av, bv = numeric(a.text), numeric(b.text)
            if kind == 'profit':
                prev = mapped.get(akey, {}).get(prior[0]) if len(prior) == 1 else None
                pv = numeric(prev.text) if prev else None
                if pv is None or av is None: continue
                av -= pv
            if av is None or bv is None: continue
            tolerance = Decimal(1) if all(v == v.to_integral_value() for v in [av, bv]) else Decimal('.01')
            if abs(av - bv) > tolerance:
                reason = '可能有受限资金' if kind == 'cash' else '可能有利润分配'
                results.append(CheckResult('L-13', 'low', [a.loc, b.loc], a.file_seq,
                    f'{a.loc} 与 {b.loc} 跨表差额 {av-bv}，{reason}',
                    f'{name} 与 {other} 跨表勾稽不一致，差异约 {pct_diff(av, bv)}，{reason}'))
    if reports_out is not None:
        reports_out.extend(reports)
    return results
