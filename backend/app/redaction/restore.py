"""代号还原（SPEC 9.9，设计 docs/design/M2.md 第六节）。

模型返回的问题说明、修改建议、对话回复在展示和导出时把代号换回真实值；数据库中始终保存代号形式。
- 一次扫描完成全部替换，长代号优先（“客户_AB”不会被当成“客户_A”+“B”），已换上的真实值不会被再次替换；
- 以字母或数字结尾的代号后面紧跟字母、数字、下划线时不算（“人员_1”不误伤“人员_12”）；
- 方括号代号也接受全角方括号写法（模型常把 [金额_1] 写成 ［金额_1］）；
- 对照表中“代号#序号”形式的键只用于出口复检，不参与还原。
"""
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from app.redaction.placeholders import _NEUTRAL_LABELS, FIXED, NEUTRAL, NUMBERED_LABELS, ROLE_LABELS


def _variants(code: str) -> list[str]:
    if code.startswith("[") and code.endswith("]"):
        return [left + code[1:-1] + right for left in '[［【' for right in ']］】']
    return [code]


@lru_cache(maxsize=64)
def _pattern(codes: tuple[str, ...]) -> re.Pattern | None:
    parts = []
    for variant in sorted({v for c in codes for v in _variants(c)}, key=len, reverse=True):
        p = re.escape(variant)
        if variant[-1].isalnum() and variant[-1].isascii():
            p += r"(?![A-Za-z0-9_])"
        parts.append(p)
    return re.compile("|".join(parts)) if parts else None


def restore(text: str, mapping: dict[str, str]) -> str:
    """把 text 中的代号替换为真实值。"""
    if not text or not mapping:
        return text
    lookup = {}
    for code, value in mapping.items():
        if "#" in code or not code or code.startswith('@'):
            continue
        for variant in _variants(code):
            lookup[variant] = value
    pattern = _pattern(tuple(sorted(lookup)))
    if pattern is None:
        return text
    return pattern.sub(lambda m: lookup[m.group()], text)


@dataclass(frozen=True)
class Mark:
    start: int
    end: int
    kind: str
    raw: str


@dataclass(frozen=True)
class Checked:
    text: str
    marks: tuple[Mark, ...]
    restored: int
    fixed: int
    removed: int


_ENGLISH = {
    "Customer": "客户", "Client": "客户", "Supplier": "供应商", "Vendor": "供应商",
    "Buyer": "买方", "Seller": "卖方", "Consignee": "收货人", "Notify": "通知方",
    "Notify Party": "通知方", "Beneficiary": "受益人", "Bank": "银行", "Company": "公司",
    "Other": "其他", "Person": "人员", "Project": "项目", "Ref": "编号", "No": "编号",
    "Account": "账号", "Amount": "金额", "Price": "单价", "Unit Price": "单价",
    "Qty": "数量", "Quantity": "数量", "Term": "敏感词",
}
_ALIASES = {re.sub(r"\s", "", key).upper(): value for key, value in _ENGLISH.items()}
_LABELS = ROLE_LABELS | NUMBERED_LABELS
_LEFT, _RIGHT = "[［【〔", "]］】〕"
_ASCII_WORD = r"A-Za-z0-9_Ａ-Ｚａ-ｚ０-９＿"
_LOC = r"F\d+(?:-[A-Z]{1,2}\d+){1,4}"
_LOCATION = re.compile(rf"(?<![A-Za-z0-9-])(?:\[{_LOC}\]|{_LOC})(?![A-Za-z0-9-])")
_RATIO = r"[（(]\s*=合同总价的\s*\d+(?:\.\d+)?\s*%\s*[）)]"
_SHARED = frozenset(code[1:-1] for code in FIXED.values())


def _identity(raw: str) -> tuple[str, str] | None:
    """只归一文法允许的代号，不把普通词猜成其他类别。"""
    body = re.sub(r"\s", "", unicodedata.normalize("NFKC", raw)).upper()
    if raw[0] in _LEFT and raw[-1] in _RIGHT:
        body = body[1:-1]
    if body == "我方":
        return "our", body
    if body in _SHARED or body.startswith("地址:") and len(body) > 3:
        return "shared", body
    for label in sorted(_LABELS | _ALIASES.keys(), key=len, reverse=True):
        if not body.startswith(label):
            continue
        chinese = _ALIASES.get(label, label)
        suffix = body[len(label):].removeprefix("_")
        if chinese in ROLE_LABELS and re.fullmatch(r"[A-Z]+", suffix):
            return "role", f"{chinese}_{suffix}"
        if chinese in NUMBERED_LABELS and re.fullmatch(r"Q?\d+(?::[^\[\]]+)?", suffix):
            return "number", f"{chinese}_{suffix}"
    return None


