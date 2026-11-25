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
from collections import Counter, defaultdict
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import DocNode, Task, TaskFile
from app.security import crypto
from app.security.errors import AppError, NotFoundError

logger = logging.getLogger(__name__)

MOD_RATIO = 0.5  # 替换块内配对为“修改”的相似度
LINK_RATIO = 0.6  # 自动认稿：逐段相似度
LINK_SHARE = 0.5  # 相同或高度相似的段落占新文件的比例
MERGE_GAP = 2  # 相邻差异碎片间隔不超过 2 字时并成一段
_PUNCT = str.maketrans({'，': ',', '。': '.', '；': ';', '：': ':', '（': '(', '）': ')', '“': '"', '”': '"',
                        '‘': "'", '’': "'", '、': ',', '【': '[', '】': ']', '《': '<', '》': '>', '！': '!',
                        '？': '?', '—': '-', '－': '-', '～': '~', '…': '.'})
_WS = re.compile(r'\s+')
# 段首手写编号：第八条、8.3、(一)、一、1.；自动编号不在正文里（clause_no），本来就不比
_LEAD_NO = re.compile(r'^(?:第[0-9一二三四五六七八九十百千零〇两]+[条章节款项]|\d{1,3}(?:\.\d{1,3})*[.、)]?(?![\d%])|'
                      r'\([0-9一二三四五六七八九十]{1,3}\)|[一二三四五六七八九十]{1,3}[、.])')
_DRAFT_WORDS = re.compile(r'(?:第[一二三四五六七八九十\d]+稿|[一二三四五六七八九十\d]稿|[vV]\d+(?:\.\d+)*|'
                          r'修改版|修订版|修改稿|修订稿|最终版|终稿|定稿|新版|初稿|草稿|对方修改|对方意见|回复版|反馈版|'
                          r'\d{4}[-._年]?\d{1,2}[-._月]?\d{1,2}日?|\d{6,8}|[(（]\d{1,2}[)）]|副本|copy)', re.I)


def normalize(text: str) -> str:
    return _WS.sub('', unicodedata.normalize('NFKC', text or '').translate(_PUNCT))


def key(text: str) -> str:
    """对齐键：规范化后去掉段首手写编号。"""
    value = normalize(text)
    stripped = _LEAD_NO.sub('', value, count=1)
    return stripped or value


def _loose(text: str) -> str:
    return ''.join(c for c in key(text) if not unicodedata.category(c).startswith('P'))


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return m.ratio() if m.real_quick_ratio() >= MOD_RATIO and m.quick_ratio() >= MOD_RATIO else 0.0


def _ops(a: str, b: str):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()


# ---------------------------------------------------------------- 留痕判断

class _Trace:
    """P→A 的一处差异是否落在 B→A（留痕）范围内。tracked=None：非 Word 文件，没有修订概念。"""

    def __init__(self, p: str, a: str, b: str | None, mode: str):
        self.mode = mode  # exact / flag / none
        if mode != 'exact':
            return
        b = a if b is None else b
        self.ins_a = [False] * len(a)
        self.del_b = [False] * len(b)
        for tag, i1, i2, j1, j2 in _token_ops(b, a):
            if tag in ('insert', 'replace'):
                self.ins_a[j1:j2] = [True] * (j2 - j1)
            if tag in ('delete', 'replace'):
                self.del_b[i1:i2] = [True] * (i2 - i1)
        self.p2b: list[int | None] = [None] * len(p)
        for tag, i1, i2, j1, j2 in _token_ops(p, b):
            if tag == 'equal':
                for k in range(i2 - i1):
                    self.p2b[i1 + k] = j1 + k

    def tracked(self, a0, a1, b0, b1, has_revision=False) -> bool | None:
        """(a0, a1) 为上一稿 P 的区间，(b0, b1) 为本稿 A 的区间。"""
        if self.mode == 'none':
            return None
        if self.mode == 'flag':  # 旧版本解析没有“拒绝修订”文字：只能按节点是否有修订判断
            return has_revision
        if b1 > b0 and not all(self.ins_a[b0:b1]):
            return False
        return all(self.p2b[k] is not None and self.del_b[self.p2b[k]] for k in range(a0, a1))


