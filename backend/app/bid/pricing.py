"""报价表、投标总价与报价规律（bid.md 3.1，第四十条（四））。全部用 Decimal（ROUND_HALF_UP），不记录金额日志。

报价表识别复用审核的 L-01、L-02（app.checks.amounts）；投标总价取“投标总价 / 投标报价”等标签后的小写金额。
分项按品名归一化对齐（对不上且行数相同时按行序），两两计算单价比值的变异系数、差额与尾数习惯。
各家总价另做比较（V34Q-27）：最高 / 最低 / 平均、比最低价高、偏离平均、异常低价提示与总价等差 / 等比线索；
只有唯一总价、币种一致的投标人参加比较。投标人仍按录入顺序排列，不提供按价格排序。
"""
import re
import unicodedata
from collections import Counter
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP, localcontext
from itertools import combinations

from openpyxl.utils.cell import column_index_from_string

from app.checks.amounts import _CURRENCY_TOKEN, _check_table, _scan_tables
from app.checks.numbers import CENT, money, parse_number, parse_rate, rounded
from app.redaction.detect import _CURRENCY, find_amounts
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
_SUB_LIST = re.compile(r"(?:不)?含在(?:投标)?总价内|单独报价|(?:备品备件|备件|专用工具|工具)(?:价格)?清单")
_MOTOR_SCOPE = re.compile(r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*元\s*[（(](不?含电机)(?:价格|价)?[）)]")
RATE_LABELS = {"rate": "投标费率", "discount": "投标下浮率"}
RATE_LIMIT_LABELS = {"rate": "最高费率", "discount": "最低下浮率"}
_QUOTE_RATE = {"rate": r"(?<![税浮折利汇])费率", "discount": r"下浮率"}
_PERCENT = r"(\d{1,3}(?:\.\d{1,8})?)\s*%(?![\d%]|\s*(?:[-~～—–至]|或)\s*\d)"
_TAX_LABEL = r"(?:投标)?(?:总报价|报价|总金额|金额|总价|合计|价款|价)"
_TAX_FIELD = re.compile(rf"(?<!不)不?含(?:增值)?税(?:的)?{_TAX_LABEL}|{_TAX_LABEL}\s*\(不?含(?:增值)?税\)|(?:增值)?税率")
_TAX_AMOUNT = re.compile(r"\s*[:=为]?\s*(?:人民币|美元|欧元|港币|港元|CNY|USD|EUR|HKD|[¥￥])?\s*([\d,]+(?:\.\d+)?)\s*(万元|元)?\s*")


def rate_text(value):
    return f"{_trim(value)}%"


def rate_value(text):
    """关键参数使用百分数，2 或 2% 均表示 2%。"""
    match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,8})?)\s*%?", unicodedata.normalize("NFKC", text).strip())
    return Decimal(match[1]) if match and Decimal(match[1]) <= 100 else None


def _quote_fields(nodes):
    """物理表格按文件隔离，同行用途由调用方确认。"""
    tables = {}
    for n in nodes:
        if cell := table_cell(n.loc):
            table, col, row = cell
            tables.setdefault((n.file_seq, table), {}).setdefault(row, {})[_column_index(col)] = n
    return tables


def labelled_rates(nodes, mode):
    """只取明确的投标费率 / 下浮率。表头可注明 %，文字须带 %。"""
    label = _QUOTE_RATE[mode]
    explicit = re.compile(rf"(?:投标报价|投标|报价|综合|统一)\s*\(?\s*{label}\s*\)?|{label}\s*报价")
    bare = rf"{label}(?:\s*\(%\))?"
    fields = re.compile(rf"(?:(?:{explicit.pattern})(?:\s*\(%\))?|{bare})")
    found = []

    def add(text, node, percent_required=True):
        if percent_required and not text.strip().endswith("%"):
            return
        if (value := rate_value(text)) is not None:
            found.append((value, None, node))

    for n in nodes:
        text = unicodedata.normalize("NFKC", n.text)
        if re.search(r"最高|最低|不得|不[高低]于|至少|限价|控制价|历史合同|业绩合同", text):
            continue
        pattern = fields.pattern if table_cell(n.loc) or re.match(rf"^\s*{bare}\s*[:=为]", text) else explicit.pattern
        for match in re.finditer(rf"(?:{pattern})\s*[:=为]?\s*{_PERCENT}", text):
            add(match[1] + "%", n)
        # “投标报价：下浮 8%”。
        if mode == "discount" and (match := re.search(rf"(?:投标报价|投标费率)\s*[:=为]?\s*下浮\s*{_PERCENT}", text)):
            add(match[1] + "%", n)
    for rows in _quote_fields(nodes).values():
        for row, cells in rows.items():
            for col, n in cells.items():
                text = unicodedata.normalize("NFKC", n.text).strip(" ：:")
                if not fields.fullmatch(text):
                    continue
                right = cells.get(col + 1)
                if right and rate_value(right.text) is not None:
                    add(unicodedata.normalize("NFKC", right.text), right, "(%)" not in text)
                elif below := rows.get(row + 1, {}).get(col):
                    add(unicodedata.normalize("NFKC", below.text), below, "(%)" not in text)
    return found


