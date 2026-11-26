"""多稿对比（V35-D-02，设计 docs/v35/design/diff.md 第二、三节）。

本机、确定性、不调模型；对比结果不存，打开时现算（3.3）。输入两稿的正文节点原文，输出变化列表。
返回界面的变化含原文，只给本人；日志只记会话编号、文件序号与计数。

留痕判断（3.2）：本稿“接受修订”文字 A、“拒绝修订”文字 B（doc_nodes.text_before_enc，没有值即 B = A）、上一稿 P。
B→A 是对方留了修订痕迹的改动，P→B 是没留痕的改动；P→A 的每一处插入 / 删除，落在 B→A 范围内才算“已留痕”。
"""
import difflib
import logging
import re
import time
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import timedelta
from itertools import groupby
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import DocNode, Task, TaskFile
from app.parsing.docx import MULTILEVEL_NUMBER, WARN_COMMENTS, WARN_EMBEDDED, WARN_NOTES
from app.security import crypto
from app.security.errors import AppError, NotFoundError

logger = logging.getLogger(__name__)

MOD_RATIO = 0.6  # 替换块内配对为“修改”的相似度
LINK_RATIO = 0.6  # 自动认稿：逐段相似度
LINK_SHARE = 0.5  # 相同或高度相似的段落占新文件的比例
MERGE_GAP = 2  # 相邻差异碎片间隔不超过 2 字时并成一段
_PUNCT = str.maketrans({'，': ',', '。': '.', '；': ';', '：': ':', '（': '(', '）': ')', '“': '"', '”': '"',
                        '‘': "'", '’': "'", '、': ',', '【': '[', '】': ']', '《': '<', '》': '>', '！': '!',
                        '？': '?', '—': '-', '－': '-', '～': '~', '…': '.'})
_WS = re.compile(r'\s+')
_CHINESE_NUM = '一二三四五六七八九十百千零〇两'
# 编号在去空白、替换标点之前识别，避免拆开小数和千分位。
_LEAD_NO = re.compile(
    rf'^\s*(第[\d{_CHINESE_NUM}]+[条章节款项]|{MULTILEVEL_NUMBER}|'
    rf'\d{{1,3}}\.(?![\d.%‰])|\d{{1,3}}、|\([\d{_CHINESE_NUM}]{{1,6}}\)|[{_CHINESE_NUM}]{{1,6}}[、.])'
)
_NUMBER = re.compile(r'[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[%‰]?')
_PARTY = re.compile(r'^(甲方|乙方|丙方|买方|卖方|采购方|供应方|供方|需方)(?:\([^)]{1,8}\))?[:=]')
REGIONS = {'header': '页眉', 'footer': '页脚', 'textbox': '文本框'}
COMPARE_PARTS = ('header', 'body', 'textbox', 'footer')
_DRAFT_WORDS = re.compile(r'(?:第[一二三四五六七八九十\d]+稿|[一二三四五六七八九十\d]稿|[vV]\d+(?:\.\d+)*|'
                          r'修改版|修订版|修改稿|修订稿|最终版|终稿|定稿|新版|初稿|草稿|对方修改|对方意见|回复版|反馈版|'
                          r'\d{4}[-._年]?\d{1,2}[-._月]?\d{1,2}日?|\d{6,8}|[(（]\d{1,2}[)）]|副本|copy)', re.I)


def normalize(text: str) -> str:
    return _WS.sub('', unicodedata.normalize('NFKC', text or '').translate(_PUNCT))


def key(text: str) -> str:
    """对齐键：去段首手写编号，再规范化正文。"""
    return normalize(_without_number(text)[1]) or normalize(text)


def _without_number(text: str) -> tuple[str, str]:
    value = unicodedata.normalize('NFKC', text or '')
    match = _LEAD_NO.match(value)
    return (match[1], value[match.end():]) if match else ('', value)


def _meaning(text: str) -> tuple[str, list[str]]:
    """忽略标点与空格，保留数字的符号、小数点、千分位、比例符号及数字间的分隔。"""
    value = unicodedata.normalize('NFKC', text)
    words = ''.join(c for c in value if not c.isspace() and not unicodedata.category(c).startswith('P'))
    return words, _NUMBER.findall(value)


def _ratio(a: str, b: str) -> float:
    if not a or not b or 2 * min(len(a), len(b)) < MOD_RATIO * (len(a) + len(b)):
        return 0.0
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return m.ratio() if m.real_quick_ratio() >= MOD_RATIO and m.quick_ratio() >= MOD_RATIO else 0.0


def _ops(a: str, b: str):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()


