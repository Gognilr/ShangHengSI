"""报价表、投标总价与报价规律（bid.md 3.1，第四十条（四））。全部用 Decimal（ROUND_HALF_UP），不记录金额日志。

报价表识别复用审核的 L-01、L-02（app.checks.amounts）；投标总价取“投标总价 / 投标报价”等标签后的小写金额。
分项按品名归一化对齐（对不上且行数相同时按行序），两两计算单价比值的变异系数、差额与尾数习惯。
各家总价另做比较（V34Q-27）：最高 / 最低 / 平均、比最低价高、偏离平均、异常低价提示与总价等差 / 等比线索；
只有唯一总价、币种一致的投标人参加比较。投标人仍按录入顺序排列，不提供按价格排序。
"""
import re
import unicodedata
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
# 以下三个阈值待 V34-B-08 用真实标书校准
LOW_PRICE = Decimal("0.20")          # 低于其余各家平均 20% 以上：异常低价提示（参照第五十一条（五）低于成本）
TOTAL_DIFF_TOL = Decimal("1")        # 总价等差：相邻差额之差不超过 1 元
TOTAL_RATIO_TOL = Decimal("0.00005")  # 总价等比：相邻比值之差不超过 0.00005
_UPPER = re.compile(r"[零壹贰叁肆伍陆柒捌玖拾佰仟]")
_TOTAL_LABEL = re.compile(r"(?<!不含税)(?<!分项)(?:投标报价总金额|投标总报价|投标总价|投标报价|报价总额|报价合计|总报价|总投标价|总计报价|投标价格|投标金额)")
_QUOTE_HEADING = re.compile(r"投标函|投标书|(?:开标|投标)一览表|报价(?:文件|单|表|章节)")
_SERVICE_LABEL = re.compile(r"报价(?!单位|人|有效|咨询|要求|文件|表|单)|小写|评价费(?:用)?|服务费|合同价|项目费用|费用报价|总价|合计|￥|¥")
_NOT_TOTAL = re.compile(r"最高限价|控制价|保证金|不含税|分项|单价|历史合同|业绩合同")
_BARE_MONEY = re.compile(r"^[\s:：()（）]*(?:小写[\s:：()（）]*)?([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)(万?元|万)?[\s。；;()（）]*$")
_NAME = re.compile(r"名称|品名|货物|设备|品目|项目|内容|规格|型号")
_STRIP = re.compile(r"[\W_]+")