def _tax_quotes(nodes):
    """配套总价来自同一段、同一表：并排字段、同一合计行或三行标签值。"""
    candidates = [n for n in nodes if "税" in n.text]
    if not any("不含税" in n.text or "不含增值税" in n.text for n in candidates):
        return

    def key(m):
        return "rate" if "税率" in m[0] else "net" if "不含" in m[0] else "gross"

    def value(k, raw, label):
        raw = unicodedata.normalize("NFKC", raw).strip(" ：:，,；;。")
        if k == "rate":
            rate = parse_rate(raw + "%" if label.endswith("%") and not raw.endswith("%") else raw)
            return (rate, None) if rate is not None and rate <= 1 else None
        if not (m := _TAX_AMOUNT.fullmatch(raw)) or not re.fullmatch(r"\d+(?:,\d{3})*(?:\.\d+)?", m[1]):
            return None
        token = _CURRENCY_TOKEN.search(raw) or _CURRENCY_TOKEN.search(label)
        currency = _CURRENCY.get(token[0].upper()) if token else None
        return Decimal(m[1].replace(",", "")) * (10000 if "万元" in (m[2] or label) else 1), currency

    def add(fields, k, raw, label, n):
        if parsed := value(k, raw, label):
            fields.setdefault(k, []).append((*parsed, n))

    groups = []
    table_ids = set()
    for n in candidates:
        if cell := table_cell(n.loc):
            table_ids.add((n.file_seq, cell[0]))
            continue  # 表格只在后面按行匹配，避免同一单元格被核对两次
        text = unicodedata.normalize("NFKC", n.text)
        if re.search(r"最高限价|控制价|历史合同|业绩合同", text):
            continue
        fields = {}
        matches = list(_TAX_FIELD.finditer(text))
        for i, m in enumerate(matches):
            raw = text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            add(fields, key(m), raw, m[0], n)
        if fields:
            groups.append((fields, True))
    for (seq, table), rows in (_quote_fields(nodes).items() if table_ids else ()):
        if (seq, table) not in table_ids:
            continue
        vertical = {}
        for row, cells in sorted(rows.items()):
            horizontal, columns = {}, {}
            for col, n in cells.items():
                text = unicodedata.normalize("NFKC", n.text).strip()
                m = _TAX_FIELD.match(text)
                if not m:
                    continue
                k = key(m)
                raw = text[m.end():].strip(" :：")
                unit = re.match(r"\((万元|元|%|人民币|美元|欧元|港币|CNY|USD|EUR|HKD)\)", raw)
                label = m[0] + (unit[1] if unit else "")
                raw = raw[unit.end():].strip(" :：") if unit else raw
                if raw:
                    add(horizontal, k, raw, label, n)
                else:
                    columns[col] = (k, label)
                    if right := cells.get(col + 1):
                        add(horizontal, k, right.text, label, right)
            if horizontal:
                groups.append((horizontal, True))
            if horizontal:
                for k, vals in horizontal.items():
                    vertical.setdefault(k, []).extend(vals)
            if len({k for k, _ in columns.values()}) == 3:
                for after in sorted(r for r in rows if r > row):
                    detail = rows[after]
                    is_total = any(re.match(r"^(?:投标)?(?:总[价计额]|合计|价税合计)", n.text.strip()) for n in detail.values())
                    fields = {}
                    for col, (k, label) in columns.items():
                        if n := detail.get(col):
                            add(fields, k, n.text, label, n)
                    if fields:
                        groups.append((fields, is_total or len(rows) == 2))
        if vertical:
            groups.append((vertical, True))

    seen = set()
    for fields, total in groups:
        if any(len(fields.get(k, ())) != 1 for k in ("net", "rate", "gross")):
            continue
        net, rate, gross = (fields[k][0] for k in ("net", "rate", "gross"))
        if net[1] and gross[1] and net[1] != gross[1]:
            continue
        identity = tuple((v, n.file_seq, n.loc) for v, _, n in (net, rate, gross))
        if identity in seen:
            continue
        seen.add(identity)
        with localcontext() as ctx:
            ctx.prec = max(34, len(net[0].as_tuple().digits) + len(rate[0].as_tuple().digits) + 4)
            expected = rounded(net[0] * (1 + rate[0]))
        yield (net, rate, gross, expected), total


