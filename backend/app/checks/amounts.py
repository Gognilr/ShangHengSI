"""报价表识别与确定性金额校验（M6 3.1、3.2）。纯函数，不记录原文或金额日志。"""
import re
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.checks.model import CheckFile, CheckNode, CheckResult, SEVERITY
from app.checks.numbers import CENT, money, parse_number, parse_rate, pct_diff, rounded
from app.parsing.xlsx import WARN_FORMULA
from app.redaction.detect import _CURRENCY
from app.redaction.tables import HEADER_SCAN_ROWS, table_cell

_CURRENCY_TOKEN = re.compile(
    "|".join(r"(?<![A-Za-z])" + re.escape(k) + r"(?![A-Za-z])" if k.isascii() and k[0].isalpha()
             else re.escape(k) for k in sorted(_CURRENCY, key=len, reverse=True)), re.IGNORECASE)
_TOTAL = re.compile(r"合计|总计|\btotal\b", re.IGNORECASE)
_AMOUNTS = ("amount", "amount_excl", "amount_incl")


@dataclass
class QuoteTableCheck:
    table: str
    file_seq: int
    recognized: bool
    rows_checked: int
    rows_skipped: list[int]
    row_errors: int
    total: Decimal | None
    total_ok: bool | None
    currency: str | None


def column_kind(text: str) -> str | None:
    """按设计固定顺序匹配，避免税率/税额、总价/单价的包含关系。"""
    for pattern, kind in ((r"税率|\b(?:tax|vat)\s*rate\b", "rate"),
                          (r"税额|\b(?:tax|vat)\b", "tax"),
                          (r"单价|\bunit\s*price\b", "price"),
                          (r"金额|总价|小计|\b(?:amount|total|subtotal)\b", "amount"),
                          (r"\bprice\b", "price"), (r"数量|\b(?:qty|quantity)\b", "qty")):
        if re.search(pattern, text, re.IGNORECASE):
            if kind == "amount":
                low = text.casefold()
                if any(x in low for x in ("不含税", "excl", "net")):
                    return "amount_excl"
                if any(x in low for x in ("含税", "incl", "gross")):
                    return "amount_incl"
            return kind
    return None


def _col_number(col: str) -> int:
    if col.isdigit():
        return int(col)
    number = 0
    for ch in col:
        number = number * 26 + ord(ch) - ord("A") + 1
    return number


@dataclass
class _Table:
    name: str
    file_seq: int
    rows: dict[int, dict[int, CheckNode]]
    header: int
    columns: dict[str, int]
    total_row: int | None

    def node(self, row: int, kind: str) -> CheckNode | None:
        return self.rows[row].get(self.columns.get(kind, -1))

    def text(self, row: int, kind: str) -> str:
        node = self.node(row, kind)
        return node.text if node else ""

    @property
    def amount(self) -> str:
        # Q-91：多种金额列同时存在时，普通金额优先，其次不含税，最后含税；不推断单价税务口径。
        return next((kind for kind in _AMOUNTS if kind in self.columns), "amount")

    @property
    def recognized(self) -> bool:
        return "qty" in self.columns and "price" in self.columns and any(x in self.columns for x in _AMOUNTS)

    @property
    def first_loc(self) -> str:
        row = self.rows[self.header]
        return row[min(row)].loc

    @property
    def data_rows(self) -> list[int]:
        # Q-91：保留金额/单价有文字但都无法解析的行，必须报告无法核验，不能静默忽略。
        return [r for r in sorted(self.rows) if r > self.header and (self.total_row is None or r < self.total_row)
                and (self.text(r, "price").strip() or self.text(r, self.amount).strip())]


def _scan_tables(nodes: list[CheckNode]) -> list[_Table]:
    grouped: dict[str, tuple[int, dict[int, dict[int, CheckNode]]]] = {}
    for node in nodes:
        cell = table_cell(node.loc)
        if cell:
            name, col, row = cell
            grouped.setdefault(name, (node.file_seq, {}))[1].setdefault(row, {})[_col_number(col)] = node
    out = []
    for name, (seq, rows) in grouped.items():
        candidates = []
        for row in sorted(r for r in rows if r <= HEADER_SCAN_ROWS):
            columns = {}
            for col, node in sorted(rows[row].items()):
                kind = column_kind(node.text)
                if kind:
                    columns.setdefault(kind, col)
            amount = any(k in columns for k in _AMOUNTS)
            complete = "qty" in columns and "price" in columns and amount
            if "price" in columns or ("qty" in columns and amount):
                candidates.append((complete, row, columns))
        if not candidates:
            continue
        _, header, columns = next((x for x in candidates if x[0]), candidates[0])
        total = None
        for row in sorted(r for r in rows if r > header):
            first = next((n.text for _, n in sorted(rows[row].items()) if n.text.strip()), "")
            if _TOTAL.search(first):
                total = row
                break
        out.append(_Table(name, seq, rows, header, columns, total))
    return out


def _result(code: str, table: _Table, locs: list[str], detail: str, summary: str) -> CheckResult:
    return CheckResult(code, SEVERITY[code], locs, table.file_seq, detail, summary)


def _currency(table: _Table) -> str | None:
    rows = [*table.data_rows, *([table.total_row] if table.total_row is not None else [])]
    texts = [node.text for row in rows for col, node in sorted(table.rows[row].items())
             if col in {table.columns[k] for k in (*_AMOUNTS, "price") if k in table.columns}]
    texts += [n.text for _, n in sorted(table.rows[table.header].items())]
    for text in texts:
        match = _CURRENCY_TOKEN.search(text)
        if match:
            return _CURRENCY.get(match.group().upper()) or _CURRENCY.get(match.group())
    return None


