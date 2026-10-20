"""日期解析和 L-07、L-08。只接受明确的日历日期；今天由调用方传入。"""
import calendar
import re
from datetime import date

from app.checks.model import CheckNode, CheckResult, SEVERITY

_MONTHS = {name.casefold(): n for n in range(1, 13)
           for name in (calendar.month_name[n], calendar.month_abbr[n])}
_MONTHS["sept"] = 9
_MON = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DATE = re.compile(r"(?<![\dA-Za-z])(?:\d{4}年\s*\d{1,2}月\s*\d{1,2}日|"
                   r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
                   rf"\d{{1,2}}\s+{_MON}\s+\d{{4}}|{_MON}\s+\d{{1,2}},?\s+\d{{4}})(?![\dA-Za-z])", re.I)
_LABEL_TEXT = {
    "expiry": r"报价有效期|有效期至|有效期截止|有效至|Valid\s+until|Valid\s+till|Validity|Offer\s+valid",
    "delivery": r"交货日期|交货期|交期|交付日期|装运期|装运日期|最迟装运日|Delivery\s+date|Date\s+of\s+delivery|Shipment\s+date|Date\s+of\s+shipment|Latest\s+shipment",
    "signing": r"签订日期|签约日期|签署日期|合同日期|Date\s+of\s+signing|Signing\s+date|Contract\s+date",
}
_ALL_LABELS = re.compile("|".join(_LABEL_TEXT.values()), re.I)
_RELATIVE = re.compile(r"\d+\s*(?:天|日|周|个月|年|days?\b|weeks?\b|months?\b)", re.I)


def _parse(text: str) -> date | None:
    match = re.fullmatch(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    try:
        if match:
            return date(*(int(x) for x in match.groups()))
        match = re.fullmatch(r"(\d{4})([-/.])(\d{1,2})\2(\d{1,2})", text)
        if match:
            return date(int(match[1]), int(match[3]), int(match[4]))
        parts = text.replace(".", "").replace(",", "").split()
        if len(parts) == 3:
            if parts[0].casefold() in _MONTHS:
                return date(int(parts[2]), _MONTHS[parts[0].casefold()], int(parts[1]))
            if parts[1].casefold() in _MONTHS:
                return date(int(parts[2]), _MONTHS[parts[1].casefold()], int(parts[0]))
    except ValueError:
        pass
    return None


def find_dates(text: str) -> list[tuple[int, int, date]]:
    return [(m.start(), m.end(), value) for m in _DATE.finditer(text) if (value := _parse(m.group())) is not None]


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
            values = find_dates(tail)
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
            results.append(CheckResult("L-07", SEVERITY["L-07"], [node.loc], node.file_seq, text, text))
    signing = _labelled(nodes, "signing")
    if len({value for _, value in signing}) == 1:
        signed_node, signed = signing[0]
        for node, value in _labelled(nodes, "delivery"):
            if value < signed:
                text = f"{node.loc}：交货日期 {value.isoformat()} 早于签约日期 {signed.isoformat()}（{signed_node.loc}）"
                results.append(CheckResult("L-08", SEVERITY["L-08"], [node.loc, signed_node.loc], node.file_seq, text, text))
    return results