def _motor_totals(text):
    """同一合计格明确并列含 / 不含电机价格时，保留两种口径。"""
    found = [(Decimal(m[1].replace(',', '')), m[2]) for m in _MOTOR_SCOPE.finditer(text)]
    return found if {scope for _, scope in found} == {'含电机', '不含电机'} else []


def _column_index(col):
    return int(col) if col.isdecimal() else column_index_from_string(col)


def labelled_totals(nodes):
    """[(值, 币种, node)]：总价标签、报价页的服务费，支持表头同列与同页相邻节点。

    宽泛标签仅用于有报价标题的页（无页码的 Word 用最近标题）；优先小写，无小写时取大写。
    不把历史合同中的金额或不含税价当作本次报价，多个报价仍保留供人工核对。
    """
    out, column_hits, summarized = [], set(), set()
    cells = {}
    quote_pages = set()
    # 扫描件的页眉（“××项目计量泵商务报价单”每页都有）不能让每一页都算报价页（V34-B-08c）
    heads = Counter()
    for n in nodes:
        if getattr(n, 'page', None) is not None and len(n.text) < 160:
            heads[(n.file_seq, _STRIP.sub('', n.text))] += 1
    sub_list = set()  # 备品备件、专用工具等分项清单所在页：表中“合计”是清单小计，不是投标总价
    for n in nodes:
        cell = table_cell(n.loc)
        if cell:
            cells[(n.file_seq, *cell)] = n
        if getattr(n, 'page', None) is not None and _SUB_LIST.search(n.text):
            sub_list.add((n.file_seq, n.page))
        if (len(n.text) < 160 and _QUOTE_HEADING.search(n.text) and not re.search(r"\.{3}|…|目录|分项|备品备件", n.text)
                and heads[(n.file_seq, _STRIP.sub('', n.text))] < 3):
            quote_pages.add((n.file_seq, getattr(n, 'page', None)))

    def amount(text, label, bare=False):
        text = unicodedata.normalize('NFKC', text)
        # 前置“人民币”被 OCR 拆开时恢复；不连接不同数字。
        text = re.sub(r'人\s*民\s*币', '人民币', text)
        text = re.sub(r'(?<=[零壹贰叁肆伍陆柒捌玖拾佰仟万亿])[_\s]+(?=[零壹贰叁肆伍陆柒捌玖拾佰仟万亿元圆整])', '', text)
        inherited_wan = '万元' in label and not re.search(r'\d|[壹贰叁肆伍陆柒捌玖]', label)
        match_currency = _CURRENCY_TOKEN.search(label)
        inherited_currency = _CURRENCY.get(match_currency.group().upper()) if match_currency else None
        hits = find_amounts(text)
        lower = [h for h in hits if h.value is not None and not _UPPER.search(text[h.start:h.end])]
        found = lower or [h for h in hits if h.value is not None]
        if found:
            h = found[0]
            # 单位只写在表头时继承；数值自身有单位时以自身为准。
            scale = 10000 if inherited_wan and h.currency is None and not re.search(r'万?元|万圆', text) else 1
            return h.value * scale, h.currency or inherited_currency
        match = _BARE_MONEY.fullmatch(text) if bare else None
        if match:
            scale = 10000 if '万' in (match[2] or '') or inherited_wan else 1
            unit = match[2]
            return Decimal(match[1].replace(',', '')) * scale, _CURRENCY.get(unit, inherited_currency)
        return None

    section = None
    for i, n in enumerate(nodes):
        text = unicodedata.normalize('NFKC', n.text)
        if i == 0 or nodes[i - 1].file_seq != n.file_seq:
            section = None
        if getattr(n, 'kind', '') == 'heading':
            section = bool(_QUOTE_HEADING.search(text))
        in_quote = (n.file_seq, getattr(n, 'page', None)) in quote_pages if getattr(n, 'page', None) is not None else section
        if re.match(r'^\s*(?:投标)?总价\s*[:：]', text) and (scoped := _motor_totals(text)):
            out.extend((v, 'CNY', n) for v, _scope in scoped)
            if cell := table_cell(n.loc):
                summarized.add((n.file_seq, cell[0]))
            continue
        matches = list(_TOTAL_LABEL.finditer(text))
        if in_quote:
            matches += [m for m in _SERVICE_LABEL.finditer(text)
                        if not any(s.start() <= m.start() < s.end() for s in matches)]
        for m in sorted(matches, key=lambda m: m.start()):
            if _NOT_TOTAL.search(text[max(0, m.start() - 6):m.end()]):
                continue
            if m.group() == '合计' and (text[:m.start()].strip(' :()') or (n.file_seq, getattr(n, 'page', None)) in sub_list):
                continue
            tail = text[m.end():]
            # “报价含…税费”“总价的百分比”等说明不作为总价标签。
            if re.match(r'\s*(?:含配套|有效|的|=)', tail):
                continue
            tail = re.split(r'不含税|保证金|最高限价', tail, maxsplit=1)[0]
            unit_label = re.match(r"\s*\(([^)]{1,20})\)", tail)
            label = text[:m.end()] + (unit_label[1] if unit_label else "")
            value_text = tail[unit_label.end():] if unit_label and (
                _CURRENCY_TOKEN.search(unit_label[1]) or unit_label[1] in ("万元", "小写")
            ) else tail
            found = amount(value_text[:100], label, bare=True)
            target = n
            cell = table_cell(n.loc)
            if found is None and cell and not _CURRENCY_TOKEN.sub("", value_text).strip(' :()万元人民币含税小写'):
                table, col, row = cell
                # 单独标签格优先同行右侧；列标题则取下一行同列。
                current_col = _column_index(col)
                right = [(_column_index(k[2]), v) for k, v in cells.items()
                         if k[0] == n.file_seq and k[1] == table and k[3] == row and _column_index(k[2]) > current_col]
                candidates = [min(right, key=lambda pair: pair[0])[1]] if right else []
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
                    found = amount((value_text + '\n' + nxt.text)[:140], label, bare=True)
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
    """只有明确标题或含电机说明能区分报价口径时，才不按重复总价处理；不猜测合计。"""
    def scope(text):
        label = re.search(r'表[一二三四五六七八九十](?=[：:、\s）)]|$)', text)
        if label:
            return label[0]
        if re.search(r'泵头|泵体', text):
            return '泵头'
        if '电机' in text:
            return '电机'
        if found := re.search(r'主机|辅机', text):
            return found[0]
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
        inline = next((scope for v, scope in _motor_totals(n.text) if rounded(v) == rounded(value)), None)
        found = inline or (own[0] if own else next(iter(labels)) if len(labels) == 1 else at.get(id(n)) if getattr(n, 'page', None) is None else None)
        by_value.setdefault(rounded(value), set())
        if found:
            by_value[rounded(value)].add(found)
    values = list(by_value.values())
    return len(values) > 1 and all(values) and all(not (a & b) for a, b in combinations(values, 2))


