"""代号还原（SPEC 9.9，设计 docs/design/M2.md 第六节）。

模型返回的问题说明、修改建议、对话回复在展示和导出时把代号换回真实值；数据库中始终保存代号形式。
- 一次扫描完成全部替换，长代号优先（“客户_AB”不会被当成“客户_A”+“B”），已换上的真实值不会被再次替换；
- 以字母或数字结尾的代号后面紧跟字母、数字、下划线时不算（“人员_1”不误伤“人员_12”）；
- 方括号代号也接受全角方括号写法（模型常把 [金额_1] 写成 ［金额_1］）；
- 对照表中“代号#序号”形式的键只用于出口复检，不参与还原。
"""
import re
from functools import lru_cache


def _variants(code: str) -> list[str]:
    if code.startswith("[") and code.endswith("]"):
        return [code, "［" + code[1:-1] + "］"]
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
        if "#" in code or not code:
            continue
        for variant in _variants(code):
            lookup[variant] = value
    pattern = _pattern(tuple(sorted(lookup)))
    if pattern is None:
        return text
    return pattern.sub(lambda m: lookup[m.group()], text)
