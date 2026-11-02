"""L-05、L-06 及 L-17～19：金额与跨文件一致性；只在内存比较真实值。"""
from decimal import Decimal
import re
from itertools import combinations

from app.checks.amounts import _TOTAL, QuoteTableCheck, _Table, _scan_tables, _currency
from app.checks.model import CheckFile, CheckNode, CheckResult, SEVERITY
from app.checks.numbers import CENT, money, parse_number, pct_diff, rounded
from app.redaction.detect import find_amounts, find_formatted
from app.redaction import parties as P
from app.redaction.placeholders import BUYER, SELLER
from app.redaction.ratio import AmountAt, TOTAL_LABEL, contract_total


def contract_amount(nodes: list[CheckNode], files: list[CheckFile]) -> tuple[str, str, Decimal] | None:
    """(节点位置、币种、金额)；只把主文件的节点交给既有总价识别函数。"""
    main = next((f for f in files if f.is_main), None)
    if main is None:
        return None
    body = [n for n in nodes if n.file_seq == main.seq]
    amounts = [AmountAt(i, h) for i, n in enumerate(body) for h in find_amounts(n.text)]
    total = contract_total(body, amounts, f"F{main.seq}-")
    if total is None:
        return None
    candidates = [a for a in amounts if (a.hit.currency, a.hit.value) == total]
    labelled = [a for a in candidates if any(0 <= a.hit.start - m.end() <= 30
                                            for m in TOTAL_LABEL.finditer(body[a.node].text))]
    hit = (labelled or candidates)[0]
    return body[hit.node].loc, total[0], total[1]


def _single_currency(nodes: list[CheckNode]) -> bool:
    """整个任务中所有带币种的金额只有一种币种。"""
    return len({h.currency for n in nodes for h in find_amounts(n.text) if h.currency}) == 1


def _total_row_matches(layout: _Table, value: Decimal) -> bool:
    """表头之下任一合计行（某个单元格含合计 / 总计 / Total）有数值等于合同总价时视为一致（R1-10）：
    分段报价表的第一个合计行可能只是分项合计（如“硬件总计”），总价在后面的“报价合计”行。"""
    for row in sorted(r for r in layout.rows if r > layout.header):
        cells = list(layout.rows[row].values())
        if any(_TOTAL.search(n.text) for n in cells) and any(
                (x := parse_number(n.text)) is not None and abs(rounded(x) - rounded(value)) <= CENT for n in cells):
            return True
    return False


def compared_tables(nodes: list[CheckNode], files: list[CheckFile], tables: list[QuoteTableCheck]) -> list[QuoteTableCheck]:
    """参与 L-05、L-06 比较的报价表：合计行金额可解析；主文件自身的表也参与（R1-09）。
    表格没有币种时，只有任务中带币种的金额仅有一种币种才参与（按合同币种比较）。"""
    total = contract_amount(nodes, files)
    if total is None:
        return []
    layouts = {t.name: t for t in _scan_tables(nodes)}
    single = _single_currency(nodes)
    out = []
    for table in tables:
        if not table.recognized or table.total is None or (table.currency is None and not single):
            continue
        layout = layouts.get(table.table)
        if layout is not None and layout.total_row is not None and layout.node(layout.total_row, layout.amount) is not None:
            out.append(table)
    return out


def check_consistency(nodes: list[CheckNode], files: list[CheckFile],
                      tables: list[QuoteTableCheck], aliases=()) -> list[CheckResult]:
    results = check_cross_files(nodes, files, aliases)
    total = contract_amount(nodes, files)
    if total is None:
        return results
    location, currency, value = total
    layouts = {t.name: t for t in _scan_tables(nodes)}
    for table in compared_tables(nodes, files, tables):
        layout = layouts[table.table]
        locs = [location, layout.node(layout.total_row, layout.amount).loc]
        if table.currency is not None and table.currency != currency:
            text = f"主文件合同总价币种为 {currency}，报价表 {table.table} 币种为 {table.currency}"
            results.append(CheckResult("L-06", SEVERITY["L-06"], locs, table.file_seq, text, text))
        elif abs(rounded(value) - rounded(table.total)) > CENT and not _total_row_matches(layout, value):
            detail = (f"主文件合同总价 {currency} {money(value)}，报价表 {table.table} 合计 {money(table.total)}，"
                      f"相差 {money(abs(rounded(value) - rounded(table.total)))}")
            if table.currency is None:
                detail += "（报价表未注明币种，按合同币种比较）"
            results.append(CheckResult("L-05", SEVERITY["L-05"], locs, table.file_seq, detail,
                                       f"主文件合同总价与报价表 {table.table} 合计不一致，差异约 {pct_diff(table.total, value)}"))
    return results


_ACCOUNT_LABEL = re.compile(
    r'(付款账号|付款帐号|付款账户|付款方账号|payer\s+account|收款账号|开户行账号|银行账号|账号|帐号|账户|帐户|'
    r'account\s*(?:no\.?|number)?|a/c(?:\s*no\.?)?|acct\.?|iban(?:\s*(?:no\.?|number))?)'
    r'\s*[:：]?\s*[【［\[〖]?\s*$', re.I)
_CONTRACT_LABEL = re.compile(r'(?:合同编号|合同号|contract\s*(?:no\.?|number))\s*[:：]?\s*$', re.I)