def labelled_totals(nodes):
    """[(值, 币种, node)]：总价标签、报价页的服务费，支持表头同列与同页相邻节点。

    宽泛标签仅用于有报价标题的页（无页码的 Word 用最近标题）；优先小写，无小写时取大写。
    不把历史合同中的金额或不含税价当作本次报价，多个报价仍保留供人工核对。
    """
    out, column_hits, summarized = [], set(), set()
    cells = {}
    quote_pages = set()
    for n in nodes:
        cell = table_cell(n.loc)
        if cell:
            cells[(n.file_seq, *cell)] = n
        if len(n.text) < 160 and _QUOTE_HEADING.search(n.text) and not re.search(r"\.{3}|…|目录|分项|备品备件", n.text):
            quote_pages.add((n.file_seq, getattr(n, 'page', None)))

    def amount(text, label, bare=False):
        text = unicodedata.normalize('NFKC', text)
        # 前置“人民币”被 OCR 拆开时恢复；不连接不同数字。
        text = re.sub(r'人\s*民\s*币', '人民币', text)
        text = re.sub(r'(?<=[零壹贰叁肆伍陆柒捌玖拾佰仟万亿])[_\s]+(?=[零壹贰叁肆伍陆柒捌玖拾佰仟万亿元圆整])', '', text)
        inherited_wan = '万元' in label and not re.search(r'\d|[壹贰叁肆伍陆柒捌玖]', label)
        hits = find_amounts(text)
        lower = [h for h in hits if h.value is not None and not _UPPER.search(text[h.start:h.end])]
        found = lower or [h for h in hits if h.value is not None]
        if found:
            h = found[0]
            # 单位只写在表头时继承；数值自身有单位时以自身为准。
            scale = 10000 if inherited_wan and h.currency is None and not re.search(r'万?元|万圆', text) else 1
            return h.value * scale, h.currency
        match = _BARE_MONEY.fullmatch(text) if bare else None
        if match:
            scale = 10000 if '万' in (match[2] or '') or inherited_wan else 1
            return Decimal(match[1].replace(',', '')) * scale, 'CNY'
        return None

    section = None
    for i, n in enumerate(nodes):
        text = unicodedata.normalize('NFKC', n.text)
        if i == 0 or nodes[i - 1].file_seq != n.file_seq:
            section = None
        if getattr(n, 'kind', '') == 'heading':
            section = bool(_QUOTE_HEADING.search(text))
        in_quote = (n.file_seq, getattr(n, 'page', None)) in quote_pages if getattr(n, 'page', None) is not None else section
        matches = list(_TOTAL_LABEL.finditer(text))
        if in_quote:
            matches += [m for m in _SERVICE_LABEL.finditer(text)
                        if not any(s.start() <= m.start() < s.end() for s in matches)]
        for m in sorted(matches, key=lambda m: m.start()):
            if _NOT_TOTAL.search(text[max(0, m.start() - 6):m.end()]):
                continue
            if m.group() == '合计' and text[:m.start()].strip(' :()'):
                continue
            tail = text[m.end():]
            # “报价含…税费”“总价的百分比”等说明不作为总价标签。
            if re.match(r'\s*(?:含配套|有效|的|=)', tail):
                continue
            tail = re.split(r'不含税|保证金|最高限价', tail, maxsplit=1)[0]
            label = text[:m.end()] + ('（万元）' if re.search(r'[（(]万元[）)]', tail[:8]) else '')
            found = amount(tail[:100], label, bare=True)
            target = n
            cell = table_cell(n.loc)
            if found is None and cell and not tail.strip(' :()万元人民币含税小写'):
                table, col, row = cell
                # 单独标签格优先同行右侧；列标题则取下一行同列。
                right = sorted((k[2], v) for k, v in cells.items()
                               if k[0] == n.file_seq and k[1] == table and k[3] == row and int(k[2]) > int(col))
                candidates = [right[0][1]] if right else []
                below = cells.get((n.file_seq, table, col, row + 1))
                if below:
                    candidates.append(below)
                for candidate in candidates:
                    found = amount(candidate.text, text, bare=True)
                    if found:
                        target = candidate
                        if candidate is below:
                            column_hits.add(id(candidate))
                        break
            if found is None and not cell and i + 1 < len(nodes):
                nxt = nodes[i + 1]
                if (nxt.file_seq == n.file_seq and getattr(nxt, 'page', None) == getattr(n, 'page', None)
                        and not table_cell(nxt.loc) and not _NOT_TOTAL.search(tail)):
                    found = amount((tail + '\n' + nxt.text)[:140], text, bare=True)
                    target = nxt
            if found:
                out.append((*found, target))
                if cell and target is not cells.get((n.file_seq, cell[0], cell[1], cell[2] + 1)):
                    summarized.add((n.file_seq, cell[0]))
    # 表中已有明确合计行时，列标题下第一项的“总价”属于分项金额。
    return [(v, c, n) for v, c, n in out
            if not (id(n) in column_hits and (n.file_seq, table_cell(n.loc)[0]) in summarized)]


def _name_col(table):
    for row in (table.header - 1, table.header):
        for col, node in sorted(table.rows.get(row, {}).items()):
            if _NAME.search(node.text) and col not in table.columns.values():
                return col
    return None


