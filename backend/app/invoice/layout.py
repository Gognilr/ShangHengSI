"""按坐标套国家统一版式取值（设计第三节）：PDF 文字层与 OCR 共用。

输入是带坐标的文字段（PDF 词或 OCR 行），先按纵向中心聚成视觉行，行内按横坐标排好，再在“发票号码”
“开票日期”“名称”“价税合计”等锚点的同一行右侧或下一行取值。实测有一种版式的文字层顺序与标签不相邻，
所以只看坐标，不看文字层里的先后。
"""
import re
import statistics
import unicodedata
from dataclasses import dataclass, field

from app.invoice.model import CN_AMOUNT_CHARS, Invoice, Qr, norm_date, parse_money


@dataclass
class Seg:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    conf: float = 1.0

    @property
    def h(self) -> float:
        return max(self.bottom - self.top, 0.1)

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


def _disp_char(ch: str) -> str:
    """显示用：只把康熙部首、部首补充（部分 PDF 字体用它们代替常用汉字）归一为常用汉字。"""
    if "⺀" <= ch <= "⿟":
        n = unicodedata.normalize("NFKC", ch)
        return n if len(n) == 1 else ch
    return ch


def _norm_char(ch: str) -> str:
    """匹配用：再把全角字母数字与标点换成半角，￥ 换成 ¥。逐字一一对应，保证两串等长。"""
    ch = _disp_char(ch)
    if "！" <= ch <= "～" or ch in "￥　":
        n = unicodedata.normalize("NFKC", ch)
        return n if len(n) == 1 else ch
    return {"：": ":", "（": "(", "）": ")", "－": "-", "—": "-"}.get(ch, ch)


@dataclass
class Row:
    segs: list[Seg]
    disp: str = ""
    norm: str = ""
    owner: list[int] = field(default_factory=list)  # 每个字符所属的 segs 下标，分隔符为 -1

    @property
    def top(self) -> float:
        return min(s.top for s in self.segs)

    @property
    def bottom(self) -> float:
        return max(s.bottom for s in self.segs)

    @property
    def h(self) -> float:
        return statistics.median(s.h for s in self.segs)

    def x_at(self, i: int) -> float:
        """第 i 个字符的大致横坐标（按所在段等分）。"""
        k = self.owner[min(max(i, 0), len(self.owner) - 1)] if self.owner else -1
        if k < 0:
            j = i
            while j < len(self.owner) and self.owner[j] < 0:
                j += 1
            if j >= len(self.owner):
                return self.segs[-1].x1
            k, i = self.owner[j], j
        seg = self.segs[k]
        start = self.owner.index(k)
        n = max(len(seg.text), 1)
        return seg.x0 + (seg.x1 - seg.x0) * (i - start) / n

    def conf(self, start: int, end: int) -> float:
        ks = {self.owner[i] for i in range(start, min(end, len(self.owner))) if self.owner[i] >= 0}
        return min((self.segs[k].conf for k in ks), default=1.0)


def build_rows(segs: list[Seg]) -> list[Row]:
    """按纵向中心聚行：与当前行中心相差不超过半个字高即同一行。"""
    segs = [s for s in segs if s.text.strip()]
    if not segs:
        return []
    rows: list[list[Seg]] = []
    for s in sorted(segs, key=lambda s: (s.cy, s.x0)):
        if rows:
            cur = rows[-1]
            cy = statistics.median(x.cy for x in cur)
            h = statistics.median(x.h for x in cur)
            if abs(s.cy - cy) <= 0.5 * min(h, s.h) + 0.1:
                cur.append(s)
                continue
        rows.append([s])
    out = []
    for group in rows:
        group.sort(key=lambda s: s.x0)
        row = Row(group)
        h = row.h
        prev = None
        for k, s in enumerate(group):
            if prev is not None:
                gap = s.x0 - prev.x1
                sep = "\t" if gap > 1.5 * h else " " if gap > 0.25 * h else ""
                row.disp += sep
                row.norm += sep
                row.owner += [-1] * len(sep)
            text = s.text.replace("\t", " ")
            row.disp += "".join(_disp_char(c) for c in text)
            row.norm += "".join(_norm_char(c) for c in text)
            row.owner += [k] * len(text)
            prev = s
        out.append(row)
    return out


# ---------------------------------------------------------------- 锚点与取值

def _sp(word: str) -> str:
    """标签字间允许空白（宽字距的标签被拆成几个词）。"""
    return r"\s*".join(map(re.escape, word))