# ---------------------------------------------------------------- 留痕判断

class _Trace:
    """P→A 的一处差异是否落在 B→A（留痕）范围内。tracked=None：非 Word 文件，没有修订概念。"""

    def __init__(self, p: str, a: str, b: str | None, mode: str, *, literal: bool = False):
        self.mode = mode  # exact / flag / none
        if mode != 'exact':
            return
        self.ins_a = [False] * len(a)
        self.p2b: list[int | None] = [None] * len(p)
        if b is None or b == a:
            self.del_b = []
            return
        self.del_b = [False] * len(b)
        diff = _ops if literal else _token_ops
        for tag, i1, i2, j1, j2 in diff(b, a):
            if tag in ('insert', 'replace'):
                self.ins_a[j1:j2] = [True] * (j2 - j1)
            if tag in ('delete', 'replace'):
                self.del_b[i1:i2] = [True] * (i2 - i1)
        for tag, i1, i2, j1, j2 in diff(p, b):
            if tag == 'equal':
                for k in range(i2 - i1):
                    self.p2b[i1 + k] = j1 + k

    def segments(self, text, start, end, *, inserted, has_revision):
        if self.mode != 'exact':
            flag = {} if self.mode == 'none' else {'u': not has_revision}
            yield {'t': 'ins' if inserted else 'del', 's': text[start:end], **flag}
            return
        marks = self.ins_a[start:end] if inserted else [
            mapped is not None and self.del_b[mapped] for mapped in self.p2b[start:end]
        ]
        for marked, group in groupby(zip(text[start:end], marks), key=lambda item: item[1]):
            yield {'t': 'ins' if inserted else 'del', 's': ''.join(c for c, _ in group), 'u': not marked}


_TOKEN = re.compile(r'[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]+(?:[.,，．][0-9０-９]+)*[%％‰]?|.', re.S)


def _token_ops(p: str, a: str):
    """字级差异，数字与英文按整词比（“30 → 60”“0.5% → 0.1%”而不是单个数字），返回字符区间。"""
    tp, ta = [m.span() for m in _TOKEN.finditer(p)], [m.span() for m in _TOKEN.finditer(a)]
    sp, sa = [p[x:y] for x, y in tp], [a[x:y] for x, y in ta]
    at = lambda spans, k, text: spans[k][0] if k < len(spans) else len(text)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sp, sa, autojunk=False).get_opcodes():
        yield tag, at(tp, i1, p), at(tp, i2, p), at(ta, j1, a), at(ta, j2, a)


def _segments(p: str, a: str, trace: _Trace, has_revision: bool, *, literal: bool = False) -> list[dict]:
    """字级差异；精确判断时保留每一段的留痕边界。"""
    groups = []
    for tag, i1, i2, j1, j2 in (_ops if literal else _token_ops)(p, a):
        if (tag != 'equal' and trace.mode != 'exact' and not literal and len(groups) >= 2 and groups[-1][0] == 'equal'
                and groups[-1][2] - groups[-1][1] <= MERGE_GAP and groups[-2][0] != 'equal'):
            _, start_old, _, start_new, _ = groups[-2]
            groups[-2:] = [('replace', start_old, i2, start_new, j2)]
        else:
            groups.append((tag, i1, i2, j1, j2))
    segs = []
    for tag, i1, i2, j1, j2 in groups:
        if tag == 'equal':
            segs.append({'t': 'eq', 's': a[j1:j2]})
            continue
        if i2 > i1:
            segs.extend(trace.segments(p, i1, i2, inserted=False, has_revision=has_revision))
        if j2 > j1:
            segs.extend(trace.segments(a, j1, j2, inserted=True, has_revision=has_revision))
    return segs


def _brief(segs: list[dict]) -> dict:
    """一句摘录：变化前的 8 字上下文、旧文字、新文字（各最多 30 字）。"""
    ctx, old, new, seen = '', '', '', False
    for seg in segs:
        if seg['t'] == 'eq':
            if seen:
                break
            ctx = seg['s'][-8:]
        else:
            seen = True
            if seg['t'] == 'del':
                old += seg['s']
            else:
                new += seg['s']
    cut = lambda s: s if len(s) <= 30 else s[:30] + '…'
    return {'ctx': ctx, 'old': cut(old), 'new': cut(new)}


# ---------------------------------------------------------------- 对比

