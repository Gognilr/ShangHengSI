"""相似段落（bid.md 3.1，第四十条（四））：句级归一化、二元组 Dice、剔除招标原文、相邻命中合并成段。

候选对用前缀过滤（按全局稀有度排序的二元组前缀建倒排），不漏掉 Dice 达标的句对，又不必两两比较全部句子。
只在内存比较；结果只写摘录（加密），日志只写数量。
"""
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from app.redaction.normalize import normalize_value

MIN_CHARS = 20  # 20 字以下的句子不比
TENDER_DICE = 0.9  # 与招标文件句子 Dice ≥ 0.9 视为照抄招标文件
PAIR_DICE = 0.85  # 两家之间 Dice ≥ 0.85 视为近似句
STRONG_RUN = 200  # 自写部分连续相同 ≥ 200 字为强
STRONG_EXACT = 5  # 只有两家共有的完全相同句 ≥ 5 处为强
MEDIUM_RUN = 80  # 合并后的段 ≥ 80 字为“长段相似”（中）
MAP_SEGS = 300  # 位置条最多画的段数
_SPLIT = re.compile(r"[^。；;！!？?\n]+[。；;！!？?]?")
_STRIP = re.compile(r"[\W_]+")
_LAW = re.compile(r"《[^《》]{2,40}(?:法|条例|规定|办法|细则|解释)》|第[一二三四五六七八九十百零\d]+条")
_CHAPTER = re.compile(r"^\s*(?:第[一二三四五六七八九十百零\d]+[章部分篇卷节]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.．\s])\s*(.{1,30})$")
_TECH = re.compile(r"技术参数|技术规格|技术偏离|技术指标|规格参数|产品参数|技术响应|配置清单|参数响应")
_PRICE = re.compile(r"报价|开标一览表|价格|分项|投标函")
# 公共来源文字（V34-B-08c）：审计报告标准意见、专利证书固定文字、营业执照套话、信用公示网址、标准编号行。
# 降权不排除：所在段降一档，不计入“只有两家共有的相同句”；自填参数、偏离与共同错字不受影响。
_COMMON = re.compile(r"注册会计师|审计准则|审计证据|审计意见|财务报表|重大错报|治理层|职业道德守则|持续经营|内部控制"
                     r"|专利权|专利登记|专利法|实用新型|知识产权局|专利证书"
                     r"|依法须经批准的项目|国家企业信用信息公示系统|gsxt\.gov\.cn"
                     r"|^\s*(?:GB|HG|SH|JB|NB|API|ISO|IEC)\s*/?\s*T?\s*\d")
_UP = {"weak": "medium", "medium": "strong", "strong": "strong"}
_DOWN = {"strong": "medium", "medium": "weak", "weak": "weak"}
_HAN_PAIR = re.compile(r"[一-鿿]{2}")
SHARED_TYPO = "同句同错"


@dataclass
class Sent:
    owner: object  # 投标人 id；招标文件为 None
    index: int  # 在本家句子序列中的序号（用于判断相邻）
    file_id: object
    loc: str
    page: int | None
    text: str  # 原文句子
    norm: str
    grams: frozenset
    section: str  # body / tech / price
    owner_rank: int = 0
    common: bool = False  # 公共来源文字（审计、专利、执照套话、标准编号）
    ocr: bool = False


def _sections(nodes):
    current, out = "body", []
    for _f, _l, _p, text, kind, *_ in nodes:
        title = text.strip()
        m = _CHAPTER.match(title) if len(title) <= 40 else None
        if m or (kind == "heading" and len(title) <= 40):
            name = m.group(1) if m else title
            current = "tech" if _TECH.search(name) else "price" if _PRICE.search(name) else "body"
        out.append(current)
    return out


def sentences(owner, nodes, start=0):
    """[Sent]：nodes 为 [(file_id, loc, page, text, kind)]；只保留归一化后 ≥ 20 字且不是法条引文的句子。"""
    out, index = [], start
    for (file_id, loc, page, text, _kind, *extra), section in zip(nodes, _sections(nodes)):
        for m in _SPLIT.finditer(text):
            piece = m.group().strip()
            if not piece or _LAW.search(piece):
                continue
            norm = _STRIP.sub("", normalize_value(piece))
            if len(norm) < MIN_CHARS:
                continue
            grams = frozenset(norm[i:i + 2] for i in range(len(norm) - 1))
            out.append(Sent(owner, index, file_id, loc, page, piece, norm, grams, section,
                            common=bool(_COMMON.search(piece)), ocr=bool(extra and extra[0] is not None)))
            index += 1
    return out


def dice(a, b):
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