_TOKEN = re.compile(r'[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]+(?:[.,，．][0-9０-９]+)*[%％‰]?|.', re.S)


def _token_ops(p: str, a: str):
    """字级差异，数字与英文按整词比（“30 → 60”“0.5% → 0.1%”而不是单个数字），返回字符区间。"""
    tp, ta = [m.span() for m in _TOKEN.finditer(p)], [m.span() for m in _TOKEN.finditer(a)]
    sp, sa = [p[x:y] for x, y in tp], [a[x:y] for x, y in ta]
    at = lambda spans, k, text: spans[k][0] if k < len(spans) else len(text)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sp, sa, autojunk=False).get_opcodes():
        yield tag, at(tp, i1, p), at(tp, i2, p), at(ta, j1, a), at(ta, j2, a)


def _segments(p: str, a: str, trace: _Trace, has_revision: bool, *, literal: bool = False) -> list[dict]:
    """字级差异：相邻碎片间隔 ≤ 2 字并成一段；每段带是否留痕（u = 未留痕）。"""
    groups: list[list] = []  # [tag, a0, a1, b0, b1, tracked]
    for tag, i1, i2, j1, j2 in (_ops if literal else _token_ops)(p, a):
        if tag == 'equal':
            groups.append(['equal', i1, i2, j1, j2, None])
            continue
        t = trace.tracked(i1, i2, j1, j2, has_revision)
        if (not literal and len(groups) >= 2 and groups[-1][0] == 'equal' and groups[-1][2] - groups[-1][1] <= MERGE_GAP
                and groups[-2][0] != 'equal'):
            groups.pop()
            prev = groups.pop()
            merged_t = None if t is None else bool(prev[5]) and t
            groups.append(['replace', prev[1], i2, prev[3], j2, merged_t])
        else:
            groups.append([tag, i1, i2, j1, j2, t])
    segs = []
    for tag, i1, i2, j1, j2, t in groups:
        if tag == 'equal':
            segs.append({'t': 'eq', 's': a[j1:j2]})
            continue
        extra = {} if t is None else {'u': not t}
        if i2 > i1:
            segs.append({'t': 'del', 's': p[i1:i2], **extra})
        if j2 > j1:
            segs.append({'t': 'ins', 's': a[j1:j2], **extra})
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


def _move_anchors(kind: str, text: str):
    yield kind, 'text', text
    # 足够长的相同段首或段尾用于找挪动后有修改的段落。
    if len(text) >= 14:
        yield kind, 'start', text[:8]
        yield kind, 'end', text[-8:]


def _literal_moves(old, new, deleted, added) -> dict[int, int]:
    """唯一对应且原文相近的段落可标为移位，其余按增删显示。"""
    index = defaultdict(list)
    new_text = {j: _loose(new[j].text) or key(new[j].text) for j in added}
    for j, text in new_text.items():
        for anchor in _move_anchors(new[j].kind, text):
            index[anchor].append(j)
    paired = {}
    for i, _ in deleted:
        text = _loose(old[i].text) or key(old[i].text)
        targets = set()
        for anchor in _move_anchors(old[i].kind, text):
            choices = index.get(anchor, ())
            if len(choices) == 1:
                targets.add(choices[0])
        if len(targets) == 1:
            j = next(iter(targets))
            if _ratio(text, new_text[j]) >= 0.8:
                paired[i] = j
    counts = Counter(paired.values())
    return {i: j for i, j in paired.items() if counts[j] == 1}


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