def separate_quote_scopes(nodes, totals):
    """只有明确标题能把每个金额分到不同范围时，才不按重复总价处理；不猜测合计。"""
    def scope(text):
        label = re.search(r'表[一二三四五六七八九十](?=[：:、\s）)]|$)', text)
        if label:
            return label[0]
        if re.search(r'泵头|泵体', text):
            return '泵头'
        if '电机' in text:
            return '电机'
        return None

    pages, at = {}, {}
    current, last_file = None, None
    for n in nodes:
        if n.file_seq != last_file:
            current, last_file = None, n.file_seq
        short = len(n.text) < 160
        heading = short and (_QUOTE_HEADING.search(n.text) or re.match(r'^表[一二三四五六七八九十][：:、\s]', n.text))
        if heading or getattr(n, 'kind', '') == 'heading':
            current = scope(n.text)
            if current and getattr(n, 'page', None) is not None:
                pages.setdefault((n.file_seq, n.page), set()).add(current)
        at[id(n)] = current
    by_value = {}
    for value, _currency, n in totals:
        own = re.search(r'表[一二三四五六七八九十](?=[：:、\s）)]|$)', n.text)
        labels = pages.get((n.file_seq, getattr(n, 'page', None)), set())
        found = own[0] if own else next(iter(labels)) if len(labels) == 1 else at.get(id(n)) if getattr(n, 'page', None) is None else None
        by_value.setdefault(rounded(value), set())
        if found:
            by_value[rounded(value)].add(found)
    values = list(by_value.values())
    return len(values) > 1 and all(values) and all(not (a & b) for a, b in combinations(values, 2))


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


