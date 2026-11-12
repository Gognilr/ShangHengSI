"""只向本人返回可安全还原的前缀；offset 始终按还原后的字符位置计算。"""
from app.redaction.placeholders import NEUTRAL
from app.redaction.restore import NEUTRAL_CLOSE, NEUTRAL_OPEN, check


def safe_prefix(text: str, *, finished: bool = False) -> str:
    # 已结束的回复不会再有后续片段：未闭合的 [[ 原样显示，不能截掉其后的正文。
    if finished:
        return text
    # 与还原共用候选代号的括号文法（含外层括号、全角变体）：可能成为候选的未闭合前缀一律暂扣。
    openings = list(NEUTRAL_OPEN.finditer(text))
    if openings and not NEUTRAL_CLOSE.search(text, openings[-1].end()):
        return text[:openings[-1].start()]
    # 网络片段也可能正好拆在开头括号处，暂扣末尾的单个左括号以免下次前缀变短。
    if text and text[-1] in "[［【":
        return text[:-1]
    return text


def incremental(text: str, mapping: dict[str, str], offset: int, *, finished: bool = False) -> tuple[str, int]:
    usable = safe_prefix(text, finished=finished)
    neutral = {code: value for code, value in mapping.items() if NEUTRAL.fullmatch(code)}
    restored = check(usable, neutral).text
    return restored[offset:], len(restored)