_CJK = r"一-鿿"
NUMBER_LABEL = re.compile(rf"(?<![{_CJK}]){_sp('发票号码')}\s*:?")
CODE_LABEL = re.compile(rf"(?<![{_CJK}]){_sp('发票代码')}\s*:?")
DATE_LABEL = re.compile(rf"(?:{_sp('开票日期')}|{_sp('填开日期')})\s*:?")
NAME_LABEL = re.compile(rf"(?<![目务物品行程票]){_sp('名称')}\s*:?")
TAX_LABEL = re.compile(rf"(?:{_sp('统一社会信用代码')}\s*/?\s*)?{_sp('纳税人识别号')}\s*:?|{_sp('统一社会信用代码')}\s*:?")
TOTAL_LABEL = re.compile(_sp("价税合计"))
SUM_LABEL = re.compile(rf"(?<!税)(?<!税\s){_sp('合计')}")
ITEM_HEADER = re.compile(rf"{_sp('项目名称')}|{_sp('货物或应税劳务')}")
REMARK_LABEL = re.compile(_sp("备注"))
CLERK_LABEL = re.compile(_sp("开票人"))
FARE_LABEL = re.compile(rf"(?<![航运]){_sp('票价')}\s*:?")
AIR_TAX_LABEL = re.compile(_sp("增值税税额"))
PARTY_LABELS = (NAME_LABEL, TAX_LABEL)

NUMBER_VALUE = re.compile(r"\d{8,20}")
CODE_VALUE = re.compile(r"\d{10,12}")
DATE_VALUE = re.compile(r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})")
TAX_VALUE = re.compile(r"[0-9A-Za-z]{15,20}")
MONEY = re.compile(r"(-)?\s*¥?\s*(-)?\s*(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?!\d)")
RATE = re.compile(r"\d{1,2}(?:\.\d+)?%|免税|不征税")

# 票种（标题去空白后匹配，先命中先定）
KINDS = (
    (re.compile(r"铁路电子客票"), "铁路电子客票"),
    (re.compile(r"航空运输电子客票行程单"), "航空运输电子客票行程单"),
    (re.compile(r"电子发票\(增值税专用发票\)"), "数电票（增值税专用发票）"),
    (re.compile(r"电子发票\(普通发票\)"), "数电票（普通发票）"),
    (re.compile(r"增值税电子专用发票"), "增值税电子专用发票"),
    (re.compile(r"通行费"), "增值税电子普通发票（通行费）"),
    (re.compile(r"增值税电子普通发票"), "增值税电子普通发票"),
    (re.compile(r"卷票"), "增值税普通发票（卷票）"),
    (re.compile(r"增值税专用发票"), "增值税专用发票"),
    (re.compile(r"增值税普通发票"), "增值税普通发票"),
)
QR_KINDS = {"01": "增值税专用发票", "04": "增值税普通发票", "08": "增值税电子专用发票", "10": "增值税电子普通发票",
            "11": "增值税普通发票（卷票）", "14": "增值税电子普通发票（通行费）",
            "31": "数电票（增值税专用发票）", "32": "数电票（普通发票）"}


@dataclass
class Hit:
    row: int
    start: int
    end: int


def _hits(rows: list[Row], pattern: re.Pattern) -> list[Hit]:
    return [Hit(r, m.start(), m.end()) for r, row in enumerate(rows) for m in pattern.finditer(row.norm)]


def _value_after(rows: list[Row], hit: Hit, value: re.Pattern, *, stop_labels=()) -> tuple[str, float] | None:
    """锚点同一行右侧的值；没有则在下一行（与锚点横向相近处）找。返回 (显示文字, 置信度)。"""
    row = rows[hit.row]
    tail = row.norm[hit.end:]
    end = len(tail)
    for p in stop_labels:
        m = p.search(tail)
        if m and m.start() > 0:
            end = min(end, m.start())
    tab = tail.find("\t", len(tail) - len(tail.lstrip(" \t:")))
    if tab >= 0:
        end = min(end, tab)
    m = value.match(tail[:end].lstrip(" \t:"))
    if m:
        off = hit.end + (len(tail[:end]) - len(tail[:end].lstrip(" \t:"))) + m.start()
        return row.disp[off: off + len(m.group(0))], row.conf(off, off + len(m.group(0)))
    if hit.row + 1 < len(rows):
        below = rows[hit.row + 1]
        if below.top - row.bottom < 1.5 * row.h:
            lx0 = row.x_at(hit.start) - 2 * row.h
            lx1 = row.x_at(hit.end) + 8 * row.h
            for m in value.finditer(below.norm):
                if lx0 <= below.x_at(m.start()) <= lx1:
                    return below.disp[m.start(): m.end()], below.conf(m.start(), m.end())
    return None