@lru_cache(maxsize=1)
def _candidates() -> re.Pattern:
    labels = [re.escape(label) for label in _LABELS]
    labels += ["(?i:" + r"[ \t]*".join(map(re.escape, name.split())) + ")" for name in _ENGLISH]
    label = "(?:" + "|".join(sorted(labels, key=len, reverse=True)) + ")"
    # 助手只接受 restore() 已支持的外层括号变体，不扩展自动修正。
    neutral = r"[\[［【]\[(?:OUR|[A-Z]+_\d+)\][\]］】]"
    bracket = r"[\[［【〔][^\[\]［］【】〔〕\r\n]+[\]］】〕]"
    bare = rf"(?<![{_ASCII_WORD}]){label}[ \t]*[_＿]?[ \t]*(?:[A-ZＡ-Ｚ]+|[QＱ]?[0-9０-９]+)(?![{_ASCII_WORD}])"
    # 不带括号的助手代号只认助手类别；大写 OUR、ISO_9001 等普通英文不算候选。
    tags = "|".join(sorted(set(_NEUTRAL_LABELS.values()), key=len, reverse=True))
    bare_neutral = rf"(?<![{_ASCII_WORD}])(?:{tags})_\d+(?![{_ASCII_WORD}])"
    return re.compile("|".join((
        _RATIO, r"【主体说明】|【报价明细表：[^】]*】", neutral, _LOCATION.pattern,
        bracket, bare, bare_neutral, "我方",
    )))


def check(text: str | None, mapping: dict[str, str], *, clause: bool = False,
          locations: Mapping[str, str] | None = None, dates: Mapping[str, str] | None = None) -> Checked:
    """一次扫描模型原文；只有对照表里无歧义的代号才还原，标记位置按输出计算。"""
    text = text or ""
    lookup: dict[tuple[str, str], set[str]] = {}
    exact: dict[str, str] = {}
    assistant: dict[str, str] = {}
    for code, value in mapping.items():
        if not code or "#" in code or code.startswith('@'):
            continue
        if NEUTRAL.fullmatch(code):
            assistant.update((variant, value) for variant in _variants(code))
            continue
        identity = _identity(code)
        if identity and identity[0] != "shared":
            lookup.setdefault(identity, set()).add(value)
            exact[code] = value

    chunks: list[str] = []
    marks: list[Mark] = []
    cursor = size = restored = fixed = removed = 0
    tokens = [(m.start(), m.end(), m.group(), 'code') for m in _candidates().finditer(text)]
    tokens = [t for t in tokens if not (t[2][0] in _LEFT and t[2][-1] in _RIGHT
              and _identity(t[2]) is None and not NEUTRAL.fullmatch(t[2]) and t[2] not in assistant
              and not (assistant and re.fullmatch(r'\[?(?:OUR|[A-Z]+_\d+)\]?', t[2]))
              and not _LOCATION.fullmatch(t[2]) and t[2] != '【主体说明】' and not t[2].startswith('【报价明细表：'))]
    if dates is not None:
        from app.redaction.dates import find_dates
        tokens += [(h.start, h.end, text[h.start:h.end], 'date') for h in find_dates(text)]
        if dates:
            paired = re.compile(r'(?<![\dA-Za-z])(?:' + '|'.join(re.escape(k) for k in sorted(dates, key=len, reverse=True))
                                + r')(?![\dA-Za-z])')
            tokens += [(m.start(), m.end(), m.group(), 'date') for m in paired.finditer(text)]
    for start, end, raw, token in sorted(set(tokens), key=lambda t: (t[0], -t[1], t[3])):
        if start < cursor:
            continue
        prefix = text[cursor:start]
        chunks.append(prefix)
        size += len(prefix)
        replacement, kind = raw, None
        bracketed = raw[0] in _LEFT and raw[-1] in _RIGHT
        if token == 'date':
            if raw in dates:
                replacement = dates[raw]
                restored += 1
            else:
                kind = 'date'
        elif re.fullmatch(_RATIO, raw):
            if clause:
                replacement = ""
                removed += 1
        elif raw == "【主体说明】" or raw.startswith("【报价明细表："):
            kind = "note" if clause else None
        elif _LOCATION.fullmatch(raw):
            loc = raw.strip("[]")
            if not clause and locations is not None and loc in locations:
                replacement = locations[loc]
            else:
                kind = "note"
        elif raw in assistant:
            replacement = assistant[raw]
            restored += 1
        elif NEUTRAL.fullmatch(raw) or re.fullmatch(r"[\[［【]\[(?:OUR|[A-Z]+_\d+)\][\]］】]", raw):
            kind = "unknown"
        elif (assistant and re.fullmatch(r"\[?(?:OUR|[A-Z]+_\d+)\]?", raw)
              and _identity(raw) not in lookup):
            code = "[[" + raw.strip("[]") + "]]"
            kind = "variant" if code in mapping else "unknown"
        else:
            identity = _identity(raw)
            if identity:
                family, _ = identity
                values = lookup.get(identity, set())
                if family == "shared":
                    kind = "shared" if clause else None
                elif family == "our" and not bracketed and "我方" not in exact:
                    pass
                elif raw in exact:
                    replacement = exact[raw]
                    restored += 1
                elif not bracketed and "_" not in unicodedata.normalize("NFKC", raw):
                    if values and (family == "role" or clause):
                        kind = "variant"
                elif len(values) == 1:
                    replacement = next(iter(values))
                    restored += 1
                    fixed += 1
                else:
                    # 新旧键归一后若指向不同真实值，保留并标出，不任取一个。
                    kind = "variant" if values else "unknown"
        chunks.append(replacement)
        if kind:
            marks.append(Mark(size, size + len(raw), kind, raw))
        size += len(replacement)
        cursor = end
    chunks.append(text[cursor:])
    return Checked("".join(chunks), tuple(marks), restored, fixed, removed)
