"""号码校验只提供证据，不替代标签识别。"""
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path


def compact(s: str) -> str:
    return re.sub(r'[\s-]', '', s).upper()


def id_card(s: str) -> bool:
    s = compact(s)
    if not re.fullmatch(r'\d{17}[\dX]', s):
        return False
    try:
        birthday = date(int(s[6:10]), int(s[10:12]), int(s[12:14]))
    except ValueError:
        return False
    return (date(1900, 1, 1) <= birthday <= date.today()
            and s[-1] == '10X98765432'[sum(int(c) * w for c, w in zip(
                s[:17], (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2))) % 11])


def uscc(s: str) -> bool:
    s = compact(s)
    chars = '0123456789ABCDEFGHJKLMNPQRTUWXY'
    if len(s) != 18 or any(c not in chars for c in s):
        return False
    return s[-1] == chars[(31 - sum(chars.index(c) * w for c, w in zip(
        s[:17], (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28))) % 31) % 31]


@lru_cache
def iban_lengths() -> dict[str, int]:
    return json.loads((Path(__file__).resolve().parents[2] / 'resources/iban_lengths.json').read_text('utf-8'))


def iban(s: str) -> bool:
    s = compact(s)
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]+', s) or len(s) != iban_lengths().get(s[:2]):
        return False
    digits = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


@lru_cache
def _country_codes() -> set[str]:
    import yaml
    data = yaml.safe_load((Path(__file__).resolve().parents[2] / 'resources/countries.yaml').read_text('utf-8'))
    return {row['code'] for row in data['countries'] if row.get('code')}


def bic(s: str) -> bool:
    s = compact(s)
    return bool(re.fullmatch(r'[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?', s) and s[4:6] in _country_codes())


def luhn(s: str) -> bool:
    s = compact(s)
    if not re.fullmatch(r'\d{12,19}', s):
        return False
    total = 0
    for i, c in enumerate(reversed(s)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0