def _component_table(table):
    """明确分列组成单价且共用数量、总价的表，按组成单价之和复算。

    只建临时计算副本，不改原始节点或普通审核的金额引擎；多种税价等含混列不猜测相加。
    """
    labels = {col: re.sub(r'\s+', '', n.text) for col, n in table.rows[table.header].items()}
    components = {re.sub(r'单价[（(]元[）)]$', '单价', t): c for c, t in labels.items() if '单价' in t}
    match = next((pair for pair in (('泵头单价', '电机单价'), ('泵体单价', '电机单价'), ('主机单价', '辅机单价'))
                  if components.keys() == set(pair)), None)
    if not match or not re.fullmatch(r'总价(?:[（(]元[）)])?', labels.get(table.columns[table.amount], '')):
        return table
    if len([t for t in labels.values() if '单价' in t]) != 2:
        return table
    rows = {r: dict(cells) for r, cells in table.rows.items()}
    for row in table.data_rows:
        parts = [parse_number(rows[row][c].text) if c in rows[row] else None for c in (components[k] for k in match)]
        # 缺少任一部分时留空，让既有引擎按无法核验处理。
        node = rows[row].get(table.columns['price'])
        if node:
            rows[row][table.columns['price']] = replace(node, text=str(sum(parts, Decimal(0))) if all(v is not None for v in parts) else '')
    return replace(table, rows=rows)