def _pct(x, signed=False):
    q = (x * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{'+' if signed and q > 0 else ''}{q}%"


def _trim(x):
    return f"{x:f}".rstrip("0").rstrip(".") if "." in f"{x:f}" else f"{x:f}"


def compare(totals):
    """各家总价比较。totals 为 {投标人: Decimal}（只含参加比较的，按录入顺序）。
    返回 (汇总或 None, {投标人: 字段})；字段：mark 最高 / 最低、above_lowest(_pct) 比最低价高、vs_mean 偏离平均、
    low_price 异常低价（低于其余各家平均 LOW_PRICE 以上，其余至少 2 家）。"""
    if len(totals) < 2:
        return None, {}
    with localcontext() as ctx:
        ctx.prec = 34
        vals = {b: rounded(v) for b, v in totals.items()}
        hi, lo = max(vals.values()), min(vals.values())
        mean = sum(vals.values(), Decimal(0)) / len(vals)
        out = {}
        for b, v in vals.items():
            row = {"mark": None if hi == lo else "highest" if v == hi else "lowest" if v == lo else None,
                   "above_lowest": money(v - lo) if v != lo else None,
                   "above_lowest_pct": _pct((v - lo) / lo) if v != lo and lo > 0 else None,
                   "vs_mean": _pct((v - mean) / mean, signed=True) if mean > 0 else None, "low_price": None}
            others = [w for o, w in vals.items() if o != b]
            if len(others) >= 2:
                other_mean = sum(others, Decimal(0)) / len(others)
                if other_mean > 0 and v < other_mean * (1 - LOW_PRICE):
                    row["low_price"] = {"others": len(others), "others_mean": money(other_mean),
                                        "below_pct": _pct((other_mean - v) / other_mean)}
            out[b] = row
        summary = {"n": len(vals), "highest": money(hi), "lowest": money(lo), "mean": money(mean),
                   "spread_pct": _pct((hi - lo) / lo) if lo > 0 else None}
    return summary, out


def total_patterns(totals):
    """三家及以上总价从低到高呈等差或等比（第四十条（四）“投标报价呈规律性差异”）。
    返回 [(投标人按金额升序, "diff" | "ratio", 显示用的差额或比值)]；已报的一组的子集不再重复报。"""
    ordered = sorted(((rounded(v), b) for b, v in totals.items()), key=lambda x: x[0])
    found = []
    with localcontext() as ctx:
        ctx.prec = 34
        for size in range(len(ordered), 2, -1):
            for combo in combinations(ordered, size):
                ids = [b for _v, b in combo]
                if any(set(ids) <= set(f[0]) for f in found):
                    continue
                vals = [v for v, _b in combo]
                if vals[0] <= 0 or any(x == y for x, y in zip(vals, vals[1:])):
                    continue
                diffs = [y - x for x, y in zip(vals, vals[1:])]
                ratios = [y / x for x, y in zip(vals, vals[1:])]
                if max(diffs) - min(diffs) <= TOTAL_DIFF_TOL:
                    found.append((ids, "diff", money(sum(diffs, Decimal(0)) / len(diffs))))
                elif max(ratios) - min(ratios) <= TOTAL_RATIO_TOL:
                    k = (sum(ratios, Decimal(0)) / len(ratios)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                    found.append((ids, "ratio", _trim(k)))
    return found


def analyze(bidders, per_bidder_nodes, ceiling=None, currency=None, place=None):
    """返回 (报价分析 JSON, 线索, 每家 {总价, 币种, 多个总价, 表, L 结果, 异常低价})。bidders 为按录入顺序的 id；
    place(node) 给出证据位置（总价规律线索用，可省）。"""
    facts, analysis = {}, {"bidders": {}, "items": [], "pairs": [], "aligned_by": {}, "compare": None, "total_patterns": []}
    for b in bidders:
        nodes = per_bidder_nodes.get(b, [])
        tables, results = quote_tables(nodes)
        main = _main(tables)
        totals = labelled_totals(nodes)
        distinct = sorted({rounded(v) for v, _c, _n in totals})
        separate_scopes = separate_quote_scopes(nodes, totals)
        total = distinct[0] if len(distinct) == 1 else None
        table_total = None
        if main is not None:
            info, items = main[1], main[2]
            table_total = info.total if info.total is not None else (
                sum((rounded(it[3]) for it in items), Decimal(0)) if items and all(it[3] is not None for it in items) else None)
        currency_found = next((c for _v, c, _n in totals if c), None)
        facts[b] = {"total": total if total is not None else (table_total if not distinct else None),
                    "currency": currency_found, "totals": totals, "distinct": distinct, "main": main,
                    "table_total": table_total, "results": results, "separate_scopes": separate_scopes}
        row = {"total": money(facts[b]["total"]) if facts[b]["total"] is not None else None,
               "totals_found": [money(v) for v in distinct], "currency": currency_found,
               "table": None, "over_ceiling": None}
        if main is not None:
            row["table"] = {"name": main[0].name, "rows": len(main[2]),
                            "total": money(table_total) if table_total is not None else None}
        if ceiling is not None and facts[b]["total"] is not None and (currency_found in (None, currency)):
            row["over_ceiling"] = money(facts[b]["total"] - ceiling)
        elif ceiling is not None and len(distinct) > 1 and not separate_scopes and (currency_found in (None, currency)):
            # 多个不同总价：按最高者与限价比较（V34Q-25 第 4 条），over_ceiling 仍只表示唯一总价
            row["over_ceiling_max"] = money(distinct[-1] - ceiling)
        analysis["bidders"][str(b)] = row
    # 总价比较：只有唯一总价、币种与关键参数一致（或未写币种）的参加；多个总价的不参加
    eligible = {}
    for b in bidders:
        f, row = facts[b], analysis["bidders"][str(b)]
        row["not_compared"] = None
        if len(f["distinct"]) > 1:
            row["not_compared"] = "不同范围报价，未参加比较" if f['separate_scopes'] else "多个总价，未参加比较"
        elif f["total"] is not None and f["currency"] not in (None, currency or "CNY"):
            row["not_compared"] = f"币种为 {f['currency']}，未参加比较"
        elif f["total"] is not None:
            eligible[b] = f["total"]
    summary, fields = compare(eligible)
    analysis["compare"] = summary
    for b in bidders:
        analysis["bidders"][str(b)] |= fields.get(b, {"mark": None, "above_lowest": None, "above_lowest_pct": None,
                                                     "vs_mean": None, "low_price": None})
        facts[b]["low_price"] = fields.get(b, {}).get("low_price")
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
    # 总价规律（三家及以上）：线索按录入顺序列投标人，证据按金额从低到高
    order = {b: i for i, b in enumerate(bidders)}
    for ids, how, step in total_patterns(eligible):
        title = (f"{len(ids)} 家投标总价呈等差（从低到高每家相差 {step}）" if how == "diff"
                 else f"{len(ids)} 家投标总价呈等比（从低到高每家约为前一家的 {step} 倍）")
        places = {}
        for b in ids:
            f = facts[b]
            node = f["totals"][0][2] if f["totals"] else (f["main"][2][0][4] if f["main"] and f["main"][2] else None)
            places[str(b)] = [place(node)] if place and node is not None else []
        rows = [{"label": "投标总价（从低到高）", "value": "、".join(money(eligible[b]) for b in ids), "places": places}]
        clues.append({"ids": tuple(sorted(ids, key=order.get)), "level": "medium", "title": title, "rows": rows})
        analysis["total_patterns"].append({"ids": [str(b) for b in ids], "kind": how, "step": step, "level": "medium"})
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
