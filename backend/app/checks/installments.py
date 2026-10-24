"""L-09：正文分期比例与金额；仅明确基数时校验，金额不进入 AI 摘要。"""
import re
from decimal import Decimal, localcontext

from app.checks.consistency import contract_amount
from app.checks.model import CheckNode, CheckFile, CheckResult
from app.checks.numbers import CENT, rounded
from app.redaction.detect import find_amounts

_TOTALS = ('合同总价', '合同总额', '合同金额', '合同价款', '总价款', '总价')
_FILL = r'[ \t【［\[〖]*'
_CLOSE = r'[ \t】］\]〗]*'
_RATE = _FILL + r'(?P<rate>\d+(?:\.\d+)?)' + _CLOSE + r'[%％]'
_AMOUNT = re.compile(r'(?P<base>[\u4e00-\u9fff]{1,30})的\s*' + _RATE +
                     r'\s*[，,]?\s*(?:即|计|为|合)\s*(?P<amount>[^。；;！？\n]+)')
_SUM = re.compile(r'(?:合同总价|合同总额|合同金额|合同价款|总价款)的\s*' + _RATE)
_LEVEL = re.compile(r'^第[^条章\n]{1,20}[条章]$')


def _base(text: str) -> str:
    text = re.split(r'支付|预付款为|付款为|应付|为|按', text)[-1]
    for label in _TOTALS:
        if label.startswith('合同') and text.endswith(label):
            return label
    return re.split(r'支付|预付款为|付款为|应付|为|按', text)[-1]


def _named_base(nodes: list[CheckNode], node: CheckNode, base: str):
    pattern = re.compile(re.escape(base) + r'\s*(?:为|是|：|:)\s*(?P<amount>[^。；;！？\n]+)')
    found = []
    for other in nodes:
        if other.file_seq != node.file_seq:
            continue
        for m in pattern.finditer(other.text):
            hits = {(h.currency, h.value) for h in find_amounts(m['amount']) if h.value is not None and h.currency}
            if len(hits) == 1:
                found.append(next(iter(hits)))
    return found[0] if len(found) == 1 else None


def check_installments(nodes: list[CheckNode], files: list[CheckFile]) -> list[CheckResult]:
    results = []
    total = contract_amount(nodes, files)
    with localcontext() as context:
        context.prec = max(28, sum(len(n.text) for n in nodes) + 8)
        for node in nodes:
            for m in _AMOUNT.finditer(node.text):
                base = _base(m['base'])
                basis = (total[1], total[2]) if base in _TOTALS and total else _named_base(nodes, node, base)
                if basis is None:
                    continue
                amounts = {(h.currency, h.value) for h in find_amounts(m['amount']) if h.value is not None}
                if len(amounts) != 1:
                    continue
                currency, actual = next(iter(amounts))
                if currency and currency != basis[0]:
                    continue
                expected = rounded(basis[1] * Decimal(m['rate']) / 100)
                if abs(expected - actual) > CENT:
                    results.append(CheckResult('L-09', 'high', [node.loc], node.file_seq,
                        f"{node.loc}：{base}的 {m['rate']}% 应为 {expected:.2f}，文中写 {actual}",
                        f'{node.loc}：比例金额与基数不一致'))
        # 按文件独立维护一级条款，避免跨附件、跨条款合并。
        for seq in dict.fromkeys(n.file_seq for n in nodes):
            section = []
            active = False

            def finish():
                if len(section) < 2:
                    return
                value = sum((r for _, r in section), Decimal(0))
                if abs(value - 100) <= CENT:
                    return
                over = value > 100
                text = f'各期付款比例合计 {value:f}%，' + ('超过 100%' if over else
                    '不足 100%，请确认其余部分是否另有约定（如质保金在其他条款）')
                results.append(CheckResult('L-09', 'medium' if over else 'low',
                    list(dict.fromkeys(loc for loc, _ in section)), seq, text, text))

            for node in (n for n in nodes if n.file_seq == seq):
                if _LEVEL.fullmatch(node.clause_no or ''):
                    finish()
                    section = []
                    active = bool(re.search('付款|支付|结算', node.text))
                if active:
                    section += [(node.loc, Decimal(m['rate'])) for m in _SUM.finditer(node.text)]
            finish()
    return results