def _labelled_nodes(nodes):
    """紧邻的标签格 / 标签段与值合读，报告定位到值；不跨文件。"""
    for i, node in enumerate(nodes):
        text = node.text
        if i and nodes[i - 1].file_seq == node.file_seq:
            label = nodes[i - 1].text.strip().rstrip(':：')
            if (_ACCOUNT_LABEL.fullmatch(label) or _CONTRACT_LABEL.fullmatch(label)
                    or P._LABEL_RE.fullmatch(label + ':')):
                text = label + ': ' + text
        yield node, text


def check_cross_files(nodes: list[CheckNode], files: list[CheckFile], aliases=()) -> list[CheckResult]:
    """L-17～19：只比较明确标注的值，不把没有标签的数字猜成账号或合同号。"""
    if len(files) < 2:
        return []
    results = []

    def report(code, a, b, label, values, *, severity=None, relative=''):
        summary = f'{a.loc} 与 {b.loc} 的{label}不一致{relative}'
        results.append(CheckResult(code, severity or SEVERITY[code], list(dict.fromkeys([a.loc, b.loc])),
                                   b.file_seq, summary + '：' + ' / '.join(values), summary))

    parents = {}

    def root(value):
        value = P.normalize(value)
        while value in parents and parents[value] != value:
            value = parents[value]
        return value

    def link(a, b):
        a, b = root(a), root(b)
        if a != b:
            parents[b] = a

    for a, b in aliases:
        link(a, b)
    for node in nodes:
        names = P.find_labeled_parties(node.text)
        spans = [(a, b) for _, a, b in names] + P.find_other_org_names(node.text)
        for structure, start, end in P.find_aliases(node.text):
            prior = [(a, b) for a, b in spans if b <= structure and not node.text[b:structure].strip()]
            if prior:
                a, b = max(prior, key=lambda span: span[1])
                link(node.text[a:b], node.text[start:end])

    accounts, roles, contracts = [], {}, []
    for node, text in _labelled_nodes(nodes):
        labelled = P.bilingual_label_text(text)
        seen = set()
        for hit in find_formatted(labelled):
            label = _ACCOUNT_LABEL.search(labelled[:hit.start]) if hit.category == 'ACCOUNT' else None
            if label and not re.search(r'付款|payer', label[1], re.I) and hit.key not in seen:
                seen.add(hit.key)
                accounts.append((node, hit.key, text[hit.start:hit.end]))
            if hit.category == 'REFNO' and _CONTRACT_LABEL.search(labelled[:hit.start]):
                contracts.append((node, hit.key, text[hit.start:hit.end]))
        found = P.find_labeled_parties(text)
        # 已知简称可能没有企业后缀，只在明确角色标签后整串命中已知别名时使用。
        for m in P._LABEL_RE.finditer(P.bilingual_label_text(text)):
            role = P._LABEL_CANON[m[1].casefold()]
            candidate = re.split(r'[\n;；]', text[m.end():], 1)[0].strip()
            if role in (BUYER, SELLER) and P.normalize(candidate) in parents:
                start = m.end() + len(text[m.end():]) - len(text[m.end():].lstrip())
                found.append((role, start, start + len(candidate)))
        for role, start, end in found:
            if role in (BUYER, SELLER):
                roles.setdefault(role, []).append((node, root(text[start:end]), text[start:end]))

    for a, b in combinations(accounts, 2):
        if a[1] != b[1]:
            report('L-17', a[0], b[0], '收款账号', [a[2], b[2]],
                   severity='medium' if a[0].file_seq == b[0].file_seq else 'high')
    for role, entries in roles.items():
        for a, b in combinations(entries, 2):
            if a[0].file_seq != b[0].file_seq and a[1] != b[1]:
                report('L-18', a[0], b[0], f'{role}名称', [a[2], b[2]])
    for a, b in combinations(contracts, 2):
        if a[0].file_seq != b[0].file_seq and a[1] != b[1]:
            report('L-19', a[0], b[0], '合同号', [a[2], b[2]])

    products = {}
    for table in _scan_tables(nodes):
        label_cols = [c for c, n in table.rows[table.header].items()
                      if re.fullmatch(r'品名|产品名称|货物名称|商品名称|项目名称|名称|项目|(?i:item|description|product(?:\s+name)?)', n.text.strip())]
        if len(label_cols) != 1:
            continue
        currency = _currency(table)
        for row in table.data_rows:
            name = table.rows[row].get(label_cols[0])
            if name is None or not name.text.strip():
                continue
            for kind, label in (('price', '单价'), ('qty', '数量')):
                cell = table.node(row, kind)
                value = parse_number(cell.text) if cell else None
                if value is not None:
                    # 单价币种不明时不猜，数量不依赖币种。
                    if kind == 'price' and currency is None:
                        continue
                    key = (P.normalize(name.text), kind, currency if kind == 'price' else None)
                    products.setdefault(key, []).append((cell, value, label))
    for entries in products.values():
        for a, b in combinations(entries, 2):
            if a[0].file_seq != b[0].file_seq and a[1] != b[1]:
                report('L-19', a[0], b[0], f'同一品名的{a[2]}', [a[0].text, b[0].text],
                       relative=f'，差异约 {pct_diff(b[1], a[1])}')
    # 同一位置的重复标签或命中不重复列出。
    return list({(r.code, tuple(r.locs), r.ai_summary): r for r in results}.values())
