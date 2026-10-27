"""只向本人返回可安全还原的前缀；offset 始终按还原后的字符位置计算。"""
from app.redaction.placeholders import NEUTRAL
from app.redaction.restore import restore


def safe_prefix(text: str, *, finished: bool = False) -> str:
    opening = text.rfind("[[")
    if opening >= 0 and text.find("]]", opening + 2) < 0:
        return text[:opening]
    # 网络片段也可能正好拆在两个左括号之间，暂扣一个括号以免下次前缀变短。
    if not finished and text.endswith("["):
        return text[:-1]
    return text


def incremental(text: str, mapping: dict[str, str], offset: int, *, finished: bool = False) -> tuple[str, int]:
    usable = safe_prefix(text, finished=finished)
    neutral = {code: value for code, value in mapping.items() if NEUTRAL.fullmatch(code)}
    restored = restore(usable, neutral)
    return restored[offset:], len(restored)
