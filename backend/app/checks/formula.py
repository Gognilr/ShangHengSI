"""有限的 Excel 四则运算求值；不执行公式代码，不访问外部工作簿。"""
import re
from decimal import Decimal, DecimalException, localcontext

from app.checks.model import CheckResult
from app.checks.numbers import parse_number, pct_diff

_REF = r"(?:(?:'(?:[^']|'')+'|[^\W\d]\w*)!)?\$?[A-Za-z]{1,3}\$?\d+"
_TOKEN = re.compile(rf"\s*({_REF}|\d+(?:\.\d*)?|\.\d+|SUM\b|[()+*/,:-])", re.I)


class UnsupportedFormula(ValueError):
    pass


def evaluate_formula(formula, sheet, cells, *, stack=()):
    """cells[(sheet, A1)] = (Decimal 缓存值或 None, 公式或 None)。空格引用不猜成零。"""
    if len(stack) >= 50:
        raise UnsupportedFormula()
    tokens, pos = [], 0
    source = formula.lstrip('=')
    while pos < len(source.rstrip()):
        match = _TOKEN.match(source, pos)
        if not match:
            raise UnsupportedFormula()
        tokens.append(match[1]); pos = match.end()
    i = 0

    def reference(token):
        if not re.fullmatch(_REF, token):
            raise UnsupportedFormula()
        if '!' in token:
            target, ref = token.rsplit('!', 1)
            target = target[1:-1].replace("''", "'") if target.startswith("'") else target
        else:
            target, ref = sheet, token
        return target, ref.replace('$', '').upper()

    def value(token):
        key = reference(token)
        if key in stack or key not in cells:
            raise UnsupportedFormula()
        cached, nested = cells[key]
        if nested:
            return evaluate_formula(nested, key[0], cells, stack=(*stack, key))
        if cached is None:
            raise UnsupportedFormula()
        return cached

    def take(expected=None):
        nonlocal i
        if i >= len(tokens) or (expected is not None and tokens[i].upper() != expected):
            raise UnsupportedFormula()
        token = tokens[i]; i += 1
        return token

    def atom():
        token = take()
        if token in ('-', '+'):
            return (-1 if token == '-' else 1) * atom()
        if token == '(':
            result = expression(); take(')'); return result
        if token.upper() == 'SUM':
            take('('); total = Decimal(0)
            while True:
                if i + 1 < len(tokens) and tokens[i + 1] == ':':
                    start = reference(take()); take(':'); end_token = take()
                    end = reference(end_token) if '!' in end_token else (start[0], reference(end_token)[1])
                    if start[0] != end[0]:
                        raise UnsupportedFormula()
                    from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter
                    a, b = coordinate_to_tuple(start[1]), coordinate_to_tuple(end[1])
                    if a[0] > b[0] or a[1] > b[1]:
                        raise UnsupportedFormula()
                    for row in range(a[0], b[0] + 1):
                        for col in range(a[1], b[1] + 1):
                            escaped_sheet = start[0].replace("'", "''")
                            total += value(f"'{escaped_sheet}'!{get_column_letter(col)}{row}")
                else:
                    total += expression()
                if i >= len(tokens) or tokens[i] != ',':
                    break
                take(',')
            take(')'); return total
        if re.fullmatch(r'\d+(?:\.\d*)?|\.\d+', token):
            return Decimal(token)
        return value(token)

    def product():
        result = atom()
        while i < len(tokens) and tokens[i] in ('*', '/'):
            op = take(); other = atom()
            result = result * other if op == '*' else result / other
        return result

    def expression():
        result = product()
        while i < len(tokens) and tokens[i] in ('+', '-'):
            op = take(); other = product()
            result = result + other if op == '+' else result - other
        return result

    try:
        with localcontext() as ctx:
            ctx.prec = 50
            result = expression()
        if i != len(tokens) or not result.is_finite():
            raise UnsupportedFormula()
        return result
    except (DecimalException, RecursionError):
        raise UnsupportedFormula() from None


def numeric(text):
    """财务括号负数和百分比；普通校验原有口径不变。"""
    from app.checks.numbers import _CURRENCIES
    import unicodedata
    text = unicodedata.normalize("NFKC", text)
    text = _CURRENCIES.sub("", text).strip()
    if not re.fullmatch(r"[-+()\d,.%\s]+", text):
        return None
    text = text.strip().replace('（', '(').replace('）', ')')
    if text.startswith('(') and text.endswith(')'):
        value = parse_number(text[1:-1])
        return -value if value is not None else None
    if text.endswith('%'):
        value = parse_number(text[:-1])
        return value / Decimal(100) if value is not None else None
    return parse_number(text)


def check_formulas(nodes):
    results, unsupported, grouped = [], {}, {}
    by_file = {}
    row_labels = {}
    for n in nodes:
        if n.sheet and n.cell_ref:
            match = re.fullmatch(r"([A-Z]+)(\d+)", n.cell_ref)
            if match:
                from openpyxl.utils.cell import column_index_from_string
                key = (n.file_seq, n.sheet, int(match[2]))
                col = column_index_from_string(match[1])
                if key not in row_labels or col < row_labels[key][0]:
                    row_labels[key] = (col, n.text)
    for n in nodes:
        if n.sheet and n.cell_ref:
            by_file.setdefault(n.file_seq, {})[(n.sheet, n.cell_ref)] = (numeric(n.text), n.formula)
            match = re.fullmatch(r'([A-Z]+)(\d+)', n.cell_ref)
            if match:
                grouped.setdefault((n.file_seq, n.sheet, match[1]), []).append((int(match[2]), n))
    for n in nodes:
        if not n.formula:
            continue
        try:
            actual = evaluate_formula(n.formula, n.sheet, by_file[n.file_seq], stack=((n.sheet, n.cell_ref),))
        except UnsupportedFormula:
            unsupported.setdefault(n.file_seq, []).append(n.loc)
            continue
        cached = numeric(n.text)
        if cached is not None and abs(actual - cached) > Decimal('.01'):
            results.append(CheckResult('L-15', 'high', [n.loc], n.file_seq,
                f'{n.loc} 公式重算值 {actual}，显示值 {cached}',
                f'{n.loc} 公式重算结果与显示值不一致，差异约 {pct_diff(cached, actual)}'))
    for seq, locs in unsupported.items():
        text = '、'.join(locs) + ' 的公式含不支持的函数或引用，未核验'
        results.append(CheckResult('L-INFO', 'low', locs, seq, text, text))
    for entries in grouped.values():
        entries.sort()
        runs, run = [], []
        for row, n in entries:
            label = row_labels.get((n.file_seq, n.sheet, row), (0, ""))[1]
            total_row = bool(re.search(r"小计$|合计$|总计$|^Total$", label, re.I))
            if total_row or numeric(n.text) is None or (run and row != run[-1][0] + 1):
                if run: runs.append(run)
                run = []
            if not total_row and numeric(n.text) is not None:
                run.append((row, n))
        if run: runs.append(run)
        for run in runs:
            shapes = {}
            for row, n in run:
                if n.formula:
                    shape = re.sub(r'(\$?[A-Za-z]{1,3}\$?)\d+', r'\1#', n.formula)
                    shapes.setdefault(shape, []).append(row)
            for rows in shapes.values():
                if len(rows) < 3 or len(rows) * 5 < len(run) * 4:
                    continue
                for row, n in run:
                    if rows[0] < row < rows[-1] and not n.formula:
                        text = f'{n.loc} 应为公式却是手填数字'
                        results.append(CheckResult('L-16', 'low', [n.loc], n.file_seq, text, text))
    return results