class _Index:
    """前缀过滤：两句 Dice ≥ d 时，按全局稀有度排序后的前缀必有共同二元组（|A∩B| ≥ d·|A|/(2-d)）。"""

    def __init__(self, sents, threshold):
        freq = defaultdict(int)
        for s in sents:
            for g in s.grams:
                freq[g] += 1
        self.rank = lambda g: (freq.get(g, 0), g)
        self.d = threshold
        self.post = defaultdict(list)
        for i, s in enumerate(sents):
            for g in self.prefix(s.grams):
                self.post[g].append(i)

    def prefix(self, grams):
        need = math.ceil(self.d * len(grams) / (2 - self.d) - 1e-9)
        return sorted(grams, key=self.rank)[:max(1, len(grams) - need + 1)]

    def candidates(self, grams):
        found = set()
        for g in self.prefix(grams):
            found.update(self.post.get(g, ()))
        return found


def _drop_tender(bid_sents, tender_sents):
    """剔除与招标文件完全一致或 Dice ≥ 0.9 的句子（格式页照抄、条款复述）。"""
    exact = {s.norm for s in tender_sents}
    rest = [s for s in bid_sents if s.norm not in exact]
    if not tender_sents or not rest:
        return rest, len(bid_sents) - len(rest)
    # 用两侧合并的频率建索引：同一全局顺序下前缀过滤才不漏
    pool = tender_sents + rest
    index = _Index(pool, TENDER_DICE)
    n = len(tender_sents)
    index.post = {g: [i for i in ids if i < n] for g, ids in index.post.items()}
    kept = [s for s in rest if not any(dice(s.grams, tender_sents[i].grams) >= TENDER_DICE for i in index.candidates(s.grams))]
    return kept, len(bid_sents) - len(kept)


def _pairs(sents):
    """跨投标人的命中句对：{(a, b): [(sa, sb, exact)]}，a、b 按录入顺序。"""
    by_norm = defaultdict(list)
    for s in sents:
        by_norm[s.norm].append(s)
    hits = defaultdict(dict)  # (a, b) → {(ia, ib): (sa, sb, exact)}

    def put(x, y, exact):
        if x.owner == y.owner:
            return
        if x.owner_rank > y.owner_rank:
            x, y = y, x
        key = (x.index, y.index)
        slot = hits[(x.owner, y.owner)]
        if key not in slot or exact:
            slot[key] = (x, y, exact)

    for group in by_norm.values():
        for x, y in combinations(group, 2):
            put(x, y, True)
    index = _Index(sents, PAIR_DICE)
    for i, s in enumerate(sents):
        for j in index.candidates(s.grams):
            if j <= i:
                continue
            t = sents[j]
            if t.owner != s.owner and t.norm != s.norm and dice(s.grams, t.grams) >= PAIR_DICE:
                put(s, t, False)
    return {k: list(v.values()) for k, v in hits.items()}


def _segments(matches):
    """相邻命中合并成段：两侧序号都只前进 1～2 句时并入同一段。"""
    matches = sorted(matches, key=lambda m: (m[0].index, m[1].index))
    segs = []
    for m in matches:
        last = segs[-1][-1] if segs else None
        if last and 0 < m[0].index - last[0].index <= 2 and 0 < m[1].index - last[1].index <= 2:
            segs[-1].append(m)
        else:
            segs.append([m])
    return segs


def _place(s):
    return {"file_id": str(s.file_id), "loc": s.loc, "page": s.page}


def _map(a, b, graded, totals, starts):
    """相似段落位置条：两家各一条轴（按句序，文件依次排列），每段给出两侧的句序范围与页码范围。"""
    def side(seg, k):
        ss = [m[k] for m in seg]
        pages = [s.page for s in ss if s.page is not None]
        return [min(s.index for s in ss), max(s.index for s in ss)], [min(pages), max(pages)] if pages else None, str(ss[0].file_id)
    segs = []
    for lv, length, _section, seg, _common in sorted(graded, key=lambda g: min(m[0].index for m in g[3]))[:MAP_SEGS]:
        (ia, pa, fa), (ib, pb, fb) = side(seg, 0), side(seg, 1)
        segs.append({"level": lv, "chars": length, "a": ia, "b": ib, "pa": pa, "pb": pb, "fa": fa, "fb": fb})
    return {"n": {str(a): totals[a], str(b): totals[b]}, "files": {str(a): starts[a], str(b): starts[b]}, "segs": segs}


def _transposition(actual, expected):
    if len(actual) != len(expected):
        return None
    for start, (a, b) in enumerate(zip(actual, expected)):
        if a == b:
            continue
        end = start + 2
        word, correct = actual[start:end], expected[start:end]
        if word == correct[::-1] and _HAN_PAIR.fullmatch(word) and actual[end:] == expected[end:]:
            return word, correct
        break
    return None


