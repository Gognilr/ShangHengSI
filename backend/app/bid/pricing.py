"""报价表、投标总价与报价规律（bid.md 3.1，第四十条（四））。全部用 Decimal（ROUND_HALF_UP），不记录金额日志。

报价表识别复用审核的 L-01、L-02（app.checks.amounts）；投标总价取“投标总价 / 投标报价”等标签后的小写金额。
分项按品名归一化对齐（对不上且行数相同时按行序），两两计算单价比值的变异系数、差额与尾数习惯。
"""
import re
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP, localcontext
from itertools import combinations

from app.checks.amounts import _check_table, _scan_tables
from app.checks.numbers import money, parse_number, rounded
from app.redaction.detect import find_amounts
from app.redaction.normalize import normalize_value
from app.redaction.tables import table_cell

MIN_ITEMS = 5
CV_STRONG = Decimal("0.005")  # 比值的变异系数 < 0.5%
_UPPER = re.compile(r"[零壹贰叁肆伍陆柒捌玖拾佰仟]")
_TOTAL_LABEL = re.compile(r"(?<!不含税)(?<!分项)(?:投标总报价|投标总价|投标报价|报价总额|报价合计|总报价|投标价格|投标金额)")
_NAME = re.compile(r"名称|品名|货物|设备|品目|项目|内容|规格|型号")
_STRIP = re.compile(r"[\W_]+")


def _lower_amounts(text):
    return [h for h in find_amounts(text) if h.value is not None and not _UPPER.search(text[h.start:h.end])]


def labelled_totals(nodes):
    """[(值, 币种, node)]：标签后 40 字内的第一个小写金额；标签单独成格时取同行下一格。"""
    out = []
    for i, n in enumerate(nodes):
        text = n.text
        for m in _TOTAL_LABEL.finditer(text):
            tail = [h for h in _lower_amounts(text) if m.end() <= h.start <= m.end() + 40
                    and not _TOTAL_LABEL.search(text[m.end():h.start])]
            if tail:
                out.append((tail[0].value, tail[0].currency, n))
            elif not text[m.end():].strip(" :：（）()元人民币") and i + 1 < len(nodes) and nodes[i + 1].file_seq == n.file_seq:
                nxt = nodes[i + 1]
                hits = _lower_amounts(nxt.text)
                if hits and (table_cell(nxt.loc) or len(nxt.text) <= 40):
                    out.append((hits[0].value, hits[0].currency, nxt))
    return out


def _name_col(table):
    for row in (table.header - 1, table.header):
        for col, node in sorted(table.rows.get(row, {}).items()):
            if _NAME.search(node.text) and col not in table.columns.values():
                return col
    return None


def quote_tables(nodes):
    """已识别的报价表：[(表, 检查结果, 分项 [(键, 名称, 单价, 金额, node)])]；以及 L-01、L-02 结果。"""
    out, results = [], []
    with localcontext() as ctx:
        ctx.prec = max(28, sum(len(n.text) for n in nodes) + 8)
        for table in _scan_tables(nodes):
            if not table.recognized:
                continue
            info, found = _check_table(table)
            results += [r for r in found if r.code in ("L-01", "L-02")]
            col = _name_col(table)
            items = []
            for row in table.data_rows:
                name = table.rows[row][col].text.strip() if col is not None and col in table.rows[row] else ""
                price, amount = parse_number(table.text(row, "price")), parse_number(table.text(row, table.amount))
                if price is None and amount is None:
                    continue
                node = table.node(row, "price") or table.node(row, table.amount)
                items.append((_STRIP.sub("", normalize_value(name)), name, price, amount, node))
            out.append((table, info, items))
    return out, results


def _main(tables):
    """分项最多的报价表作为主表（同样多时取有合计行的、靠前的）。"""
    if not tables:
        return None
    return max(tables, key=lambda t: (len(t[2]), t[1].total is not None))


def _value(item):
    return item[2] if item[2] is not None else item[3]


def align(a_items, b_items):
    """按品名对齐；对上的不足 5 项且两表分项数相同（同一份分项清单）时按行序对齐。返回 ([(a, b)], 方式)。"""
    b_by = {}
    for it in b_items:
        if it[0]:
            b_by.setdefault(it[0], it)
    pairs = [(it, b_by[it[0]]) for it in a_items if it[0] and it[0] in b_by]
    if len(pairs) < MIN_ITEMS and len(a_items) == len(b_items) >= MIN_ITEMS:
        return list(zip(a_items, b_items)), "row"
    return pairs, "name"


def _tail(value):
    q = rounded(value)
    return int(q) % 10 if q == q.to_integral_value() else None


def _vals(pairs, fixed):
    vals = [(_value(a), _value(b)) for a, b in pairs if a[0] not in fixed]
    return [(x, y) for x, y in vals if x is not None and y is not None and x > 0 and y > 0]


