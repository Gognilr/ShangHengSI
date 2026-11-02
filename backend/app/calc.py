"""回复中的代号算式；仅在本机展示时计算，不修改模型原回复。"""
import ast
import re
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext

from app.checks.numbers import parse_number
from app.redaction import restore
from app.redaction.detect import _CURRENCY, chinese_upper_value

INSTRUCTION = '需要计算时，不要自己算出数字，把算式单独写一行：【算式】名称 = 表达式，表达式只用文件中的金额、单价、数量代号、数字常数、+ − × ÷ 和括号。'
_CODE = re.compile(r'\[(?:金额|单价|数量)_Q?\d+\]|\[\[(?:AMT|PRICE|QTY)_\d+\]\]')
_NUMBER = re.compile(r'(?:\d+(?:\.\d+)?|\.\d+)%?')


class CalcError(Exception):
    pass


def _number(text):
    # parse_number 不接受范围和倍率；大写金额只复用 L-04 已有的识别结果。
    value = parse_number(text)
    if value is None:
        from app.checks.numbers import _CURRENCIES
        value = chinese_upper_value(_CURRENCIES.sub('', text).strip()) if re.search(r'[壹贰叁肆伍陆柒捌玖拾佰仟]', text) else None
    return value


def _currency(text):
    from app.checks.amounts import _CURRENCY_TOKEN
    return {_CURRENCY.get(m.group().upper(), _CURRENCY.get(m.group(), m.group()))
            for m in _CURRENCY_TOKEN.finditer(text)}


def _eval(node, values):
    if isinstance(node, ast.Expression):
        return _eval(node.body, values)
    if isinstance(node, ast.Name) and node.id in values:
        return values[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand, values)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        a, b = _eval(node.left, values), _eval(node.right, values)
        if isinstance(node.op, ast.Add): return a + b
        if isinstance(node.op, ast.Sub): return a - b
        if isinstance(node.op, ast.Mult): return a * b
        if not b: raise ZeroDivisionError
        return a / b
    raise CalcError('unparsable')


def evaluate(text: str, mapping: dict[str, str], *, labels=None, currencies_by_code=None, locations=None, dates=None) -> list[dict]:
    """纯函数；偏移为 check() 还原后的回复码点，line_end 不含换行。"""
    labels = labels or {}
    out = []
    offset = 0
    for line in text.splitlines(keepends=True):
        raw = line.rstrip('\r\n')
        match = re.match(r'^\s*【算式】\s*(.*?)\s*[=＝]\s*(.*?)\s*$', raw)
        if not raw.lstrip().startswith('【算式】'):
            offset += len(line)
            continue
        checked = restore.check(raw, mapping, locations=locations, dates=dates).text
        start = len(restore.check(text[:offset], mapping, locations=locations, dates=dates).text)
        item = dict(line_start=start, line_end=start + len(checked), expression=checked,
                    result='', status='unparsable')
        try:
            if match is None or not match[1] or not match[2]: raise CalcError('unparsable')
            expr = match[2]
            # 与 check 共用候选及代号归一规则；不把类别以外的代号当作常数。
            lookup = {}
            for code, value in mapping.items():
                if _CODE.fullmatch(code):
                    identity = restore._identity(code) if not code.startswith('[[') else code
                    lookup.setdefault(identity, []).append((code, value))
            values, tokens, currencies, scales, displays = {}, [], set(), [2], []
            cursor = 0
            for token in restore._candidates().finditer(expr):
                displays.append(expr[cursor:token.start()])
                identity = restore._identity(token.group())
                if identity is None:
                    identity = next((c for c in mapping if c.startswith('[[') and token.group() in restore._variants(c)), None)
                entries = lookup.get(identity, [])
                if len(entries) != 1: raise CalcError('unknown_code')
                code, surface = entries[0]
                value = _number(surface)
                if value is None: raise CalcError('unparsable')
                key = f'v{len(values)}'; values[key] = value
                currencies.update(_currency(surface))
                currencies.update((currencies_by_code or {}).get(code, ()))
                if '金额' in code or '单价' in code or code.startswith(('[[AMT_', '[[PRICE_')):
                    scales.append(max(0, -value.as_tuple().exponent))
                displays.append(labels.get(code, '') + surface)
                tokens.append((token.start(), token.end(), key))
                cursor = token.end()
            displays.append(expr[cursor:])
            name = restore.check(match[1], mapping, locations=locations, dates=dates).text
            item['expression'] = f'{name} = ' + ''.join(displays)
            if len(currencies) > 1: raise CalcError('currency_mixed')
            rewritten, cursor = [], 0
            for a, b, key in tokens:
                if re.search(r'[A-Za-z_]', expr[cursor:a]): raise CalcError('unparsable')
                rewritten.extend((expr[cursor:a], f' {key} ')); cursor = b
            if re.search(r'[A-Za-z_]', expr[cursor:]): raise CalcError('unparsable')
            rewritten.append(expr[cursor:])
            expr = ''.join(rewritten).translate(str.maketrans('×÷−（）', '*/-()'))
            lexical, cursor = [], 0
            lex = re.compile(r'\s+|v\d+|(?:\d+(?:\.\d+)?|\.\d+)%?|[+*/()\-]')
            for t in lex.finditer(expr):
                if t.start() != cursor: raise CalcError('unparsable')
                cursor = t.end(); value = t.group()
                if value.isspace(): continue
                if _NUMBER.fullmatch(value):
                    key = f'n{len(values)}'
                    values[key] = Decimal(value.rstrip('%')) / (100 if value.endswith('%') else 1)
                    value = key
                lexical.append(value)
            if cursor != len(expr): raise CalcError('unparsable')
            with localcontext() as ctx:
                ctx.prec = 50
                value = _eval(ast.parse(' '.join(lexical), mode='eval'), values)
                percent = bool(re.search(r'率|比|%', match[1]) or re.search(r'[×*]\s*100%\s*$', match[2]))
                digits = 1 if percent else max(scales)
                if percent: value *= 100
                value = value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
            item.update(status='ok', result=f'{value:f}' + ('%' if percent else (' ' + next(iter(currencies)) if currencies else '')))
        except CalcError as exc:
            item['status'] = str(exc)
        except ZeroDivisionError:
            item['status'] = 'div_zero'
        except (SyntaxError, DecimalException, ValueError, RecursionError):
            item['status'] = 'unparsable'
        out.append(item)
        offset += len(line)
    return out