def _text_after(rows: list[Row], hit: Hit) -> tuple[str, float]:
    """锚点右侧到下一个名称、税号标签或大间隔为止的文字（名称类字段）。"""
    row = rows[hit.row]
    tail = row.norm[hit.end:]
    lead = len(tail) - len(tail.lstrip(" :	"))
    end = len(tail)
    for p in PARTY_LABELS:
        m = p.search(tail, lead)
        if m and m.start() >= lead:
            end = min(end, m.start())
    tab = tail.find("\t", lead)
    if tab >= 0:
        end = min(end, tab)
    s, e = hit.end + lead, hit.end + end
    text = row.disp[s:e].strip()
    if not text and hit.row + 1 < len(rows):
        below = rows[hit.row + 1]
        if below.top - row.bottom < 1.0 * row.h:
            lx0, lx1 = row.x_at(hit.start) - row.h, row.x_at(hit.end) + 8 * row.h
            parts = [(k, sg) for k, sg in enumerate(below.segs) if lx0 <= sg.x0 <= lx1]
            if parts:
                return parts[0][1].text.strip(), parts[0][1].conf
    return text, row.conf(s, e)


def _money_in(row: Row, start: int = 0) -> list[tuple[float, str, float, int]]:
    """行内 start 之后的金额：[(横坐标, 显示文字, 置信度, 结束位置)]。"""
    out = []
    for m in MONEY.finditer(row.norm, start):
        out.append((row.x_at(m.start()), m.group(0), row.conf(m.start(), m.end()), m.end()))
    return out


def detect_kind(rows: list[Row]) -> str:
    text = re.sub(r"\s+", "", "".join(r.norm for r in rows))
    for pattern, kind in KINDS:
        if pattern.search(text):
            return kind
    return ""


def looks_like_invoice(rows: list[Row]) -> bool:
    text = re.sub(r"\s+", "", "".join(r.norm for r in rows))
    return bool(re.search(r"发票|客票|行程单", text)) and bool(re.search(r"号码|合计|票价", text))


# ---------------------------------------------------------------- 一张票

def _conf_ok(conf: float, low_conf: float) -> bool:
    return conf >= low_conf