def _labels(nodes) -> list[str]:
    """所属条款：节点的条款号，没有时取前面最近的一级条款号；PDF、图片为页码。"""
    out, nearest = [], ''
    for n in nodes:
        region = _region_location(n)
        if region:
            out.append(region)
            continue
        clause = (n.clause_no or '').strip()
        if re.fullmatch(r'\d{1,3}[.、．]?', clause):
            clause = f'第 {clause.rstrip(".、．")} 条'
        if clause and '.' not in clause.strip('.'):
            nearest = clause
        if clause:
            label = f'{nearest} {clause}' if nearest and nearest != clause and re.fullmatch(r'[（(]?[\d一二三四五六七八九十a-zA-Z]+[）).、]?', clause) else clause
        elif n.page:
            label = f'第 {n.page} 页'
        else:
            label = nearest
        if n.kind == 'table_cell':
            label = (label + ' 表格').strip()
        out.append(label)
    return out


def _region_location(node) -> str:
    part = getattr(node, 'part', 'body')
    if part not in REGIONS:
        return ''
    number = re.search(r'\d+$', node.loc)
    return f'{REGIONS[part]}第 {number[0]} 段' if number else REGIONS[part]


def _alignment_key(node) -> tuple[bool, str]:
    return node.kind == 'table_cell', key(node.text)


def _score(old_key, new_key, old_no, new_no) -> float:
    if old_key[0] != new_key[0]:
        return 0.0
    a, b = old_key[1], new_key[1]
    role_a, role_b = _PARTY.match(a), _PARTY.match(b)
    if role_a and role_b:
        return 1.0 if role_a[1] == role_b[1] else 0.0
    score = _ratio(a, b)
    if score < MOD_RATIO and (old_no and old_no == new_no or
                              _NUMBER.search(a) and _NUMBER.sub('#', a) == _NUMBER.sub('#', b)):
        return MOD_RATIO
    return score


def _optimal_pairs(old_keys, new_keys, old_no, new_no, i1, i2, j1, j2) -> list[tuple[int, int]]:
    """顺序不交叉，选择相似度总和最高的替换配对。只存当前两行分数和回溯方向。"""
    width = j2 - j1
    previous = [0] * (width + 1)
    directions = []
    for i in range(i1, i2):
        current = [0] * (width + 1)
        path = bytearray(width)
        directions.append(path)
        for k, j in enumerate(range(j1, j2)):
            score = _score(old_keys[i], new_keys[j], old_no[i], new_no[j])
            best = current[k]
            if previous[k + 1] > best:
                best, path[k] = previous[k + 1], 1
            if score >= MOD_RATIO:
                paired = previous[k] + round(1000 * score)
                if paired > best:
                    best, path[k] = paired, 2
            current[k + 1] = best
        previous = current
    result = []
    i, j = i2 - i1 - 1, width - 1
    while i >= 0 and j >= 0:
        direction = directions[i][j]
        if direction == 2:
            result.append((i1 + i, j1 + j))
        if direction:
            i -= 1
        if direction != 1:
            j -= 1
    return result[::-1]


def _moves(old_keys, new_keys, ordered, deleted, added) -> dict[int, int]:
    """只在原有顺序被打破时找移位。两侧有多个近似候选时按增删显示。"""
    old_counts, new_counts = Counter(old_keys), Counter(new_keys)
    positions = [i for i, _ in ordered]
    exact = {new_keys[j]: j for j in added if new_counts[new_keys[j]] == 1}
    found, near_old, near_new = {}, defaultdict(list), defaultdict(list)
    for i, _ in deleted:
        k = bisect_left(positions, i)
        left = ordered[k - 1][1] if k else -1
        right = ordered[k][1] if k < len(ordered) else len(new_keys)
        token = old_keys[i]
        if old_counts[token] != 1:
            continue
        j = exact.get(token)
        if j is not None and (j < left or j > right):
            found[i] = j
            continue
        a = token[1]
        for j in added:
            cell, b = new_keys[j]
            if left < j < right or cell != token[0] or new_counts[new_keys[j]] != 1:
                continue
            if min(len(a), len(b)) < 3 or (max(len(a), len(b)) > 20 and a[:4] != b[:4] and a[-4:] != b[-4:]):
                continue
            ratio = _ratio(a, b)
            if ratio >= 2 / 3:
                near_old[i].append((ratio, j))
                near_new[j].append((ratio, i))

    def best(scores):
        scores = sorted(scores, reverse=True)
        return scores[0][1] if scores and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.12) else None

    exact_targets = set(found.values())
    for i, scores in near_old.items():
        j = best(scores)
        if j is not None and j not in exact_targets and best(near_new[j]) == i:
            found[i] = j
    return found


_CELL_SLOT = re.compile(r"^F\d+-(.+)-R\d+-C(\d+)$")