def compare(old: list, new: list, *, mode: str = 'exact', with_map: bool = False, literal: bool = False) -> dict:
    """old / new：上一稿、本稿的正文节点（loc、text、before、kind、clause_no、page、has_revision）。
    mode：exact（本稿有“拒绝修订”文字）/ flag（旧版本解析，按节点修订标记）/ none（非 Word，不判断留痕）。
    with_map：另返回 map {上一稿位置: 本稿位置}（未变、修改、位置调整的段落），供增量复审换算位置（D-03）。
    literal：一次性合同对比按原始字符高亮，位置调整也展示文字变化。"""
    started = time.perf_counter()
    old_keys, new_keys = [key(n.text) for n in old], [key(n.text) for n in new]
    old_labels, new_labels = _labels(old), _labels(new)
    pairs, added, deleted = [], [], []  # pairs: (i, j)；deleted: (i, 插在本稿第 j 个节点之前)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes():
        if tag == 'equal':
            pairs.extend(zip(range(i1, i2), range(j1, j2)))
        elif tag == 'delete':
            deleted.extend((i, j1) for i in range(i1, i2))
        elif tag == 'insert':
            added.extend(range(j1, j2))
        else:  # 替换块内按顺序贪心配对
            j = j1
            for i in range(i1, i2):
                best, best_j = 0.0, None
                for k in range(j, min(j2, j + 6)):
                    r = _ratio(old_keys[i], new_keys[k])
                    if r > best:
                        best, best_j = r, k
                if best_j is not None and best >= MOD_RATIO:
                    added.extend(range(j, best_j))
                    pairs.append((i, best_j))
                    j = best_j + 1
                else:
                    deleted.append((i, j))
            added.extend(range(j, j2))

    cell_moves, ambiguous = _literal_cell_moves(old, new, old_keys, new_keys, pairs) if literal else (set(), set())
    if ambiguous:
        pairs = [(i, j) for i, j in pairs if (i, j) not in ambiguous]
        deleted.extend(sorted(ambiguous))
        added.extend(j for _, j in sorted(ambiguous))

    changes = []

    def node_trace(i, j):
        n = new[j]
        return _Trace(old[i].text if i is not None else '', n.text, n.before if mode == 'exact' else None, mode)

    for i, j in pairs:
        p, n = old[i], new[j]
        if (i, j) in cell_moves:
            kind = 'move'
        elif p.text != n.text:
            kind = 'format' if _loose(p.text) == _loose(n.text) else 'mod'
        else:
            continue
        segs = _segments(p.text, n.text, node_trace(i, j), n.has_revision, literal=literal)
        changes.append({'type': kind, 'loc': n.loc, 'prev_loc': p.loc, 'clause': new_labels[j], 'segs': segs, 'order': j})
    # 会话对比沿用归一段落键，一次性对比较验有文字变化的唯一移位段落。
    by_key = defaultdict(list)
    if not literal:
        for j in added:
            by_key[new_keys[j]].append(j)
    ambiguous_old, ambiguous_new = {i for i, _ in ambiguous}, {j for _, j in ambiguous}
    literal_moves = _literal_moves(old, new, [(i, at) for i, at in deleted if i not in ambiguous_old],
                                   [j for j in added if j not in ambiguous_new]) if literal else {}
    moved = set()
    rest_deleted = []
    for i, at in deleted:
        candidates = by_key.get(old_keys[i]) if old_keys[i] else None
        j = literal_moves.get(i)
        if candidates:
            j = candidates.pop(0)
        if j is not None:
            moved.add(j)
            n = new[j]
            trace = node_trace(None, j)
            ins_t = trace.tracked(0, 0, 0, len(n.text), n.has_revision)
            t = None if ins_t is None else ins_t and _deleted_tracked(old[i], new, at, mode)
            segs = [{'t': 'eq', 's': n.text}]
            if literal:
                segs = _segments(old[i].text, n.text, node_trace(i, j), n.has_revision, literal=True)
            changes.append({'type': 'move', 'loc': n.loc, 'prev_loc': old[i].loc, 'clause': new_labels[j],
                            'prev_clause': old_labels[i], 'segs': segs,
                            'order': j, 'untracked': None if t is None else not t})
        else:
            rest_deleted.append((i, at))
    for j in added:
        if j in moved:
            continue
        n = new[j]
        trace = node_trace(None, j)
        t = trace.tracked(0, 0, 0, len(n.text), n.has_revision)
        changes.append({'type': 'add', 'loc': n.loc, 'prev_loc': None, 'clause': new_labels[j],
                        'segs': [{'t': 'ins', 's': n.text, **({} if t is None else {'u': not t})}], 'order': j})
    for i, at in rest_deleted:
        t = _deleted_tracked(old[i], new, at, mode)
        # anchor：挂在本稿哪一段之前；删在文末时挂在最后一段之后（after）
        changes.append({'type': 'del', 'loc': None, 'prev_loc': old[i].loc,
                        'anchor': new[at].loc if at < len(new) else (new[-1].loc if new else None),
                        'after': at >= len(new),
                        'clause': old_labels[i], 'segs': [{'t': 'del', 's': old[i].text, **({} if t is None else {'u': not t})}],
                        'order': at - 0.5 + i * 1e-6})
    changes.sort(key=lambda c: c['order'])
    loc_map = {old[i].loc: new[j].loc for i, j in pairs}
    loc_map.update({c['prev_loc']: c['loc'] for c in changes if c['type'] == 'move'})
    summary = {'mod': 0, 'add': 0, 'del': 0, 'move': 0, 'format': 0, 'untracked': 0}
    for n, c in enumerate(changes, 1):
        c['id'] = f'C{n}'
        c.pop('order')
        if 'untracked' not in c:
            flags = [s['u'] for s in c['segs'] if 'u' in s]
            c['untracked'] = (any(flags) if flags else None)
            c['mixed'] = bool(flags) and any(flags) and not all(flags)
        else:
            c['mixed'] = False
        moved_edit = literal and c['type'] == 'move' and any(s['t'] != 'eq' for s in c['segs'])
        c['brief'] = _brief(c['segs']) if c['type'] in ('mod', 'format') or moved_edit else {
            'ctx': '', 'old': '', 'new': '', 'text': (lambda s: s if len(s) <= 40 else s[:40] + '…')(c['segs'][0]['s'])}
        summary[c['type']] += 1
        if c['untracked'] and c['type'] != 'format':
            summary['untracked'] += 1
    logger.info('多稿对比 old=%d new=%d changes=%d ms=%d', len(old), len(new), len(changes),
                (time.perf_counter() - started) * 1000)
    out = {'changes': changes, 'summary': summary, 'elapsed_ms': round((time.perf_counter() - started) * 1000)}
    if with_map:
        out['map'] = loc_map
    return out


