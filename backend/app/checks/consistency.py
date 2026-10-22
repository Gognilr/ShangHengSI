"""L-05、L-06：比较主文件合同总价与报价表合计（含主文件自身的报价表），不读取数据库。"""
from decimal import Decimal

from app.checks.amounts import QuoteTableCheck, _scan_tables
from app.checks.model import CheckFile, CheckNode, CheckResult, SEVERITY
from app.checks.numbers import CENT, money, pct_diff, rounded
from app.redaction.detect import find_amounts
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
                      tables: list[QuoteTableCheck]) -> list[CheckResult]:
    total = contract_amount(nodes, files)
    if total is None:
        return []
    location, currency, value = total
    layouts = {t.name: t for t in _scan_tables(nodes)}
    results = []
    for table in compared_tables(nodes, files, tables):
        layout = layouts[table.table]
        locs = [location, layout.node(layout.total_row, layout.amount).loc]
        if table.currency is not None and table.currency != currency:
            text = f"主文件合同总价币种为 {currency}，报价表 {table.table} 币种为 {table.currency}"
            results.append(CheckResult("L-06", SEVERITY["L-06"], locs, table.file_seq, text, text))
        elif abs(rounded(value) - rounded(table.total)) > CENT:
            detail = (f"主文件合同总价 {currency} {money(value)}，报价表 {table.table} 合计 {money(table.total)}，"
                      f"相差 {money(abs(rounded(value) - rounded(table.total)))}")
            if table.currency is None:
                detail += "（报价表未注明币种，按合同币种比较）"
            results.append(CheckResult("L-05", SEVERITY["L-05"], locs, table.file_seq, detail,
                                       f"主文件合同总价与报价表 {table.table} 合计不一致，差异约 {pct_diff(table.total, value)}"))
    return results