def _literal_cell_moves(old, new, old_keys, new_keys, pairs):
    old_counts, new_counts = Counter(old_keys), Counter(new_keys)
    by_old, by_new, cells = defaultdict(set), defaultdict(set), []
    for i, j in pairs:
        p = _CELL_SLOT.fullmatch(old[i].loc) if old[i].kind == "table_cell" else None
        n = _CELL_SLOT.fullmatch(new[j].loc) if new[j].kind == "table_cell" else None
        if not p or not n:
            continue
        if old_keys[i] == new_keys[j] and old_counts[old_keys[i]] == new_counts[new_keys[j]] == 1:
            by_old[p[1]].add(n[1])
            by_new[n[1]].add(p[1])
        if p[2] != n[2]:
            cells.append((i, j, p[1], n[1]))
    moved, ambiguous = set(), set()
    for i, j, old_table, new_table in cells:
        if (old_counts[old_keys[i]] == new_counts[new_keys[j]] == 1
                and by_old[old_table] == {new_table} and by_new[new_table] == {old_table}):
            moved.add((i, j))
        else:
            ambiguous.add((i, j))
    return moved, ambiguous


def _number_value(text: str) -> tuple[str, int] | None:
    match = re.fullmatch(rf'(.*?)(\d+|[{_CHINESE_NUM}]+)([条章节款项、.)]?)', text)
    if not match:
        return None
    prefix, number, suffix = match.groups()
    if number.isdecimal():
        return prefix + '#' + suffix, int(number)
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    units = {'十': 10, '百': 100, '千': 1000}
    if all(c in digits for c in number):
        return prefix + '中' + suffix, int(''.join(str(digits[c]) for c in number))
    value = current = 0
    for c in number:
        if c in units:
            value += (current or 1) * units[c]
            current = 0
        else:
            current = digits[c]
    return prefix + '中' + suffix, value + current


def _numbering_runs(changes, old_no, new_no, added):
    """前插条款导致的连续同幅顺延只列一条，其余编号变化仍逐处显示。"""
    by_pos = {(c['old_index'], c['order']): c for c in changes if c.get('detail_type') == 'numbering'}
    for (i, j), first in by_pos.items():
        if first.get('grouped'):
            continue
        p, n = _number_value(old_no[i]), _number_value(new_no[j])
        if not p or not n or p[0] != n[0]:
            continue
        shift = n[1] - p[1]
        if shift < 1 or j < shift or any(k not in added or _number_value(new_no[k]) != (p[0], n[1] - j + k)
                                         for k in range(j - shift, j)):
            continue
        members = []
        for k in range(1, min(len(old_no) - i, len(new_no) - j)):
            member = by_pos.get((i + k, j + k))
            if (not member or _number_value(old_no[i + k]) != (p[0], p[1] + k)
                    or _number_value(new_no[j + k]) != (n[0], n[1] + k)):
                break
            member['grouped'] = True
            members.append({key: member[key] for key in ('prev_loc', 'loc', 'segs')})
        if members:
            first['numbering_members'] = members
            first['note'] = f'{old_no[i]}起编号顺延 {1 + len(members)} 处'
    return [c for c in changes if not c.get('grouped')]


def _visibility(p, n):
    old = getattr(p, 'hidden_spans', None) or []
    new = getattr(n, 'hidden_spans', None) or []
    if not old and not new:
        return None
    if p.text == n.text and old == new:
        return None
    revealed = concealed = False
    old_mask, new_mask = bytearray(len(p.text)), bytearray(len(n.text))
    trace_mask = bytearray(len(n.text))
    for spans, mask in ((old, old_mask), (new, new_mask), (getattr(n, 'visibility_revisions', ()), trace_mask)):
        for start, end in spans:
            mask[start:end] = b'\x01' * (end - start)
    status = bytearray(len(n.text))
    for tag, i1, i2, j1, j2 in _ops(p.text, n.text):
        if tag != 'equal':
            continue
        for j, (a, b) in enumerate(zip(old_mask[i1:i2], new_mask[j1:j2]), j1):
            if a != b:
                revealed |= a > b
                concealed |= a < b
                status[j] = 1 if trace_mask[j] else 2
    if not (revealed or concealed):
        return None
    label = '隐藏和显示范围有变化' if revealed and concealed else '隐藏文字改为显示' if revealed else '文字改为隐藏'
    return label, status