def _deleted_tracked(p, new: list, at: int, mode: str) -> bool | None:
    """整段删除是否留痕：删掉的文字出现在附近节点的“拒绝修订”文字里，且不在其接受后的文字里。"""
    if mode == 'none':
        return None
    target = key(p.text)
    for n in new[max(0, at - 3): at + 3]:
        if mode == 'flag':
            if n.has_revision:
                return True
            continue
        if n.before is not None and target and target in key(n.before) and target not in key(n.text):
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


def body_nodes(db: Session, file: TaskFile) -> list | None:
    """正文节点（含表格单元格、OCR 块）；原文已按保留期清理时为 None。"""
    rows = db.execute(select(DocNode.loc, DocNode.kind, DocNode.clause_no, DocNode.page, DocNode.has_revision,
                             DocNode.text_enc, DocNode.text_before_enc)
                      .where(DocNode.file_id == file.id, DocNode.part == 'body').order_by(DocNode.ord)).all()
    if any(r.text_enc is None for r in rows):
        return None
    out = []
    for r in rows:
        text = crypto.decrypt_text(r.text_enc)
        if not text.strip():
            continue
        out.append(SimpleNamespace(loc=r.loc, kind=r.kind, clause_no=r.clause_no, page=r.page,
                                   has_revision=r.has_revision, text=text,
                                   before=crypto.decrypt_text(r.text_before_enc) if r.text_before_enc is not None else None))
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
    old, new = body_nodes(db, prev), body_nodes(db, file)
    if old is None or new is None:
        raise AppError(409, 'TEXT_UNAVAILABLE', '原文已按保留期清理，不能对比')
    mode = _mode(file)
    result = compare(old, new, mode=mode)
    chains = chain_info(files)
    logger.info('多稿对比 task=%s file=F%d prev=F%d changes=%d untracked=%d', task.id, file.seq, prev.seq,
                len(result['changes']), result['summary']['untracked'])
    return {**result, 'file_id': str(file.id), 'prev_file_id': str(prev.id), 'prev_name': prev.original_name,
            'no': chains.get(file.id, (2,))[0], 'prev_no': chains.get(prev.id, (1,))[0],
            'trace': mode, 'ocr': any(n.kind == 'ocr_block' for n in new + old)}


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