def _tax_checks(table: _Table, row: int) -> list[CheckResult]:
    results = []
    base = parse_number(table.text(row, "amount_excl" if "amount_excl" in table.columns else "amount"))
    tax = parse_number(table.text(row, "tax"))
    rate = parse_rate(table.text(row, "rate"))
    gross = parse_number(table.text(row, "amount_incl"))
    if base is not None and tax is not None and rate is not None:
        expected, actual = rounded(base * rate), rounded(tax)
        if abs(actual - expected) > CENT:
            label = "不含税金额" if "amount_excl" in table.columns else "金额"
            detail = (f"报价表 {table.name} 第 {row} 行：{label} {money(base)} × 税率 {rate * 100}% = "
                      f"{money(expected)}，表中税额 {money(actual)}")
            results.append(_result("L-03", table, [table.node(row, "tax").loc], detail,
                                   f"{table.name} 第 {row} 行：税额与金额×税率不一致，差异约 {pct_diff(actual, expected)}"))
    if "amount_excl" in table.columns and base is not None and tax is not None and gross is not None:
        expected, actual = rounded(base + tax), rounded(gross)
        if abs(actual - expected) > CENT:
            detail = (f"报价表 {table.name} 第 {row} 行：不含税金额 {money(base)} + 税额 {money(tax)} = "
                      f"{money(expected)}，表中含税金额 {money(actual)}")
            results.append(_result("L-03", table, [table.node(row, "amount_incl").loc], detail,
                                   f"{table.name} 第 {row} 行：含税金额与不含税金额＋税额不一致，差异约 {pct_diff(actual, expected)}"))
    return results


def _check_table(table: _Table) -> tuple[QuoteTableCheck, list[CheckResult]]:
    checked, skipped, errors, results = 0, [], 0, []
    total = parse_number(table.text(table.total_row, table.amount)) if table.total_row is not None else None
    info = QuoteTableCheck(table.name, table.file_seq, table.recognized, 0, [], 0, total, None, _currency(table))
    if not table.recognized:
        text = "未识别报价表结构，未做金额核验"
        return info, [_result("L-INFO", table, [table.first_loc], text, text)]
    amounts = []
    tax_results = []
    for row in table.data_rows:
        qty, price, amount = (parse_number(table.text(row, k)) for k in ("qty", "price", table.amount))
        amounts.append(amount)
        if qty is None or price is None or amount is None:
            skipped.append(row)
        else:
            checked += 1
            expected, actual = rounded(qty * price), rounded(amount)
            if abs(actual - expected) > CENT:
                errors += 1
                detail = (f"报价表 {table.name} 第 {row} 行：数量 {qty} × 单价 {money(price)} = {money(expected)}，"
                          f"表中金额 {money(actual)}，相差 {money(abs(actual - expected))}")
                results.append(_result("L-01", table,
                                       [table.node(row, k).loc for k in (table.amount, "qty", "price")], detail,
                                       f"{table.name} 第 {row} 行：数量×单价与金额不一致，差异约 {pct_diff(actual, expected)}"))
        tax_results += _tax_checks(table, row)
    if total is not None and amounts and all(x is not None for x in amounts):
        expected, actual = rounded(sum((rounded(x) for x in amounts), Decimal(0))), rounded(total)
        info.total_ok = abs(actual - expected) <= CENT
        if not info.total_ok:
            detail = (f"报价表 {table.name} 合计行（第 {table.total_row} 行）：各行金额之和 {money(expected)}，"
                      f"合计 {money(actual)}，相差 {money(abs(actual - expected))}")
            results.append(_result("L-02", table, [table.node(table.total_row, table.amount).loc], detail,
                                   f"{table.name} 合计行：各行金额之和与合计不一致，差异约 {pct_diff(actual, expected)}"))
    if table.total_row is not None:
        tax_results += _tax_checks(table, table.total_row)
    results += tax_results
    if skipped:
        rows = "、".join(str(r) for r in skipped[:10])
        ending = f"等 {len(skipped)} 行" if len(skipped) > 10 else ""
        text = f"{table.name} 第 {rows} 行{ending}数字无法识别，未做金额核验"
        results.append(_result("L-INFO", table, [table.first_loc], text, text))
    info.rows_checked, info.rows_skipped, info.row_errors = checked, skipped, errors
    return info, results


def _evaluate_tables(nodes: list[CheckNode]) -> tuple[list[QuoteTableCheck], list[CheckResult]]:
    tables, results = [], []
    # 精度随输入长度增加，避免很长但有效的十进制数因默认 28 位精度而被截断。
    with localcontext() as ctx:
        ctx.prec = max(28, sum(len(n.text) for n in nodes) + 8)
        for table in _scan_tables(nodes):
            info, findings = _check_table(table)
            tables.append(info)
            results += findings
    return tables, results


def find_tables(nodes: list[CheckNode]) -> list[QuoteTableCheck]:
    return _evaluate_tables(nodes)[0]


def check_tables(nodes: list[CheckNode], files: list[CheckFile]) -> tuple[list[QuoteTableCheck], list[CheckResult]]:
    tables, results = _evaluate_tables(nodes)
    for file in files:
        if file.ext == "xlsx" and WARN_FORMULA in file.warnings:
            text = f"F{file.seq} 含未计算的公式，相关金额无法核验"
            results.append(CheckResult("L-INFO", SEVERITY["L-INFO"], [], file.seq, text, text))
        if file.doc_type == "quotation" and not any(t.file_seq == file.seq for t in tables):
            text = f"F{file.seq} 未识别报价表结构，未做金额核验"
            results.append(CheckResult("L-INFO", SEVERITY["L-INFO"], [], file.seq, text, text))
    return tables, results