def _visibility_segments(text: str, status: bytearray, track: bool) -> list[dict]:
    out = []
    for mark, group in groupby(zip(text, status), key=lambda item: item[1]):
        part = ''.join(c for c, _ in group)
        if not mark:
            out.append({'t': 'eq', 's': part})
        else:
            flag = {'u': mark == 2} if track else {}
            out.extend([{'t': 'del', 's': part, **flag}, {'t': 'ins', 's': part, **flag}])
    return out


def _before_text(node):
    return getattr(node, 'text_before', getattr(node, 'before', None))


def _compare_part(old, new, *, mode, literal, compact_numbering):
    deleted_revisions = [n for n in new if not n.text and _before_text(n)]
    old, new = [n for n in old if n.text], [n for n in new if n.text]
    old_keys, new_keys = [_alignment_key(n) for n in old], [_alignment_key(n) for n in new]
    old_no, new_no = [_without_number(n.text)[0] for n in old], [_without_number(n.text)[0] for n in new]
    old_labels, new_labels = _labels(old), _labels(new)
    pairs, added, deleted = [], [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes():
        if tag == 'equal':
            pairs.extend(zip(range(i1, i2), range(j1, j2)))
        elif tag == 'delete':
            deleted.extend((i, j1) for i in range(i1, i2))
        elif tag == 'insert':
            added.extend(range(j1, j2))
        else:
            i, j = i1, j1
            for k, l in _optimal_pairs(old_keys, new_keys, old_no, new_no, i1, i2, j1, j2):
                deleted.extend((idx, j) for idx in range(i, k))
                added.extend(range(j, l))
                pairs.append((k, l))
                i, j = k + 1, l + 1
            deleted.extend((idx, j) for idx in range(i, i2))
            added.extend(range(j, j2))

    cell_moves, ambiguous = _literal_cell_moves(old, new, old_keys, new_keys, pairs) if literal else (set(), set())
    if ambiguous:
        pairs = [(i, j) for i, j in pairs if (i, j) not in ambiguous]
        deleted.extend(sorted(ambiguous))
        added.extend(j for _, j in sorted(ambiguous))
    ambiguous_old, ambiguous_new = {i for i, _ in ambiguous}, {j for _, j in ambiguous}
    moves = _moves(old_keys, new_keys, pairs, [(i, at) for i, at in deleted if i not in ambiguous_old],
                   [j for j in added if j not in ambiguous_new])
    changes = []

    def node_trace(p, n):
        return _Trace(p, n.text, _before_text(n), mode, literal=literal)

    for i, j in [*pairs, *moves.items()]:
        p, n = old[i], new[j]
        visibility = _visibility(p, n)
        change = {'type': 'mod', 'loc': n.loc, 'prev_loc': p.loc, 'clause': new_labels[j], 'order': j, 'old_index': i}
        if (i, j) in cell_moves or moves.get(i) == j:
            change.update(type='move', prev_clause=old_labels[i])
        elif p.text == n.text:
            if not visibility:
                continue
            change['detail_type'] = 'visibility'
        elif _meaning(_without_number(p.text)[1]) == _meaning(_without_number(n.text)[1]):
            if old_no[i] != new_no[j]:
                change.update(detail_type='numbering', prev_clause=old_labels[i])
            elif not visibility:
                change['type'] = 'format'
        change['segs'] = _segments(p.text, n.text, node_trace(p.text, n), n.has_revision, literal=literal)
        if visibility:
            change['visibility'], status = visibility
            marks = _visibility_segments(n.text, status, mode == 'exact' and literal)
            if change.get('detail_type') == 'visibility':
                change['segs'] = marks
            else:
                change['visibility_segs'] = [{'s': s['s'], 'u': s['u']} for s in marks if s['t'] == 'ins' and 'u' in s]
        if change['type'] == 'move' and p.text == n.text and mode != 'none':
            existed_after_reject = _before_text(n) is None or _before_text(n) == n.text
            tracked = n.has_revision if mode == 'flag' else not existed_after_reject and _deleted_tracked(
                p, new, dict(deleted).get(i, j), mode, deleted_revisions)
            change['untracked'] = not tracked
        changes.append(change)
    moved = set(moves.values())
    for j in added:
        if j in moved:
            continue
        n = new[j]
        changes.append({'type': 'add', 'loc': n.loc, 'prev_loc': None, 'clause': new_labels[j],
                        'segs': list(node_trace('', n).segments(n.text, 0, len(n.text), inserted=True,
                                                               has_revision=n.has_revision)), 'order': j})
    for i, at in deleted:
        if i in moves:
            continue
        t = _deleted_tracked(old[i], new, at, mode, deleted_revisions)
        changes.append({'type': 'del', 'loc': None, 'prev_loc': old[i].loc,
                        'anchor': new[at].loc if at < len(new) else (new[-1].loc if new else None),
                        'after': at >= len(new),
                        'clause': old_labels[i], 'segs': [{'t': 'del', 's': old[i].text, **({} if t is None else {'u': not t})}],
                        'order': at - 0.5 + i * 1e-6})
    if compact_numbering:
        changes = _numbering_runs(changes, old_no, new_no, set(added) - moved)
    changes.sort(key=lambda c: c['order'])
    loc_map = {old[i].loc: new[j].loc for i, j in [*pairs, *moves.items()]}
    return changes, loc_map


def compare(old: list, new: list, *, mode: str = 'exact', with_map: bool = False, literal: bool = False,
            include_regions: bool = False, compact_numbering: bool = False) -> dict:
    """old / new：上一稿、本稿的正文节点（loc、text、before、kind、clause_no、page、has_revision）。
    mode：exact（本稿有“拒绝修订”文字）/ flag（旧版本解析，按节点修订标记）/ none（非 Word，不判断留痕）。
    with_map：另返回 map {上一稿位置: 本稿位置}（未变、修改、位置调整的段落），供增量复审换算位置（D-03）。
    literal：一次性合同对比按原始字符高亮。
    compact_numbering：对比栏合并顺延。增量复审仍逐处取变化位置。"""
    started = time.perf_counter()
    changes, loc_map = [], {}
    parts = COMPARE_PARTS if include_regions else ('body',)
    for part in parts:
        a, b = ([n for n in nodes if getattr(n, 'part', 'body') == part] for nodes in (old, new))
        # 会话历史只存了正文的拒绝修订文字；即时比两份 Word 可以判断所有分区。
        c, mapping = _compare_part(a, b, mode=mode if part == 'body' or literal else 'none', literal=literal,
                                   compact_numbering=compact_numbering)
        if c and not any(n.text for n in b):
            next_nodes = [n for n in new if n.text and getattr(n, 'part', 'body') in parts[parts.index(part) + 1:]]
            prev_nodes = [n for n in new if n.text and getattr(n, 'part', 'body') in parts[:parts.index(part)]]
            anchor = next_nodes[0].loc if next_nodes else prev_nodes[-1].loc if prev_nodes else None
            for change in c:
                change.update(anchor=anchor, after=not next_nodes)
        changes.extend(c)
        loc_map.update(mapping)
    summary = {'mod': 0, 'add': 0, 'del': 0, 'move': 0, 'format': 0, 'untracked': 0}
    for number, c in enumerate(changes, 1):
        c['id'] = f'C{number}'
        c.pop('order')
        c.pop('old_index', None)
        flags = [s['u'] for s in c['segs'] if 'u' in s]
        flags.extend(s['u'] for s in c.get('visibility_segs', ()))
        for member in c.get('numbering_members', ()):
            flags.extend(s['u'] for s in member['segs'] if 'u' in s)
        if 'untracked' in c:
            flags.append(c['untracked'])
        c['untracked'] = any(flags) if flags else None
        c['mixed'] = bool(flags) and any(flags) and not all(flags)
        edited = any(s['t'] != 'eq' for s in c['segs'])
        if c.get('note') or c.get('detail_type') == 'visibility':
            c['brief'] = {'text': c.get('note') or c['visibility'], 'ctx': '', 'old': '', 'new': ''}
        elif c['type'] in ('mod', 'format') or (c['type'] == 'move' and edited):
            c['brief'] = _brief(c['segs'])
        else:
            text = c['segs'][0]['s']
            c['brief'] = {'ctx': '', 'old': '', 'new': '', 'text': text[:40] + ('…' if len(text) > 40 else '')}
        summary[c['type']] += 1
        if c['untracked'] and c['type'] != 'format':
            summary['untracked'] += 1
    logger.info('多稿对比 old=%d new=%d changes=%d ms=%d', len(old), len(new), len(changes),
                (time.perf_counter() - started) * 1000)
    out = {'changes': changes, 'summary': summary, 'elapsed_ms': round((time.perf_counter() - started) * 1000)}
    if with_map:
        out['map'] = loc_map
    return out


def _deleted_tracked(p, new: list, at: int, mode: str, deleted_revisions=()) -> bool | None:
    """整段删除：原文应在附近节点的拒绝修订文字中，接受修订后已经消失。"""
    if mode == 'none':
        return None
    target = normalize(p.text)
    for n in [*new[max(0, at - 3): at + 3], *deleted_revisions]:
        if mode == 'flag':
            if n.has_revision:
                return True
            continue
        before = _before_text(n)
        if before is not None and target and target in normalize(before) and target not in normalize(n.text):
            return True
    return False


# ---------------------------------------------------------------- 稿次关系与数据

def chain_info(files: list[TaskFile]) -> dict:
    """{文件 id: (第几稿, 链的第一稿 id)}：只有在链上的文件（有上一稿或下一稿）才列出。"""
    by_id = {f.id: f for f in files}
    has_next = {f.prev_file_id for f in files if f.prev_file_id in by_id}
    out = {}
    for f in files:
        if f.prev_file_id not in by_id and f.id not in has_next:
            continue
        no, head, seen = 1, f, {f.id}
        while head.prev_file_id in by_id and head.prev_file_id not in seen:
            head = by_id[head.prev_file_id]
            seen.add(head.id)
            no += 1
        out[f.id] = (no, head.id)
    return out


def superseded(files: list[TaskFile]) -> set:
    """已有下一稿的文件：发起审核时默认不勾选（第二节）。"""
    ids = {f.id for f in files}
    return {f.prev_file_id for f in files if f.prev_file_id in ids}


def body_nodes(db: Session, file: TaskFile, *, include_regions: bool = False) -> list | None:
    """增量复审只读正文；打开会话对比时另读三个分区。原文已清理时返回 None。"""
    parts = COMPARE_PARTS if include_regions else ('body',)
    rows = db.execute(select(DocNode.loc, DocNode.kind, DocNode.clause_no, DocNode.page, DocNode.has_revision,
                             DocNode.text_enc, DocNode.text_before_enc, DocNode.part, DocNode.hidden_spans)
                      .where(DocNode.file_id == file.id, DocNode.part.in_(parts)).order_by(DocNode.ord)).all()
    if any(r.text_enc is None for r in rows):
        return None
    out = []
    for r in rows:
        text = crypto.decrypt_text(r.text_enc)
        if not text.strip():
            continue
        node = SimpleNamespace(loc=r.loc, kind=r.kind, clause_no=r.clause_no, page=r.page,
                               hidden_spans=r.hidden_spans, has_revision=r.has_revision, text=text,
                               before=crypto.decrypt_text(r.text_before_enc) if r.text_before_enc is not None else None)
        if include_regions:
            node.part = r.part
        out.append(node)
    return out


def _mode(file: TaskFile) -> str:
    if file.ext not in ('docx', 'doc', 'wps', 'rtf'):
        return 'none'
    return 'exact' if (file.parser_version or '').split('+')[0] >= 'docx-3' else 'flag'


def _files(db: Session, task: Task) -> list[TaskFile]:
    return list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))