def read_invoice(rows: list[Row], *, source: str, low_conf: float) -> Invoice:
    """从一张票的视觉行中取各字段。OCR 来源记下低置信的字段。"""
    inv = Invoice(source=source, kind=detect_kind(rows))
    confs: dict[str, float] = {}

    def put(key: str, got: tuple[str, float] | None, clean=lambda s: s):
        if got is None:
            return
        text, conf = got
        value = clean(text)
        if value:
            setattr(inv, key, value)
            confs[key] = conf

    for hit in _hits(rows, NUMBER_LABEL)[:1]:
        put("number", _value_after(rows, hit, NUMBER_VALUE))
    for hit in _hits(rows, CODE_LABEL)[:1]:
        put("code", _value_after(rows, hit, CODE_VALUE))
    for hit in _hits(rows, DATE_LABEL)[:1]:
        got = _value_after(rows, hit, DATE_VALUE)
        if got:
            m = DATE_VALUE.search("".join(_norm_char(c) for c in got[0]))
            put("date", (norm_date(*m.groups()) or "", got[1]) if m else None)

    # 购买方、销售方：带“购买方 / 销售方”前缀的按前缀定；其余按先上后下、先左后右依次为购买方、销售方
    for pattern, key in ((NAME_LABEL, "name"), (TAX_LABEL, "tax")):
        hits = sorted(_hits(rows, pattern), key=lambda h: (h.row, h.start))
        free = ["buyer", "seller"]
        for hit in hits:
            prefix = re.sub(r"\s", "", rows[hit.row].norm[max(0, hit.start - 6): hit.start])
            party = "buyer" if re.search(r"购买?方$", prefix) else "seller" if re.search(r"销售?方$", prefix) else None
            if party is None:
                if not free:
                    break
                party = free[0]
            if party in free:
                free.remove(party)
            else:
                continue
            if key == "name":
                put(f"{party}_name", _text_after(rows, hit), lambda s: s.strip(" :\t"))
            else:
                got = _value_after(rows, hit, re.compile(r"[0-9A-Za-z][0-9A-Za-z ]{13,22}[0-9A-Za-z]"),
                                   stop_labels=PARTY_LABELS)
                if got:
                    tax = re.sub(r"\s", "", got[0]).upper()
                    if 15 <= len(tax) <= 20:
                        put(f"{party}_tax", (tax, got[1]))

    # 价税合计：（大写）……（小写）¥…
    total_rows = _hits(rows, TOTAL_LABEL)
    for hit in total_rows[:1]:
        row = rows[hit.row]
        small = row.norm.find("小写", hit.end)
        money = _money_in(row, small if small >= 0 else hit.end)
        if not money and hit.row + 1 < len(rows):
            money = _money_in(rows[hit.row + 1])
        if money:
            put("total", (money[0][1], money[0][2]), lambda s: s)
        big_end = small if small >= 0 else (row.norm.find("¥", hit.end) if "¥" in row.norm[hit.end:] else len(row.norm))
        cn = "".join(c for c in row.disp[hit.end:big_end] if c in CN_AMOUNT_CHARS or c in "负")
        cn = re.sub(r"^[^壹贰叁肆伍陆柒捌玖拾佰仟万亿零负]+", "", cn)
        if cn:
            inv.total_cn = cn

    # 合计行：金额在左、税额在右
    sum_hits = [h for h in _hits(rows, SUM_LABEL) if not TOTAL_LABEL.search(rows[h.row].norm)]
    if inv.kind == "航空运输电子客票行程单":
        for hit in sum_hits[:1]:
            money = _money_in(rows[hit.row], hit.end)
            if money:
                put("total", (money[0][1], money[0][2]))
        for hit in _hits(rows, AIR_TAX_LABEL)[:1]:
            money = _money_in(rows[hit.row], hit.end)
            if money:
                put("tax", (money[0][1], money[0][2]))
    else:
        for hit in sum_hits[:1]:
            row = rows[hit.row]
            money = sorted(_money_in(row, hit.end))
            if money:
                put("amount", (money[0][1], money[0][2]))
                if len(money) > 1:
                    put("tax", (money[1][1], money[1][2]))
                elif "*" in row.norm[money[0][3]:]:
                    inv.tax = parse_money("0.00")
                    confs["tax"] = money[0][2]
    if inv.total is None:
        for hit in _hits(rows, FARE_LABEL)[:1]:
            money = _money_in(rows[hit.row], hit.end)
            if money:
                put("total", (money[0][1], money[0][2]))

    for key in ("amount", "tax", "total"):
        raw = getattr(inv, key)
        if isinstance(raw, str):
            v = parse_money(raw)
            setattr(inv, key, v)
            if v is None:
                confs.pop(key, None)
    if inv.kind == "航空运输电子客票行程单" and inv.total is not None and inv.tax is not None and inv.amount is None:
        inv.amount = inv.total - inv.tax
        confs["amount"] = min(confs.get("total", 1), confs.get("tax", 1))

    _read_items(rows, inv, sum_hits[0].row if sum_hits else (total_rows[0].row if total_rows else len(rows)))
    _read_remark(rows, inv, total_rows[0].row if total_rows else -1)

    if source in ("scan", "photo"):
        inv.low = [k for k, c in confs.items() if not _conf_ok(c, low_conf)]
    return inv


def _read_items(rows: list[Row], inv: Invoice, end_row: int) -> None:
    heads = _hits(rows, ITEM_HEADER)
    if not heads:
        return
    head = heads[0]
    hrow = rows[head.row]
    # 项目名称一栏的右界：表头文字居中，取本栏表头末端与下一栏表头起点的中点
    k = max(hrow.owner[head.end - 1], 0)
    label_end = hrow.segs[k].x1
    right = (label_end + hrow.segs[k + 1].x0) / 2 if k + 1 < len(hrow.segs) else label_end + 10 * hrow.h
    items: list[str] = []
    rates: list[str] = []
    for row in rows[head.row + 1: end_row]:
        rates += [m.group(0) for m in RATE.finditer(row.norm)]
        cells = [s.text.strip() for s in row.segs if s.x0 < right and not MONEY.fullmatch(s.text.strip().replace("¥", ""))]
        text = "".join(cells).strip()
        if not text or ITEM_HEADER.search(text):
            continue
        if text.startswith("*") or not items:
            items.append(text)
        else:
            items[-1] += text
    inv.items = [i for i in items if not MONEY.fullmatch(i)]
    inv.rates = list(dict.fromkeys(rates))