def ratio_stats(pairs, fixed=frozenset()):
    """两家对齐分项的单价比值统计（项数、平均比值、变异系数）；不足 5 项返回 None。报价分析页对每一对都展示。"""
    vals = _vals(pairs, fixed)
    n = len(vals)
    if n < MIN_ITEMS:
        return None
    with localcontext() as ctx:
        ctx.prec = 34
        ratios = [x / y for x, y in vals]
        if sum(ratios, Decimal(0)) < n:
            ratios = [y / x for x, y in vals]
        mean = sum(ratios, Decimal(0)) / n
        var = sum(((r - mean) ** 2 for r in ratios), Decimal(0)) / n
        cv = var.sqrt() / mean if mean else Decimal(0)
    return {"n": n, "mean_ratio": str(mean.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "cv": f"{(cv * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"}


def pattern(pairs, fixed=frozenset()):
    """比值恒定 / 等差 / 同一尾数习惯；返回 (等级, 说明, 统计) 或 None。"""
    vals = _vals(pairs, fixed)
    n = len(vals)
    if n < MIN_ITEMS:
        return None
    with localcontext() as ctx:
        ctx.prec = 34
        ratios = [x / y for x, y in vals]
        if sum(ratios, Decimal(0)) < n:  # 统一写成“高的一家约为低的一家的 k 倍”
            ratios = [y / x for x, y in vals]
        mean = sum(ratios, Decimal(0)) / n
        var = sum(((r - mean) ** 2 for r in ratios), Decimal(0)) / n
        cv = var.sqrt() / mean if mean else Decimal(0)
    stats = {"n": n, "mean_ratio": str(mean.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
             "cv": f"{(cv * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"}
    if cv < CV_STRONG:
        same = all(rounded(x) == rounded(y) for x, y in vals)
        label = "分项单价完全相同" if same else f"分项单价比值恒定（一家约为另一家的 {stats['mean_ratio']} 倍，变异系数 {stats['cv']}）"
        return "strong", label, stats
    diffs = {rounded(x - y) for x, y in vals}
    if len(diffs) == 1:
        return "medium", f"分项单价差额相同（每项相差 {money(next(iter(diffs)))}）", stats
    ta, tb = Counter(_tail(x) for x, _ in vals), Counter(_tail(y) for _, y in vals)
    for digit in set(ta) & set(tb):
        if digit not in (None, 0, 5) and ta[digit] >= 0.8 * n and tb[digit] >= 0.8 * n:
            return "medium", f"两家分项单价的个位数大多同为 {digit}（同一尾数习惯）", stats
    return None


def analyze(bidders, per_bidder_nodes, ceiling=None, currency=None):
    """返回 (报价分析 JSON, 线索, 每家 {总价, 币种, 多个总价, 表, L 结果})。bidders 为按录入顺序的 id。"""
    facts, analysis = {}, {"bidders": {}, "items": [], "pairs": [], "aligned_by": {}}
    for b in bidders:
        nodes = per_bidder_nodes.get(b, [])
        tables, results = quote_tables(nodes)
        main = _main(tables)
        totals = labelled_totals(nodes)
        distinct = sorted({rounded(v) for v, _c, _n in totals})
        total = distinct[0] if len(distinct) == 1 else None
        table_total = None
        if main is not None:
            info, items = main[1], main[2]
            table_total = info.total if info.total is not None else (
                sum((rounded(it[3]) for it in items), Decimal(0)) if items and all(it[3] is not None for it in items) else None)
        currency_found = next((c for _v, c, _n in totals if c), None)
        facts[b] = {"total": total if total is not None else (table_total if not distinct else None),
                    "currency": currency_found, "totals": totals, "distinct": distinct, "main": main,
                    "table_total": table_total, "results": results}
        row = {"total": money(facts[b]["total"]) if facts[b]["total"] is not None else None,
               "totals_found": [money(v) for v in distinct], "currency": currency_found,
               "table": None, "over_ceiling": None}
        if main is not None:
            row["table"] = {"name": main[0].name, "rows": len(main[2]),
                            "total": money(table_total) if table_total is not None else None}
        if ceiling is not None and facts[b]["total"] is not None and (currency_found in (None, currency)):
            row["over_ceiling"] = money(facts[b]["total"] - ceiling)
        analysis["bidders"][str(b)] = row
    # 各家都填同一价格的分项（多为招标文件给定的暂列金额、固定单价）不参与规律比对
    keyed = {b: {it[0]: _value(it) for it in facts[b]["main"][2] if it[0]} for b in bidders if facts[b]["main"]}
    fixed = set()
    if len(keyed) >= 3:
        common = set.intersection(*(set(v) for v in keyed.values()))
        fixed = {k for k in common if len({rounded(v[k]) for v in keyed.values() if v[k] is not None}) == 1}
    clues = []
    for a, b in combinations(bidders, 2):
        ma, mb = facts[a]["main"], facts[b]["main"]
        if ma is None or mb is None:
            continue
        pairs, how = align(ma[2], mb[2])
        found = pattern(pairs, fixed)
        stat = {"a": str(a), "b": str(b), "aligned": len(pairs), "aligned_by": how, **(ratio_stats(pairs, fixed) or {})}
        if found:
            level, label, numbers = found
            stat |= numbers | {"level": level, "label": label}
            rows = [{"label": "分项单价", "value": f"对齐 {numbers['n']} 项（{'按行序' if how == 'row' else '按品名'}）",
                     "items": [{"name": x[1] or y[1], "left": money(_value(x)), "right": money(_value(y))}
                               for x, y in pairs[:20] if _value(x) is not None and _value(y) is not None]}]
            clues.append({"ids": (a, b), "level": level, "title": label, "rows": rows})
        analysis["pairs"].append(stat)
    # 分项对齐表（以第一家有报价表的为基准，最多 300 行）
    base = next((b for b in bidders if facts[b]["main"]), None)
    if base is not None:
        matched = {}
        for other in bidders:
            if other != base and facts[other]["main"] is not None:
                pairs, how = align(facts[base]["main"][2], facts[other]["main"][2])
                matched[other] = {id(x): y for x, y in pairs}
                analysis["aligned_by"][str(other)] = how
        for it in facts[base]["main"][2][:300]:
            prices = {str(base): money(_value(it)) if _value(it) is not None else None}
            for other, found in matched.items():
                y = found.get(id(it))
                prices[str(other)] = money(_value(y)) if y is not None and _value(y) is not None else None
            analysis["items"].append({"name": it[1], "prices": prices, "fixed": it[0] in fixed})
        analysis["base"] = str(base)
    return analysis, clues, facts