def diff(db: Session, task: Task, file: TaskFile) -> dict:
    """GET /assistant/sessions/{id}/diff：本稿与上一稿的变化。"""
    files = _files(db, task)
    by_id = {f.id: f for f in files}
    prev = by_id.get(file.prev_file_id)
    if prev is None:
        raise NotFoundError('NO_PREVIOUS_DRAFT', '这份文件没有上一稿')
    if not file.parser_version or not prev.parser_version:
        raise AppError(409, 'DRAFT_NOT_READY', '文件还在解析，解析完成后可以对比')
    old, new = body_nodes(db, prev, include_regions=True), body_nodes(db, file, include_regions=True)
    if old is None or new is None:
        raise AppError(409, 'TEXT_UNAVAILABLE', '原文已按保留期清理，不能对比')
    mode = _mode(file)
    result = compare(old, new, mode=mode, include_regions=True, compact_numbering=True)
    omitted = {warning for f in (prev, file) for warning in (f.warnings or [])}
    omitted_labels = [label for warning, label in ((WARN_COMMENTS, '批注'), (WARN_NOTES, '脚注或尾注')) if warning in omitted]
    if any(isinstance(warning, str) and warning.startswith(WARN_EMBEDDED.split('{count}')[0]) for warning in omitted):
        omitted_labels.append('图片或嵌入对象')
    chains = chain_info(files)
    logger.info('多稿对比 task=%s file=F%d prev=F%d changes=%d untracked=%d', task.id, file.seq, prev.seq,
                len(result['changes']), result['summary']['untracked'])
    return {**result, 'file_id': str(file.id), 'prev_file_id': str(prev.id), 'prev_name': prev.original_name,
            'no': chains.get(file.id, (2,))[0], 'prev_no': chains.get(prev.id, (1,))[0],
            'trace': mode, 'ocr': any(n.kind == 'ocr_block' for n in new + old), 'omitted': omitted_labels}


