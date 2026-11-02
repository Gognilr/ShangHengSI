"""明确日历日期的识别与等距平移；不推测相对期限或歧义日月。"""
import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS = {name.casefold(): n for n in range(1, 13)
           for name in (calendar.month_name[n], calendar.month_abbr[n])}
_MONTHS['sept'] = 9
_MON = '(?:' + '|'.join(sorted(_MONTHS, key=len, reverse=True)) + r')\.?'
_DIGITS = '〇零一二三四五六七八九'
_VALUES = {c: n for c, n in zip(_DIGITS, (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9))}
_CN = re.compile(r'([' + _DIGITS + r']{4})年\s*([' + _DIGITS + r'十]{1,3})月\s*([' + _DIGITS + r'十]{1,3})[日号]')
_DATE = re.compile(
    r'(?<![\dA-Za-z_])(?:\d{4}年\s*\d{1,2}月\s*\d{1,2}[日号]|'
    r'\d{4}(?P<sep>[-/.])\d{1,2}(?P=sep)\d{1,2}|'
    rf'\d{{1,2}}\s+{_MON}\s+\d{{4}}|{_MON}\s+\d{{1,2}},?\s+\d{{4}}|'
    r'[' + _DIGITS + r']{4}年\s*[' + _DIGITS + r'十]{1,3}月\s*[' + _DIGITS + r'十]{1,3}[日号]|'
    r'(?<![A-Za-z0-9_-])\d{8}(?![A-Za-z0-9_-]))(?![\dA-Za-z_])', re.I)
_COMPACT_LABEL = re.compile(r'日期|签约|签订|交货|交付|有效期|装运|至')


@dataclass(frozen=True)
class DateHit:
    start: int
    end: int
    value: date
    style: str  # 原写法，仅供内存渲染，不写日志。


def _cn_number(text: str) -> int:
    if '十' in text:
        a, b = text.split('十')
        return (_VALUES[a] if a else 1) * 10 + (_VALUES[b] if b else 0)
    return _VALUES[text]


def _parse(text: str) -> date | None:
    try:
        if m := re.fullmatch(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})[日号]', text):
            return date(*(int(v) for v in m.groups()))
        if m := re.fullmatch(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})', text):
            return date(int(m[1]), int(m[3]), int(m[4]))
        if m := _CN.fullmatch(text):
            return date(int(''.join(str(_VALUES[c]) for c in m[1])), _cn_number(m[2]), _cn_number(m[3]))
        if re.fullmatch(r'\d{8}', text):
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
        parts = text.replace('.', '').replace(',', '').split()
        if len(parts) == 3:
            if parts[0].casefold() in _MONTHS:
                return date(int(parts[2]), _MONTHS[parts[0].casefold()], int(parts[1]))
            if parts[1].casefold() in _MONTHS:
                return date(int(parts[2]), _MONTHS[parts[1].casefold()], int(parts[0]))
    except (ValueError, KeyError):
        pass
    return None


def find_dates(text: str) -> list[DateHit]:
    hits = []
    for match in _DATE.finditer(text):
        raw = match.group()
        if raw.isdigit() and not _COMPACT_LABEL.search(text[max(0, match.start() - 10):match.start()]):
            continue
        value = _parse(raw)
        if value is not None:
            hits.append(DateHit(match.start(), match.end(), value, raw))
    return hits


def _chinese(n: int) -> str:
    chars = '〇一二三四五六七八九'
    return chars[n] if n < 10 else (chars[n // 10] if n >= 20 else '') + '十' + (chars[n % 10] if n % 10 else '')


def render(style: str, value: date) -> str:
    if m := _CN.fullmatch(style):
        zero = '零' if '零' in m[1] else '〇'
        year = ''.join(zero if c == '0' else '〇一二三四五六七八九'[int(c)] for c in f'{value.year:04}')
        return year + '年' + _chinese(value.month) + '月' + _chinese(value.day) + style[-1]
    if style.isdigit():
        return f'{value.year:04}{value.month:02}{value.day:02}'
    if re.match(r'\d{4}', style):
        numbers = iter((value.year, value.month, value.day))
        return re.sub(r'\d+', lambda m: str(next(numbers)).zfill(len(m.group())), style)
    month = re.search(_MON, style, re.I)
    if month is None:
        raise ValueError('日期格式不受支持')
    token = month.group().rstrip('.')
    full = token.casefold() in {calendar.month_name[n].casefold() for n in range(1, 13)}
    name = calendar.month_name[value.month] if full else calendar.month_abbr[value.month]
    if token.isupper():
        name = name.upper()
    elif token.islower():
        name = name.lower()
    replacement = name + ('.' if month.group().endswith('.') else '')
    rendered = style[:month.start()] + replacement + style[month.end():]
    numbers = iter((value.day, value.year))
    return re.sub(r'\d+', lambda m: str(next(numbers)).zfill(len(m.group())), rendered)


def shift_text(text: str, days: int, *, start_index: int = 0) -> tuple[str, dict[str, str]]:
    if not days:
        return text, {}
    chunks, mapping, cursor = [], {}, 0
    for n, hit in enumerate(find_dates(text), start_index + 1):
        shifted = render(hit.style, hit.value + timedelta(days=days))
        chunks.extend((text[cursor:hit.start], shifted))
        mapping[f'{shifted}#D{n}'] = text[hit.start:hit.end]
        cursor = hit.end
    chunks.append(text[cursor:])
    return ''.join(chunks), mapping