def _read_remark(rows: list[Row], inv: Invoice, after_row: int) -> None:
    """备注：横排“备注”或竖排“备 / 注”右侧、价税合计之下、开票人之上的文字。"""
    label = None
    for hit in _hits(rows, REMARK_LABEL):
        row = rows[hit.row]
        label = (row.x_at(hit.end), row.top, row.bottom)
        break
    if label is None:  # 竖排：单独的“备”下方不远处有单独的“注”
        for r, row in enumerate(rows):
            for s in row.segs:
                if s.text.strip() != "备":
                    continue
                for row2 in rows[r + 1: r + 6]:
                    for s2 in row2.segs:
                        if s2.text.strip() == "注" and abs(s2.x0 - s.x0) < s.h and s2.top - s.bottom < 4 * s.h:
                            label = (max(s.x1, s2.x1), s.top, s2.bottom)
            if label:
                break
    if label is None:
        return
    x1, top, bottom = label
    h = statistics.median(s.h for r in rows for s in r.segs)
    parts = []
    for r, row in enumerate(rows):
        if r <= after_row or CLERK_LABEL.search(row.norm):
            if r > after_row and row.top > bottom:
                break
            continue
        if row.bottom < top - 2 * h or row.top > bottom + 2 * h:
            continue
        text = " ".join(s.text.strip() for s in row.segs if s.x0 >= x1 - 0.2 * h and s.text.strip() not in ("备", "注"))
        if text:
            parts.append(text)
    inv.remark = " ".join(parts).strip()


# ---------------------------------------------------------------- 一页多票

def split_page(segs: list[Seg], width: float) -> list[list[Seg]] | None:
    """一页多票按“发票号码”标签与标题位置切开；只有一张时返回 [segs]，切不开时返回 None。"""
    rows = build_rows(segs)
    hits = [h for h in _hits(rows, NUMBER_LABEL) if _value_after(rows, h, NUMBER_VALUE)]
    if len(hits) <= 1:
        return [segs]
    h = statistics.median(s.h for s in segs)
    anchors = sorted((rows[x.row].x_at(x.start), rows[x.row].top) for x in hits)
    tops = sorted(t for _, t in anchors)
    if all(b - a > 6 * h for a, b in zip(tops, tops[1:])):
        # 上下排列：边界取上一张“开票人”行与下一张号码行之间；没有开票人行时取号码行上方 8 个字高
        clerks = sorted(rows[x.row].bottom for x in _hits(rows, CLERK_LABEL))
        bounds = []
        for t in tops[1:]:
            ends = [c for c in clerks if c < t]
            bounds.append((ends[-1] + t) / 2 if ends else t - 8 * h)
        out: list[list[Seg]] = [[] for _ in tops]
        for s in segs:
            out[sum(1 for b in bounds if s.cy >= b)].append(s)
        return out
    if max(tops) - min(tops) <= 6 * h:
        # 左右排列：按标题中心切
        titles = [rows[r].x_at(m.start()) for r, row in enumerate(rows)
                  for m in re.finditer(r"电子发票|增值税.{0,4}发票", row.norm)]
        if len(titles) == len(hits):
            titles.sort()
            bounds = [(a + b) / 2 for a, b in zip(titles, titles[1:])]
            out = [[] for _ in titles]
            for s in segs:
                out[sum(1 for b in bounds if (s.x0 + s.x1) / 2 >= b)].append(s)
            return out
    return None


def assign_qr(groups: list[list[Seg]], qrs: list[Qr]) -> list[Qr | None]:
    """二维码按中心点归到所在的那张票（各票文字的外接框，取距离最近者）。"""
    out: list[Qr | None] = [None] * len(groups)
    if len(groups) == 1:
        out[0] = qrs[0] if qrs else None
        return out
    boxes = [(min(s.x0 for s in g), min(s.top for s in g), max(s.x1 for s in g), max(s.bottom for s in g)) if g else None
             for g in groups]
    for q in qrs:
        best, dist = None, None
        for i, b in enumerate(boxes):
            if b is None:
                continue
            dx = max(b[0] - q.cx, 0, q.cx - b[2])
            dy = max(b[1] - q.cy, 0, q.cy - b[3])
            d = dx * dx + dy * dy
            if dist is None or d < dist:
                best, dist = i, d
        if best is not None and out[best] is None:
            out[best] = q
    return out