def quote_tables(nodes):
    """已识别的报价表：[(表, 检查结果, 分项 [(键, 名称, 单价, 金额, node)])]；以及 L-01、L-02 结果。"""
    out, results = [], []
    with localcontext() as ctx:
        ctx.prec = max(28, sum(len(n.text) for n in nodes) + 8)
        for table in _scan_tables(nodes):
            if not table.recognized:
                continue
            calculation = _component_table(table)
            info, found = _check_table(calculation)
            if calculation is not table:
                for result in found:
                    result.detail = result.detail.replace('× 单价', '× 各组成单价之和')
            results += [r for r in found if (r.unsure or r.code) in ("L-01", "L-02")]  # 含扫描识读降级的（V34-D-02）
            col = _name_col(table)
            items = []
            for row in table.data_rows:
                name = table.rows[row][col].text.strip() if col is not None and col in table.rows[row] else ""
                price, amount = parse_number(calculation.text(row, "price")), parse_number(table.text(row, table.amount))
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


def _fractional_tail_pattern(pairs, fixed, tender_prices):
    """在唯一品名的对齐分项上比较真实单价的小数尾数，排除给定价及常规取整。"""
    counts = Counter(x[0] for x, _ in pairs)
    eligible, matched = 0, []
    for x, y in pairs:
        name, a, b = x[0], x[2], y[2]
        if (counts[name] != 1 or name in fixed or a is None or b is None or min(a, b) <= 0
                or any(it[4].ocr_conf is not None or parse_number(it[4].text) != it[2] for it in (x, y))
                or {a, b} & tender_prices.get(name, set())):
            continue
        eligible += 1
        tail = (a % 1) * 100
        if (a != b and a == rounded(a) and b == rounded(b) and tail == (b % 1) * 100
                and tail % 5 != 0 and tail != 99):
            matched.append((x, y))
    return matched if len(matched) >= MIN_ITEMS and len(matched) * 5 >= eligible * 4 else []


def _pct(x, signed=False):
    q = (x * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{'+' if signed and q > 0 else ''}{q}%"


def _trim(x):
    return f"{x:f}".rstrip("0").rstrip(".") if "." in f"{x:f}" else f"{x:f}"


def compare(totals, *, rate=False):
    """各家总价比较。totals 为 {投标人: Decimal}（只含参加比较的，按录入顺序）。
    返回 (汇总或 None, {投标人: 字段})；字段：mark 最高 / 最低、above_lowest(_pct) 比最低价高、vs_mean 偏离平均、
    low_price 异常低价（低于其余各家平均 LOW_PRICE 以上，其余至少 2 家）。"""
    if len(totals) < 2:
        return None, {}
    with localcontext() as ctx:
        ctx.prec = 34
        vals = totals if rate else {b: rounded(v) for b, v in totals.items()}
        hi, lo = max(vals.values()), min(vals.values())
        mean = sum(vals.values(), Decimal(0)) / len(vals)
        display = rate_text if rate else money
        out = {}
        for b, v in vals.items():
            diff = (v - mean).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if rate else None
            row = {"mark": None if hi == lo else "highest" if v == hi else "lowest" if v == lo else None,
                   "above_lowest": (f"+{_trim(v - lo)} 个百分点" if rate else money(v - lo)) if v != lo else None,
                   "above_lowest_pct": _pct((v - lo) / lo) if not rate and v != lo and lo > 0 else None,
                   "vs_mean": f"{'+' if diff >= 0 else '−'}{abs(diff):.1f} 个百分点" if diff is not None else
                   _pct((v - mean) / mean, signed=True) if mean > 0 else None, "low_price": None}
            others = [w for o, w in vals.items() if o != b]
            if not rate and len(others) >= 2:
                other_mean = sum(others, Decimal(0)) / len(others)
                if other_mean > 0 and v < other_mean * (1 - LOW_PRICE):
                    row["low_price"] = {"others": len(others), "others_mean": money(other_mean),
                                        "below_pct": _pct((other_mean - v) / other_mean)}
            out[b] = row
        summary = {"n": len(vals), "highest": display(hi), "lowest": display(lo),
                   "mean": display(mean.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP) if rate else mean),
                   "spread_pct": _pct((hi - lo) / lo) if not rate and lo > 0 else None}
    return summary, out


