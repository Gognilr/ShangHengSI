"""财务比率只在管理员开启时拼入审核上下文；只输出固定名称与相对数。"""
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.checks.finance import check_finance
from app.checks.formula import numeric


def calculate(nodes):
    reports = []
    check_finance(nodes, reports_out=reports)
    # 多张同类报表或多组同期间列不任取；不猜缺失科目为零。
    groups = {}
    for kind, name, columns, mapped in reports:
        groups.setdefault(kind, []).append((name, columns, mapped))
    values = {}
    for kind, entries in groups.items():
        if len(entries) != 1: continue
        name, columns, mapped = entries[0]
        from app.calc import _currency
        if len(set().union(*(_currency(n.text) for n in nodes if n.loc.startswith(name + '-')))) > 1:
            continue
        for period, pattern in [('本期', r'本期|本年累计|期末'), ('上期', r'上期|上年同期|期初|年初')]:
            cols = [c for c, title in columns.items() if re.search(pattern, title)]
            if len(cols) != 1: continue
            for key, cells in mapped.items():
                node = cells.get(cols[0])
                value = numeric(node.text) if node else None
                if value is not None:
                    values[(kind, period, key)] = value
    rows = {}
    def emit(label, kind, numerator, denominator, *, multiple=False):
        for period in ('本期', '上期'):
            num = [values.get((kind, period, k)) for k, sign in numerator]
            den = values.get((kind, period, denominator))
            if any(v is None for v in num) or den in (None, 0): continue
            with localcontext() as ctx:
                ctx.prec = 50
                value = sum((v * sign for v, (_, sign) in zip(num, numerator)), Decimal(0)) / den
                value = (value * (1 if multiple else 100)).quantize(Decimal('.01' if multiple else '.1'), rounding=ROUND_HALF_UP)
            rows.setdefault(label, {})[period] = f'{value:f}' + ('倍' if multiple else '%')
    emit('毛利率', 'profit', [('营业收入', 1), ('营业成本', -1)], '营业收入')
    for label, key in [('营业利润率', '营业利润'), ('净利率', '净利润')]:
        emit(label, 'profit', [(key, 1)], '营业收入')
    emit('期间费用率', 'profit', [(k, 1) for k in ('销售费用', '管理费用', '研发费用', '财务费用')], '营业收入')
    emit('资产负债率', 'balance', [('负债合计', 1)], '资产总计')
    emit('流动比率', 'balance', [('流动资产合计', 1)], '流动负债合计', multiple=True)
    emit('速动比率', 'balance', [('流动资产合计', 1), ('存货', -1)], '流动负债合计', multiple=True)
    for key in ('营业收入', '净利润'):
        current, prior = (values.get(('profit', p, key)) for p in ('本期', '上期'))
        if current is None or prior in (None, 0): continue
        with localcontext() as ctx:
            ctx.prec = 50
            value = ((current / prior - 1) * 100).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)
        rows[key + '同比'] = {'本期': f'{value:f}%'}
    # 跨表比率须有一致的明确币种与金额单位，否则不猜比例尺。
    if len(groups.get('cash', [])) == len(groups.get('profit', [])) == 1:
        from app.calc import _currency
        def unit(kind):
            name = groups[kind][0][0]
            texts = [n.text for n in nodes if n.loc.startswith(name + '-')]
            currencies = set().union(*(_currency(t) for t in texts))
            units = {m.group() for t in texts for m in re.finditer(r'亿元|万元|千元|(?<![万千亿美日欧港])元', t)}
            return currencies, units
        if unit('cash') == unit('profit') and len(unit('cash')[0]) == len(unit('cash')[1]) == 1:
            for period in ('本期', '上期'):
                a = values.get(('cash', period, '经营活动产生的现金流量净额'))
                b = values.get(('profit', period, '净利润'))
                if a is None or b in (None, 0): continue
                value = (a / b).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                rows.setdefault('经营现金流与净利润之比', {})[period] = f'{value:f}倍'
    return '\n'.join(label + '：' + '（'.join(p + ' ' + periods[p] for p in ('本期', '上期') if p in periods)
                     + ('）' if len(periods) == 2 else '') for label, periods in rows.items())


def section(db, task):
    from app.admin.settings_store import get_setting
    from app.checks.service import load_inputs
    if task.kind != 'review' or not get_setting(db, 'review.send_ratios'):
        return ''
    nodes, _ = load_inputs(db, task.id)
    text = calculate(nodes)
    return '【本地财务比率】\n' + text if text else ''
