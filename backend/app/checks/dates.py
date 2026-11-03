"""日期解析和 L-07、L-08。只接受明确的日历日期；今天由调用方传入。"""
import re
from datetime import date

from app.checks.model import CheckNode, CheckResult, SEVERITY

_LABEL_TEXT = {
    "expiry": r"报价有效期|有效期至|有效期截止|有效至|Valid\s+until|Valid\s+till|Validity|Offer\s+valid",
    "delivery": r"交货日期|交货期|交期|交付日期|装运期|装运日期|最迟装运日|Delivery\s+date|Date\s+of\s+delivery|Shipment\s+date|Date\s+of\s+shipment|Latest\s+shipment",
    "signing": r"签订日期|签约日期|签署日期|合同日期|Date\s+of\s+signing|Signing\s+date|Contract\s+date",
}
_ALL_LABELS = re.compile("|".join(_LABEL_TEXT.values()), re.I)
_RELATIVE = re.compile(r"\d+\s*(?:天|日|周|个月|年|days?\b|weeks?\b|months?\b)", re.I)


def find_dates(text: str) -> list[tuple[int, int, date]]:
    from app.redaction.dates import find_dates as shared_dates
    return [(h.start, h.end, h.value) for h in shared_dates(text)]


def parse_date(text: str) -> date | None:
    text = text.strip()
    values = find_dates(text)
    return values[0][2] if len(values) == 1 and values[0][:2] == (0, len(text)) else None


def _labelled(nodes: list[CheckNode], kind: str) -> list[tuple[CheckNode, date]]:
    result = []
    for node in nodes:
        seen = set()
        for label in re.finditer(_LABEL_TEXT[kind], node.text, re.I):
            tail = node.text[label.end():label.end() + 80]
            context = node.text[max(0, label.end() - 10):label.end()]
            values = [(a - len(context), b - len(context), value)
                      for a, b, value in find_dates(context + tail) if a >= len(context)]
            if not values or values[0][0] > 30:
                continue
            start, end, value = values[0]
            prefix = tail[:start]
            # Q-94：相对期限和下一个日期标签不能借用后面的绝对日期来猜含义。
            if _RELATIVE.search(prefix) or _ALL_LABELS.search(prefix):
                continue
            position = label.end() + start
            if position not in seen:
                seen.add(position)
                result.append((node, value))
    return result


def check_dates(nodes: list[CheckNode], today: date) -> list[CheckResult]:
    results = []
    for node, value in _labelled(nodes, "expiry"):
        if value < today:
            text = f"{node.loc}：报价有效期截止 {value.isoformat()}，早于今天（{today.isoformat()}）"
            # 摘要外发，只写天数：日期平移开启时原日期是对照表中的真实值。
            summary = f"{node.loc}：报价有效期截止日早于今天，已过 {(today - value).days} 天"
            results.append(CheckResult("L-07", SEVERITY["L-07"], [node.loc], node.file_seq, text, summary))
    signing = _labelled(nodes, "signing")
    if len({value for _, value in signing}) == 1:
        signed_node, signed = signing[0]
        for node, value in _labelled(nodes, "delivery"):
            if value < signed:
                text = f"{node.loc}：交货日期 {value.isoformat()} 早于签约日期 {signed.isoformat()}（{signed_node.loc}）"
                summary = f"{node.loc}：交货日期早于签约日期（{signed_node.loc}）{(signed - value).days} 天"
                results.append(CheckResult("L-08", SEVERITY["L-08"], [node.loc, signed_node.loc], node.file_seq, text, summary))
    return results
