"""金额比例（SPEC 9.6，设计 docs/design/M4.md 2.5，Q-72）。

纯函数，不碰数据库：确定合同总价、计算比例文字、给出比例文字的插入位置。
比例文字只进入 redacted_text，不进入对照表和 redaction_items；写入由 engine 的 _assemble 完成（M4-04）。
全部用 Decimal，ROUND_HALF_UP。
"""
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from app.redaction.detect import _CODES, Hit

# 合同总价标签：长的在前，英文不区分大小写
TOTAL_LABEL = re.compile(
    r"合同总价|合同总额|合同金额|总金额|总价|(?<![A-Za-z])(?i:total\s+amount|total\s+value|contract\s+value|"
    r"contract\s+price|contract\s+amount)(?![A-Za-z])"
)
_LABEL_REACH = 30  # 标签之后多少个字符内的第一个金额算合同总价
_TRAILING_CURRENCY = re.compile(rf"\s?(?:万美元|万元|亿元|美元|欧元|英镑|日元|港元|港币|元|(?:{_CODES})(?![A-Za-z]))")
_PERCENT = Decimal("0.1")
_PERCENT_SIGN = re.compile(r"\d\s*[%％‰]")
_HAN = re.compile(r"[一-鿿]")


def _label_counts(text: str, m: re.Match[str], first_start: int) -> bool:
    """标签是否算合同总价（R1-05，设计 R1 第四节）。

    1. 标签与金额之间有百分比或“的”时不算（“总价的 30%，即人民币 X 元”）。
    2. “总价、总金额”前紧挨着“合同”以外的汉字时不算（“软件总价款”“硬件总价”）；“合同总额”“固定总价”等不受影响。
    """
    between = text[m.end():first_start]
    if "的" in between or _PERCENT_SIGN.search(between):
        return False
    if m.group() in ("总价", "总金额") and m.start() > 0:
        prev = text[m.start() - 1]
        if _HAN.match(prev) and text[max(m.start() - 2, 0):m.start()] not in ("合同", "固定"):
            return False
    return True


class _Node(Protocol):
    loc: str
    text: str


@dataclass(frozen=True)
class AmountAt:
    node: int  # 节点序号（compute 中 nodes 的下标）
    hit: Hit  # category=AMOUNT


def _usable(hit: Hit) -> bool:
    return hit.category == "AMOUNT" and hit.currency is not None and hit.value is not None and hit.value > 0


def contract_total(nodes: list[_Node], amounts: list[AmountAt], main_prefix: str | None) -> tuple[str, Decimal] | None:
    """合同总价 (币种, 数值)；无法确定时 None（Q-72）。

    1. 标签法：TOTAL_LABEL 之后 30 个字符内的第一个金额。先在主文件（loc 以 main_prefix 开头）的节点中找，取文档顺序第一个；
       主文件中没有时再在其他文件中找。该金额没有币种或不大于 0 时，这个标签不算。
    2. 没有任何可用的标签时：所有带币种的金额（大于 0）是同一币种时取其中最大的；有多个币种时不确定，返回 None。
    """
    by_node: dict[int, list[Hit]] = {}
    for a in amounts:
        if a.hit.category == "AMOUNT":
            by_node.setdefault(a.node, []).append(a.hit)

    def labelled(in_main: bool) -> tuple[str, Decimal] | None:
        for idx in sorted(by_node):
            if main_prefix is not None and nodes[idx].loc.startswith(main_prefix) != in_main:
                continue
            hits = sorted(by_node[idx], key=lambda h: h.start)
            for m in TOTAL_LABEL.finditer(nodes[idx].text):
                first = next((h for h in hits if h.start >= m.end()), None)
                if first is not None and first.start - m.end() <= _LABEL_REACH and _usable(first) \
                        and _label_counts(nodes[idx].text, m, first.start):
                    return first.currency, first.value  # type: ignore[return-value]
        return None

    found = labelled(True) or (labelled(False) if main_prefix is not None else None)
    if found:
        return found
    usable = [h for hits in by_node.values() for h in hits if _usable(h)]
    currencies = {h.currency for h in usable}
    if len(currencies) == 1:
        return usable[0].currency, max(h.value for h in usable)  # type: ignore[return-value,type-var]
    return None


def applies(hit: Hit, total: tuple[str, Decimal] | None) -> bool:
    """这个金额是否要加比例：AMOUNT、币种与合同总价相同、数值已知，且不是合同总价本身（同币种同数值即同一代号）。"""
    if total is None or hit.category != "AMOUNT" or hit.value is None or hit.currency != total[0]:
        return False
    return total[1] > 0 and hit.value != total[1]


def ratio_text(value: Decimal, total: Decimal) -> str:
    """“（=合同总价的 30.0%）”：保留 1 位小数，ROUND_HALF_UP；大于 100% 照常写。"""
    if total <= 0:
        raise ValueError("合同总价必须大于 0")
    pct = (value / total * 100).quantize(_PERCENT, rounding=ROUND_HALF_UP)
    return f"（=合同总价的 {pct:f}%）"


def insert_position(text: str, end: int) -> int:
    """金额数字结束于 end；数字之后紧跟后置币种（美元、元、USD…）时，比例文字插在币种之后。"""
    m = _TRAILING_CURRENCY.match(text, end)
    return m.end() if m else end