def code_context(db, task, text, mapping):
    """从第一个实际替换位置读取行标签；无法确定时仅显示位置。"""
    from sqlalchemy import select
    from app.db.models import DocNode, RedactionItem, TaskFile
    from app.redaction.tables import table_cell, _col_number
    from app.security import crypto
    candidates = {m.group() for m in restore._candidates().finditer(text)}
    identities = {restore._identity(c) for c in candidates} - {None}
    codes = [code for code in mapping if _CODE.fullmatch(code) and (
        restore._identity(code) in identities or any(v in candidates for v in restore._variants(code)))]
    if not codes:
        return {}
    nodes = list(db.scalars(select(DocNode).join(TaskFile, TaskFile.id == DocNode.file_id)
                           .where(DocNode.task_id == task.id, DocNode.part == 'body').order_by(TaskFile.seq, DocNode.ord)))
    by_loc = {n.loc: n for n in nodes}
    order = {n.loc: i for i, n in enumerate(nodes)}
    items = sorted(db.scalars(select(RedactionItem).where(RedactionItem.task_id == task.id, RedactionItem.placeholder.in_(codes))),
                   key=lambda r: (order.get(r.loc, len(nodes)), r.char_start))
    rows = {}
    for n in nodes:
        cell = table_cell(n.loc)
        if cell: rows.setdefault((cell[0], cell[2]), []).append((cell[1], n))
    header_currencies = {}
    for (table, row), cells in rows.items():
        if row > 10: continue
        for col, header in cells:
            if header.text_enc:
                header_text = crypto.decrypt_text(header.text_enc)
                if _number(header_text) is None:
                    header_currencies.setdefault((table, col, row), set()).update(_currency(header_text))
    labels, currencies = {}, {}
    for item in items:
        if item.placeholder in labels: continue
        label = ''
        node = by_loc.get(item.loc)
        source = crypto.decrypt_text(node.text_enc) if node and node.text_enc else ''
        from app.redaction.detect import find_amounts
        hits = [h for h in find_amounts(source) if h.start < item.char_end and item.char_start < h.end]
        currencies[item.placeholder] = {h.currency for h in hits if h.currency}
        cell = table_cell(item.loc)
        if cell:
            if not currencies[item.placeholder]:
                # 财务表格币种常在表头，明确标注才沿用。
                currencies[item.placeholder] = set().union(*(header_currencies.get((cell[0], cell[1], r), set())
                                                           for r in range(1, min(cell[2], 11))))
            for col, node in sorted(rows.get((cell[0], cell[2]), []), key=lambda x: _col_number(x[0])):
                if _col_number(col) >= _col_number(cell[1]): break
                text = crypto.decrypt_text(node.text_enc) if node.text_enc else ''
                if text.strip() and _number(text) is None:
                    label = text.strip(); break
        elif item.loc in by_loc:
            node = by_loc[item.loc]
            text = crypto.decrypt_text(node.text_enc) if node.text_enc else ''
            label = re.split(r'[。；;\n]', text[:item.char_start])[-1].strip(' ：:')
        labels[item.placeholder] = f'{label}（{item.loc}）'
    return dict(labels=labels, currencies_by_code=currencies)


def add_to_message(result, text, mapping, **kwargs):
    calcs = evaluate(text, mapping, **kwargs)
    result['calcs'] = calcs
    result['marks'].extend(dict(start=c['line_start'], end=c['line_end'], kind='calc',
                                raw=result['content'][c['line_start']:c['line_end']])
                           for c in calcs if c['status'] != 'ok')