def _share(old_keys: list[str], new_keys: list[str]) -> float:
    """新文件中与上一稿相同或高度相似（≥ 0.6）的段落所占比例。"""
    if not new_keys:
        return 0.0
    hit = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes():
        if tag == 'equal':
            hit += j2 - j1
        elif tag == 'replace':
            pool = old_keys[i1:i2]
            for k in new_keys[j1:j2]:
                if any(_ratio(o, k) >= LINK_RATIO for o in pool[:40]):
                    hit += 1
    return hit / len(new_keys)


def auto_link(db: Session, task: Task, file: TaskFile) -> None:
    """新文件解析完：与会话里各条链的最新一稿比，像就认作那一稿的下一稿（第二节）。只在本机、只比本会话。"""
    if task.purpose != 'chat' or file.media or file.prev_file_id is not None:
        return
    files = _files(db, task)
    nexts = superseded(files)
    new = body_nodes(db, file)
    if not new:
        return
    new_keys = [key(n.text) for n in new]
    best, best_share = None, LINK_SHARE
    for cand in files:
        if cand.id == file.id or cand.media or not cand.parser_version or cand.id in nexts or cand.seq > file.seq:
            continue
        old = body_nodes(db, cand)
        if not old:
            continue
        share = _share([key(n.text) for n in old], new_keys)
        if share > best_share:
            best, best_share = cand, share
    if best is not None:
        file.prev_file_id = best.id
        db.flush()
        logger.info('自动认稿 task=%s file=F%d prev=F%d', task.id, file.seq, best.seq)