def total_patterns(totals, *, rate=False):
    """三家及以上总价从低到高呈等差或等比（第四十条（四）“投标报价呈规律性差异”）。
    返回 [(投标人按金额升序, "diff" | "ratio", 显示用的差额或比值)]；已报的一组的子集不再重复报。"""
    ordered = sorted(((v if rate else rounded(v), b) for b, v in totals.items()), key=lambda x: x[0])
    found = []
    with localcontext() as ctx:
        ctx.prec = 34
        for size in range(len(ordered), 2, -1):
            for combo in combinations(ordered, size):
                ids = [b for _v, b in combo]
                if any(set(ids) <= set(f[0]) for f in found):
                    continue
                vals = [v for v, _b in combo]
                if (not rate and vals[0] <= 0) or any(x == y for x, y in zip(vals, vals[1:])):
                    continue
                diffs = [y - x for x, y in zip(vals, vals[1:])]
                if max(diffs) - min(diffs) <= (0 if rate else TOTAL_DIFF_TOL):
                    step = sum(diffs, Decimal(0)) / len(diffs)
                    found.append((ids, "diff", f"{_trim(step)} 个百分点" if rate else money(step)))
                elif vals[0] > 0:
                    ratios = [y / x for x, y in zip(vals, vals[1:])]
                    if max(ratios) - min(ratios) <= TOTAL_RATIO_TOL:
                        k = (sum(ratios, Decimal(0)) / len(ratios)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                        found.append((ids, "ratio", _trim(k)))
    return found


_QUOTE_PAGE = re.compile(r"开标一览表|投标函|报价|大写")


def quote_pages(nodes, limit=10):
    """“找报价页”的候选页：含开标一览表、投标函、报价、大写字样的页，按命中次数从多到少，最多 limit 页。"""
    hits = {}
    for n in nodes:
        page = getattr(n, "page", None)
        if page is None:
            continue
        count = len(_QUOTE_PAGE.findall(n.text))
        if count:
            key = (str(getattr(n, "file_id", None)), page)
            hits[key] = hits.get(key, 0) + count
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))[:limit]
    return [{"file_id": f, "page": p, "hits": c} for (f, p), c in ranked]


def _source(nodes, totals, distinct, main, *, rate=False):
    """唯一总价的出处：所在页的报价标题（开标一览表、投标函等）与文件、页码。"""
    node, label = None, None
    if len(distinct) == 1:
        node = next((n for v, _c, n in totals if (v if rate else rounded(v)) == distinct[0]), None)
    elif not distinct and main is not None and main[2]:
        node, label = main[2][0][4], "报价表"
    if node is None:
        return None
    page = getattr(node, "page", None)
    if label is None:
        same = [n.text for n in nodes if getattr(n, "file_id", None) == getattr(node, "file_id", None) and getattr(n, "page", None) == page and len(n.text) < 160] if page is not None else []
        found = next((m.group() for t in [node.text, *same] if (m := _QUOTE_HEADING.search(t))), None)
        label = found or "投标文件"
    file_id = getattr(node, "file_id", None)
    return {"label": label, "file_id": str(file_id) if file_id else None, "page": page}


def _tax_totals(totals, pairs, tax_included, table_total):
    """只替换能够唯一对应的两种税价；独立报价仍进入多个总价检查。"""
    replacements = {}
    for net, _rate, gross, _expected in pairs:
        chosen = net if tax_included is False else gross
        paired_currency = net[1] or gross[1]
        chosen = (chosen[0], paired_currency, chosen[2])
        for amount, _currency, node in (net, gross):
            replacements.setdefault((rounded(amount), paired_currency), {})[rounded(chosen[0])] = chosen
    out = []
    for amount, currency, node in totals:
        choices = {v: candidate for (price, c), options in replacements.items()
                   if price == rounded(amount) and (not c or not currency or c == currency)
                   for v, candidate in options.items()}
        out.append(next(iter(choices.values())) if len(choices) == 1 else (amount, currency, node))
    if not totals and len(pairs) == 1:
        net, _rate, gross, _expected = pairs[0]
        if table_total is None or rounded(table_total) in (rounded(net[0]), rounded(gross[0])):
            amount, currency, node = net if tax_included is False else gross
            out.append((amount, currency or net[1] or gross[1], node))
    return out


