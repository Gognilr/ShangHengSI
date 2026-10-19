"""分段（SPEC 11.5，设计 docs/design/M5.md 第二节）。纯函数，不碰数据库，不写日志。

外发文本超过分段长度时，按条款或标题边界把正文行切成多段；每段都带完整的审核背景、审核要求和主体说明（由 prompt.py 拼接）。
- 长度按字符数计，只计正文行（含行首编号和换行），不计【主体说明】等。
- 首选切分点：每个文件的第一行、标题、一级条款（由调用方给出 boundary_locs）。
- 贪心：从一个切分点到下一个切分点之前为一个单元，能放进当前段就放入，放不下另起一段；
  单元本身超过上限时按行切开；单行超过上限时独占一段（不在行内切开）。
"""
import re
from dataclasses import dataclass

MIN_LIMIT = 1000  # 设置值低于此值时按此值，防止误配置把文件切成上千段
DEFAULT_LIMIT = 60000
_LINE_LOC = re.compile(r"^\[([^\]]+)\]")


@dataclass(frozen=True)
class OutLine:
    loc: str  # 行首方括号内的位置编号（节点 loc，或报价明细表汇总行的表格位置）
    text: str  # 整行文字（含 “[loc] ” 前缀），与外发文本中的行逐字相同
    boundary: bool  # 是否是首选切分点


@dataclass(frozen=True)
class Chunk:
    index: int  # 从 1 开始
    total: int
    lines: list[str]  # 本段的正文行（不含【主体说明】）
    first_loc: str
    last_loc: str
    locs: frozenset[str]  # 本段全部行的位置编号（结果校验只保留本段内的位置）


def effective_limit(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    if n <= 0:
        n = DEFAULT_LIMIT
    return max(MIN_LIMIT, n)


def outbound_lines(body: str, boundary_locs: set[str]) -> list[OutLine]:
    """外发文本正文（不含【主体说明】行）→ 逐行。正文每行都以 “[loc] ” 开头（outbound.node_line）。"""
    out = []
    for line in body.split("\n"):
        if not line:
            continue
        m = _LINE_LOC.match(line)
        loc = m.group(1) if m else ""
        out.append(OutLine(loc, line, loc in boundary_locs))
    return out


def _size(line: OutLine) -> int:
    return len(line.text) + 1  # 含换行


def split_chunks(lines: list[OutLine], limit: int) -> list[Chunk]:
    """正文总长不超过 limit 时只有 1 段；没有正文时也返回 1 个空段。"""
    groups: list[list[OutLine]] = []
    if sum(_size(x) for x in lines) <= limit:
        groups = [list(lines)]
    else:
        units: list[list[OutLine]] = []
        for line in lines:
            if line.boundary or not units:
                units.append([])
            units[-1].append(line)
        current: list[OutLine] = []
        size = 0
        for unit in units:
            usize = sum(_size(x) for x in unit)
            if size + usize <= limit:
                current += unit
                size += usize
                continue
            if current:
                groups.append(current)
                current, size = [], 0
            if usize <= limit:
                current, size = list(unit), usize
                continue
            for line in unit:  # 单元过长：按行切开
                if current and size + _size(line) > limit:
                    groups.append(current)
                    current, size = [], 0
                current.append(line)
                size += _size(line)
        if current:
            groups.append(current)
    total = len(groups)
    return [Chunk(index=i, total=total, lines=[x.text for x in g], first_loc=g[0].loc if g else "",
                  last_loc=g[-1].loc if g else "", locs=frozenset(x.loc for x in g))
            for i, g in enumerate(groups, start=1)]