def latest_of(files: list[TaskFile], start: TaskFile) -> TaskFile:
    """沿链走到最新一稿。"""
    by_prev = {f.prev_file_id: f for f in files if f.prev_file_id is not None}
    current, seen = start, {start.id}
    while current.id in by_prev and by_prev[current.id].id not in seen:
        current = by_prev[current.id]
        seen.add(current.id)
    return current


def set_prev(db: Session, task: Task, file: TaskFile, prev_id) -> None:
    """PUT …/files/{fid}/draft：改对照哪一稿，或作为独立文件（None）。只影响对比与审核范围，不改脱敏结果。"""
    files = _files(db, task)
    by_id = {f.id: f for f in files}
    if prev_id is None:
        file.prev_file_id = None
        db.flush()
        return
    prev = by_id.get(prev_id)
    if prev is None or prev.id == file.id or prev.media or file.media:
        raise AppError(422, 'VALIDATION_ERROR', '所选上一稿不正确')
    # 不能成环：上一稿不能是本稿的后续稿
    cursor, seen = prev, set()
    while cursor is not None and cursor.id not in seen:
        if cursor.id == file.id:
            raise AppError(422, 'VALIDATION_ERROR', '所选上一稿不正确')
        seen.add(cursor.id)
        cursor = by_id.get(cursor.prev_file_id)
    other = next((f for f in files if f.prev_file_id == prev.id and f.id != file.id), None)
    if other is not None:  # 一稿只有一个下一稿：原来的下一稿改为独立文件
        other.prev_file_id = None
        db.flush()
    file.prev_file_id = prev.id
    db.flush()


def name_stem(name: str) -> str:
    stem = (name or '').rsplit('.', 1)[0]
    stem = _DRAFT_WORDS.sub('', unicodedata.normalize('NFKC', stem))
    return re.sub(r'[\s_\-—–·.,，、()（）\[\]【】]+', '', stem).lower()


def draft_hint(db: Session, task: Task, files: list[TaskFile]) -> dict | None:
    """跨会话提示（第二节）：本会话主文件不在链上，而本人近 30 天另一个会话的主文件名去掉稿次字样后相同。只比文件名。"""
    main = next((f for f in files if f.is_main and not f.media), None)
    if task.purpose != 'chat' or main is None or main.prev_file_id is not None or main.id in superseded(files):
        return None
    stem = name_stem(main.original_name)
    if len(stem) < 2:
        return None
    rows = db.execute(select(Task.id, Task.title, TaskFile.original_name)
                      .join(TaskFile, TaskFile.task_id == Task.id)
                      .where(Task.owner_id == task.owner_id, Task.kind == 'assistant', Task.purpose == 'chat',
                             Task.id != task.id, TaskFile.is_main.is_(True), TaskFile.created_at < main.created_at,
                             Task.last_activity_at >= utcnow() - timedelta(days=30))
                      .order_by(Task.last_activity_at.desc()).limit(50)).all()
    for row in rows:
        if name_stem(row.original_name) == stem:
            return {'session_id': str(row.id), 'title': row.title, 'file_name': row.original_name}
    return None