def analyze(bidders, per_bidder_nodes, ceiling=None, currency=None, place=None, manual=None, *,
            mode="fixed", rate_limit=None, tax_included=None, tender_nodes=()):
    """返回 (报价分析 JSON, 线索, 每家 {总价, 币种, 多个总价, 表, L 结果, 异常低价})。bidders 为按录入顺序的 id；
    place(node) 给出证据位置（总价规律线索用，可省）。"""
    rate = mode in RATE_LABELS
    label = RATE_LABELS.get(mode, "投标总价")
    display = rate_text if rate else money
    facts, analysis = {}, {"bidders": {}, "items": [], "pairs": [], "aligned_by": {}, "compare": None, "total_patterns": [],
                           "quote_mode": mode, "quote_label": label,
                           "rate_limit": rate_text(rate_limit) if rate_limit is not None else None,
                           "rate_limit_label": RATE_LIMIT_LABELS.get(mode)}
    for b in bidders:
        nodes = per_bidder_nodes.get(b, [])
        tables, results = ([], []) if rate else quote_tables(nodes)
        main = _main(tables)
        table_total = None
        if main is not None:
            info, items = main[1], main[2]
            table_total = info.total if info.total is not None else (
                sum((rounded(it[3]) for it in items), Decimal(0)) if items and all(it[3] is not None for it in items) else None)
        tax_found = [] if rate else list(_tax_quotes(nodes))
        tax_pairs = [pair for pair, _total in tax_found]
        tax_totals = [pair for pair, is_total in tax_found if is_total]
        totals = labelled_rates(nodes, mode) if rate else _tax_totals(labelled_totals(nodes), tax_totals, tax_included, table_total)
        distinct = sorted({v if rate else rounded(v) for v, _c, _n in totals})
        separate_scopes = not rate and separate_quote_scopes(nodes, totals)
        total = distinct[0] if len(distinct) == 1 else None
        currency_found = next((c for _v, c, _n in totals if c), None)
        if not distinct and table_total is not None:
            currency_found = main[1].currency
        facts[b] = {"total": total if total is not None else (table_total if not distinct else None),
                    "currency": currency_found, "totals": totals, "distinct": distinct, "main": main,
                    "table_total": table_total, "results": results, "separate_scopes": separate_scopes,
                    "tax_pairs": tax_pairs, "tax_totals": tax_totals}
        source = _source(nodes, totals, distinct, main, rate=rate)
        entered = (manual or {}).get(str(b)) if not rate else None
        if entered is not None:
            facts[b]["file_total"] = facts[b]["total"]
            facts[b]["file_currency"] = currency_found
            facts[b]["manual_total"] = Decimal(entered["amount"])
            facts[b]["manual_upper"] = entered.get("upper")
            facts[b]["total"] = facts[b]["manual_total"]
            # 唱标价不带币种：按关键参数的币种；参数也未填时保留未知（V34Q-50 后续）
            facts[b]["currency"] = currency_found = currency
        row = {"total": display(facts[b]["total"]) if facts[b]["total"] is not None else None,
               "totals_found": [display(v) for v in distinct], "currency": currency_found,
               "table": None, "over_ceiling": None, "manual_total": entered is not None,
               "source": source, "quote_pages": quote_pages(nodes)}
        if entered is not None:
            row["opening"] = {"amount": money(Decimal(entered["amount"])), "upper": entered.get("upper"), "note": entered.get("note")}
            if facts[b]["file_total"] is not None:
                row["file_total"] = money(facts[b]["file_total"])
        if main is not None:
            row["table"] = {"name": main[0].name, "rows": len(main[2]),
                            "total": money(table_total) if table_total is not None else None}
        if tax_pairs:
            row["tax_pairs"] = [{"net": money(net[0]), "rate": rate_text(tax[0] * 100), "gross": money(gross[0]),
                                 "expected": money(expected), "ok": abs(rounded(gross[0]) - expected) <= CENT}
                                for net, tax, gross, expected in tax_pairs]
            if entered is None and total is not None and any(
                total == rounded((net if tax_included is False else gross)[0]) for net, _, gross, _ in tax_totals
            ):
                row["tax_basis"] = "不含税" if tax_included is False else "含税"
        if rate and distinct and rate_limit is not None:
            bound = min(distinct) if mode == "discount" else max(distinct)
            delta = rate_limit - bound if mode == "discount" else bound - rate_limit
            if delta > 0:
                reason = "低于" if mode == "discount" else "超"
                row["ceiling_reason"] = f"{reason}{RATE_LIMIT_LABELS[mode]} {_trim(delta)} 个百分点"
        if ceiling is not None and facts[b]["total"] is not None and (currency_found in (None, currency)):
            row["over_ceiling"] = money(facts[b]["total"] - ceiling)
        elif ceiling is not None and len(distinct) > 1 and not separate_scopes and (currency_found in (None, currency)):
            # 多个不同总价：按最高者与限价比较（V34Q-25 第 4 条），over_ceiling 仍只表示唯一总价
            row["over_ceiling_max"] = money(distinct[-1] - ceiling)
        analysis["bidders"][str(b)] = row
        delta = row.get("over_ceiling_max") or row["over_ceiling"]
        if delta and ceiling and (difference := Decimal(delta.replace(",", ""))) > 0:
            percent = (difference * 100 / ceiling).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            row["ceiling_reason"] = f"超最高限价 +{delta}（+{percent}%）"
    # 总价比较：只有唯一总价、币种与关键参数一致（或未写币种）的参加；多个总价的不参加
    eligible = {}
    for b in bidders:
        f, row = facts[b], analysis["bidders"][str(b)]
        row["not_compared"] = None
        if len(f["distinct"]) > 1 and f.get("manual_total") is None:
            row["not_compared"] = ("不同范围报价，未参加比较" if f['separate_scopes'] else
                                   f"多个{label if rate else '总价'}，未参加比较")
        elif f["total"] is not None and f["currency"] not in (None, currency or "CNY"):
            row["not_compared"] = f"币种为 {f['currency']}，未参加比较"
        elif f["total"] is not None:
            eligible[b] = f["total"]
    summary, fields = compare(eligible, rate=rate)
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
    tender_prices = None
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
        if how == "name" and facts[a]["currency"] in (None, currency or "CNY") and facts[b]["currency"] in (None, currency or "CNY"):
            unique_b = Counter(it[0] for it in mb[2])
            named_pairs = [(x, y) for x, y in pairs if unique_b[y[0]] == 1]
            matched = _fractional_tail_pattern(named_pairs, fixed, tender_prices or {})
            if matched and tender_prices is None:
                tender_prices = {}
                for _, _, entries in quote_tables(tender_nodes)[0]:
                    for key, _, price, _, _ in entries:
                        if key and price is not None:
                            tender_prices.setdefault(key, set()).add(price)
                matched = _fractional_tail_pattern(named_pairs, fixed, tender_prices)
            if matched:
                label = "分项单价小数尾数共现"
                rows = [{"label": label, "value": f"{len(matched)} 项不同单价的小数尾数按品名对齐后相同",
                         "items": [{"name": x[1], "left": money(x[2]), "right": money(y[2])} for x, y in matched[:12]],
                         "places": {str(a): [place(x[4]) for x, _ in matched[:5]],
                                    str(b): [place(y[4]) for _, y in matched[:5]]} if place else {}}]
                clues.append({"ids": (a, b), "level": "medium", "title": label, "rows": rows})
                if not found:
                    stat |= {"level": "medium", "label": label}
        analysis["pairs"].append(stat)
    # 总价规律（三家及以上）：线索按录入顺序列投标人，证据按金额从低到高
    order = {b: i for i, b in enumerate(bidders)}
    for ids, how, step in total_patterns(eligible, rate=rate):
        title = (f"{len(ids)} 家{label}呈等差（从低到高每家相差 {step}）" if how == "diff"
                 else f"{len(ids)} 家{label}呈等比（从低到高每家约为前一家的 {step} 倍）")
        places = {}
        for b in ids:
            f = facts[b]
            node = None
            if f.get("manual_total") is None:
                node = f["totals"][0][2] if f["totals"] else (f["main"][2][0][4] if f["main"] and f["main"][2] else None)
            places[str(b)] = [place(node)] if place and node is not None else []
        rows = [{"label": f"{label}（从低到高）", "value": "、".join(display(eligible[b]) for b in ids), "places": places}]
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