def _shared_typos(tender, history, shared):
    """同一文本层句子、同一汉字倒置，且招标基线有唯一的对照写法。"""
    baseline = {s.norm for s in tender + history}
    groups = [list(by_owner.values()) for norm, by_owner in shared.items()
              if len(by_owner) >= 2 and norm not in baseline]
    if not groups:
        return []
    reference = [s for s in tender if not s.common and not s.ocr]
    if not reference:
        return []
    index = _Index(reference, 0.8)
    rows_by_pair = defaultdict(list)
    for group in groups:
        norm = group[0].norm
        grams = group[0].grams
        matches = {}
        for i in sorted(index.candidates(grams)):
            source = reference[i]
            if change := _transposition(norm, source.norm):
                matches.setdefault(source.norm, (source, change))
        if len(matches) != 1:
            continue
        source, (actual, correct) = next(iter(matches.values()))
        for x, y in combinations(group, 2):
            rows = rows_by_pair[x.owner, y.owner]
            if len(rows) < 12:
                rows.append({
                    "label": SHARED_TYPO, "level": "medium",
                    "value": f"两家同写「{actual}」，招标文件写作「{correct}」",
                    "left": {"bidder_id": str(x.owner), "places": [_place(x)], "text": x.text[:600]},
                    "right": {"bidder_id": str(y.owner), "places": [_place(y)], "text": y.text[:600]},
                    "places": {"tender": [_place(source) | {"excerpt": source.text[:600]}]},
                })
    return [{"ids": ids, "level": "medium", "title": "两家出现与招标文件不同的同句同错", "rows": rows}
            for ids, rows in rows_by_pair.items()]


def find(bidders, nodes, tender_nodes, *, history=()):
    """返回 (线索列表, 统计)。bidders 为按录入顺序的 id；nodes {id: [(file_id, loc, page, text, kind)]}。"""
    tender = sentences(None, tender_nodes)
    historic = sentences(None, history)
    baseline = tender + historic
    pool, dropped = [], 0
    shared = defaultdict(dict)
    totals, starts = {}, {}  # 位置条（V34-B-06d）：每家句子总数与各文件的起始句序号
    for b in bidders:
        mine = sentences(b, nodes.get(b, []))
        totals[b] = len(mine)
        starts[b] = []
        for x in mine:
            if not starts[b] or starts[b][-1][0] != str(x.file_id):
                starts[b].append([str(x.file_id), x.index])
            if not x.common and not x.ocr:
                shared[x.norm].setdefault(b, x)
        own, n = _drop_tender(mine, baseline)
        dropped += n
        pool += own
    for s in pool:
        s.owner_rank = bidders.index(s.owner)
    pairs = _pairs(pool)
    # 完全相同句被几家共有：只有两家共有的计入“强”的第二条
    holders = defaultdict(set)
    for s in pool:
        holders[s.norm].add(s.owner)
    items = []
    for (a, b), matches in pairs.items():
        segs = _segments(matches)
        only_two = sum(1 for x, _y, exact in matches if exact and len(holders[x.norm]) == 2 and not x.common)
        total = sum(len(x.norm) for x, _y, _e in matches)
        if total < MEDIUM_RUN and only_two < STRONG_EXACT:
            continue  # 零星短句相同（多为固定句式）不报，免得淹没真正的线索；章节升降档只用于够长的命中
        graded = []
        for seg in segs:
            length = sum(len(x.norm) for x, _y, _e in seg)
            level = "strong" if length >= STRONG_RUN else "medium" if length >= MEDIUM_RUN else "weak"
            section = seg[0][0].section
            if section == "tech":
                level = _DOWN[level]
            elif section == "price":
                level = _UP[level]
            common = sum(len(x.norm) for x, _y, _e in seg if x.common) * 10 >= length * 6
            if common:
                level = _DOWN[level]
            graded.append((level, length, section, seg, common))
        rank = {"strong": 3, "medium": 2, "weak": 1}
        level = max((g[0] for g in graded), key=rank.get)
        if only_two >= STRONG_EXACT:
            level = "strong"
        # 示例先列非公共来源的段落：降档后的审计、专利套话放在最后，不挤掉真正值得看的段落
        graded.sort(key=lambda g: (g[4], -rank[g[0]], -g[1]))
        rows = []
        for lv, length, section, seg, common in graded[:12]:
            rows.append({"label": "相似段落", "level": lv, "chars": length, "sentences": len(seg),
                         "section": section, "exact": all(e for *_x, e in seg), "common": common,
                         "left": {"bidder_id": str(a), "places": [_place(x) for x, _y, _e in seg][:6],
                                  "text": "".join(x.text for x, _y, _e in seg)[:600]},
                         "right": {"bidder_id": str(b), "places": [_place(y) for _x, y, _e in seg][:6],
                                   "text": "".join(y.text for _x, y, _e in seg)[:600]}})
        title = f"相似段落 {len(segs)} 段、约 {total} 字"
        if only_two:
            title += f"，只有这两家共有的相同句 {only_two} 处"
        if commons := sum(1 for g in graded if g[4]):
            title += f"；其中 {commons} 段为审计报告、专利证书、执照或标准条文等公共来源文字，已降一档"
        items.append({"ids": (a, b), "level": level, "title": title, "rows": rows,
                      "more": max(0, len(graded) - len(rows)), "map": _map(a, b, graded, totals, starts)})
    stats = {"sentences": len(pool), "dropped_tender": dropped}
    return items + _shared_typos(tender, historic, shared), stats
