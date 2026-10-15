"""DOCX 解析（SPEC 8.3，设计 docs/design/M2.md 第四节）。

按“接受全部修订”后的文本输出节点：w:ins、w:moveTo 中的文字计入，w:del、w:moveFrom 中的文字不计入；
段落标记被删除的段落并入下一段；被删除的表格行、单元格不生成节点。

位置编号（SPEC 8.2、open_questions Q-15、Q-40）：
  正文段落 F{n}-P{段}，只给生成了节点的段落编号；正文表格 F{n}-T{表}-R{行}-C{列}，表号按正文顶层表格顺序，
  列号按网格列计（gridSpan 占多列，gridBefore 跳过前面的列）；嵌套表格在外层单元格编号后追加 -T{表}-R{行}-C{列}；
  页眉 F{n}-H{段}、页脚 F{n}-FT{段}（每个非空段落一个节点）；批注 F{n}-CM{条}、文本框 F{n}-TB{个}（每条、每个一个节点）。

节点顺序：页眉、页脚、正文（批注和文本框紧跟在所在段落或单元格之后，嵌套表格紧跟在外层单元格之后）、
没有在正文中引用的批注。

安全：XML 一律不加载 DTD、不解析实体、不联网；含 DOCTYPE 的部件按损坏处理。
解析失败只抛出 ParseError，消息是固定的中文原因，不含原文和异常消息。
"""
import posixpath
import re
import zipfile
from dataclasses import dataclass, field, replace
from io import BytesIO

from lxml import etree

from app.parsing.model import ParsedFile, ParsedNode, ParseError

PARSER_VERSION = "docx-1"

# 与上传检查（SPEC 17.6）相同的解压上限；这里再查一次，不依赖调用方
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10000
MAX_LOC_LENGTH = 64  # doc_nodes.loc varchar(64)
MAX_CLAUSE_LENGTH = 32  # doc_nodes.clause_no varchar(32)
# 嵌套表格加上“-R行-C列”后 loc 可能超长：为行号、列号预留的长度，超出时把更深的嵌套表格文字并入外层单元格
_ROW_COL_RESERVE = 16

MSG_CORRUPT = "文件已损坏或不是有效的 Word 文档（.docx），无法解析"
MSG_ENCRYPTED = "文件已加密或是旧版 Word 格式，请取消密码保护并另存为 .docx 后重新上传"
MSG_TOO_LARGE = "文件解压后过大，无法解析"

WARN_REVISIONS = "检测到修订痕迹，按接受修订后的文本审核"
WARN_HEADER_FOOTER = "页眉页脚未参与 AI 审核"
WARN_COMMENTS = "批注未参与 AI 审核"
WARN_TEXTBOXES = "文本框未参与 AI 审核"
WARN_NOTES = "脚注和尾注未参与 AI 审核"
WARN_EMBEDDED = "文档中的图片和嵌入对象未识别文字，未参与 AI 审核"
WARN_EMPTY = "未提取到正文文字"

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # 加密的 docx 和 .doc 都是 OLE 复合文档

_W_NAMESPACES = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",  # Transitional
    "http://purl.oclc.org/ooxml/wordprocessingml/main",  # Strict
)
_REL_ID_ATTRS = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
    "{http://purl.oclc.org/ooxml/officeDocument/relationships}id",
)
_MATH_T = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/math}t",
    "{http://purl.oclc.org/ooxml/officeDocument/math}t",
)
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_MC_AC = f"{{{_MC}}}AlternateContent"
_MC_CHOICE = f"{{{_MC}}}Choice"
_MC_FALLBACK = f"{{{_MC}}}Fallback"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_EMBEDDED_LOCALNAMES = frozenset({"blip", "imagedata", "OLEObject"})

# 不带自动编号时从段首提取 clause_no：“第X条”或“X.Y”（可多级，每级 1–3 位数字）。
# X.Y 后面紧跟数字或 % 时不算（如“1.5%”），允许以一个句点结尾（“2.1.”）。
_MANUAL_CLAUSE = re.compile(
    r"^(?:(第[0-9０-９一二三四五六七八九十百千零〇两]+条)|(\d{1,3}(?:\.\d{1,3})+)\.?(?![\d%]))"
)
_LVL_PLACEHOLDER = re.compile(r"%([1-9])")


def parse_docx(data: bytes, file_seq: int) -> ParsedFile:
    """解析 DOCX。file_seq 为文件在任务中的序号（从 1 开始），用于位置编号。"""
    try:
        return _DocxParser(data, file_seq).parse()
    except ParseError:
        raise
    except Exception:  # noqa: BLE001  任何其他异常都视为文件损坏；不带异常消息，避免原文片段外泄
        raise ParseError(MSG_CORRUPT) from None


# ---------------------------------------------------------------- 编号格式


_CN_DIGITS = "零一二三四五六七八九"
_CN_UNITS = ("", "十", "百", "千")
_CN_LEGAL_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_LEGAL_UNITS = ("", "拾", "佰", "仟")
_TIANGAN = "甲乙丙丁戊己庚辛壬癸"
_DIZHI = "子丑寅卯辰巳午未申酉戌亥"
_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def _chinese_number(n: int, *, legal: bool = False) -> str:
    """中文计数：1→一，10→十，11→十一，101→一百零一，1010→一千零一十；legal 为大写（壹拾）。"""
    if n < 0 or n >= 10**8:
        return str(n)
    digits = _CN_LEGAL_DIGITS if legal else _CN_DIGITS
    units = _CN_LEGAL_UNITS if legal else _CN_UNITS
    if n == 0:
        return "零" if legal else "〇"

    def below_10000(x: int) -> str:
        out, zero = "", False
        for i in (3, 2, 1, 0):
            d = x // 10**i % 10
            if d == 0:
                zero = bool(out)
            else:
                if zero:
                    out += digits[0]
                    zero = False
                out += digits[d] + units[i]
        return out

    high, low = divmod(n, 10000)
    if high:
        text = below_10000(high) + "万"
        if low:
            text += (digits[0] if low < 1000 else "") + below_10000(low)
    else:
        text = below_10000(low)
    if not legal and text.startswith("一十"):
        text = text[1:]
    return text


def _roman(n: int) -> str:
    if not 0 < n < 4000:
        return str(n)
    out = ""
    for value, letters in _ROMAN:
        while n >= value:
            out += letters
            n -= value
    return out


def format_number(fmt: str, n: int) -> str:
    """按 numFmt 把计数值 n 转成编号文字。不认识的格式按 decimal 处理。"""
    if fmt == "none":
        return ""
    if fmt in ("upperRoman", "lowerRoman"):
        text = _roman(n)
        return text if fmt == "upperRoman" else text.lower()
    if fmt in ("upperLetter", "lowerLetter"):
        if n < 1:
            return str(n)
        # Word 的字母编号：A…Z，然后 AA、BB…（重复同一个字母）
        text = chr(ord("A") + (n - 1) % 26) * ((n - 1) // 26 + 1)
        return text if fmt == "upperLetter" else text.lower()
    if fmt in ("chineseCounting", "chineseCountingThousand", "japaneseCounting", "taiwaneseCounting",
               "taiwaneseCountingThousand", "koreanCounting"):
        return _chinese_number(n)
    if fmt in ("chineseLegalSimplified", "japaneseLegal"):
        return _chinese_number(n, legal=True)
    if fmt == "ideographTraditional":
        return _TIANGAN[(n - 1) % 10] if n >= 1 else str(n)
    if fmt == "ideographZodiac":
        return _DIZHI[(n - 1) % 12] if n >= 1 else str(n)
    if fmt in ("ideographDigital", "taiwaneseDigital", "japaneseDigitalTenThousand"):
        return "".join("〇一二三四五六七八九"[int(c)] for c in str(n)) if n >= 0 else str(n)
    if fmt in ("decimalFullWidth", "decimalFullWidth2"):
        return str(n).translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    if fmt == "decimalZero":
        return f"{n:02d}"
    if 1 <= n <= 20:
        if fmt in ("decimalEnclosedCircle", "decimalEnclosedCircleChinese", "ideographEnclosedCircle"):
            return chr(0x2460 + n - 1)  # ①
        if fmt == "decimalEnclosedParen":
            return chr(0x2474 + n - 1)  # ⑴
        if fmt == "decimalEnclosedFullstop":
            return chr(0x2488 + n - 1)  # ⒈
    return str(n)


def manual_clause_no(text: str) -> str | None:
    """不带自动编号的段落：从段首的“第X条”“X.Y”提取 clause_no。"""
    m = _MANUAL_CLAUSE.match(text)
    if not m:
        return None
    return (m.group(1) or m.group(2))[:MAX_CLAUSE_LENGTH]


# ---------------------------------------------------------------- 包与 XML


def _parse_xml(raw: bytes) -> etree._Element:
    # 正常的 DOCX 部件不会有 DTD；有就按损坏处理，杜绝实体扩展和外部实体
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ParseError(MSG_CORRUPT)
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False,
        remove_comments=True, remove_pis=True,
    )
    root = etree.fromstring(raw, parser)
    if root.getroottree().docinfo.doctype:  # 非 UTF-8 编码时上面的字节检查可能看不到
        raise ParseError(MSG_CORRUPT)
    return root


def _resolve_target(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target[1:])
    return posixpath.normpath(posixpath.join(base_dir, target))


class _Package:
    def __init__(self, data: bytes):
        if data[:8] == _OLE_MAGIC:
            raise ParseError(MSG_ENCRYPTED)
        self.zf = zipfile.ZipFile(BytesIO(data))
        infos = self.zf.infolist()
        # 声明大小取自 ZIP 目录；zipfile 读取时不会超出声明大小，所以总量受此限制
        if len(infos) > MAX_ARCHIVE_ENTRIES or sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
            raise ParseError(MSG_TOO_LARGE)
        # OPC 部件名不区分大小写
        self.members = {i.filename.lower(): i for i in infos}

    def xml(self, name: str | None) -> etree._Element | None:
        if name is None:
            return None
        info = self.members.get(name.lower())
        if info is None:
            return None
        return _parse_xml(self.zf.read(info))

    def rels(self, part: str) -> list[tuple[str, str, str]]:
        """返回部件的关系 [(Id, Type, 目标部件名)]，忽略外部链接。"""
        base_dir, base_name = posixpath.split(part)
        root = self.xml(posixpath.join(base_dir, "_rels", f"{base_name}.rels"))
        if root is None:
            return []
        out = []
        for rel in root.iter(_PKG_REL):
            if rel.get("TargetMode") == "External" or not rel.get("Target"):
                continue
            out.append((rel.get("Id") or "", rel.get("Type") or "", _resolve_target(base_dir, rel.get("Target"))))
        return out

    def main_part(self) -> str:
        for _rid, typ, target in self.rels(""):
            if typ.endswith("/officeDocument"):
                return target
        return "word/document.xml"


class _W:
    """WordprocessingML 的元素名和属性名（按文档实际使用的命名空间生成）。"""

    _NAMES = (
        "abstractNum", "abstractNumId", "basedOn", "body", "br", "cellDel", "cellIns", "commentReference", "cr",
        "customXml", "default", "document", "drawing", "footerReference", "gridBefore", "gridSpan",
        "headerReference", "id", "ilvl", "ins", "isLgl", "lvl", "lvlOverride", "lvlRestart", "lvlText", "moveFrom",
        "moveTo", "name", "noBreakHyphen", "num", "numFmt", "numId", "numPr", "numStyleLink", "object",
        "outlineLvl", "p", "pPr", "pStyle", "pict", "ptab", "r", "rPr", "sdt", "sdtContent", "sectPr",
        "sectPrChange", "start", "startOverride", "style", "styleId", "t", "tab", "tbl", "tc", "tcPr", "tr",
        "trPr", "txbxContent", "type", "val", "altChunk",
    )

    def __init__(self, ns: str):
        for name in self._NAMES:
            setattr(self, name, f"{{{ns}}}{name}")
        self.del_ = f"{{{ns}}}del"


def _child_val(parent: etree._Element | None, tag: str, attr: str) -> str | None:
    if parent is None:
        return None
    el = parent.find(tag)
    return None if el is None else el.get(attr)


def _int(value: str | None, default: int | None) -> int | None:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _choose(ac: etree._Element) -> etree._Element | None:
    """mc:AlternateContent 只取一个分支（第一个 Choice，没有则 Fallback），避免同一内容计两次。"""
    for tag in (_MC_CHOICE, _MC_FALLBACK):
        for child in ac:
            if child.tag == tag:
                return child
    return None


# ---------------------------------------------------------------- 样式与编号


@dataclass
class _StyleInfo:
    based_on: str | None
    name: str
    num_id: str | None
    ilvl: int | None
    outline: int | None


_HEADING_NAME = re.compile(r"^(heading\s*[1-9]|title|标题(\s*[1-9])?)$", re.IGNORECASE)


class _Styles:
    def __init__(self, root: etree._Element | None, w: _W):
        self.w = w
        self.styles: dict[str, _StyleInfo] = {}
        self.default_para: str | None = None
        if root is None:
            return
        for st in root.iter(w.style):
            sid = st.get(w.styleId)
            if not sid:
                continue
            ppr = st.find(w.pPr)
            num_pr = ppr.find(w.numPr) if ppr is not None else None
            self.styles[sid] = _StyleInfo(
                based_on=_child_val(st, w.basedOn, w.val),
                name=_child_val(st, w.name, w.val) or "",
                num_id=_child_val(num_pr, w.numId, w.val),
                ilvl=_int(_child_val(num_pr, w.ilvl, w.val), None),
                outline=_int(_child_val(ppr, w.outlineLvl, w.val), None),
            )
            if st.get(w.type) == "paragraph" and st.get(w.default) in ("1", "true", "on"):
                self.default_para = sid

    def chain(self, sid: str | None) -> list[_StyleInfo]:
        out, seen = [], set()
        while sid and sid not in seen and len(out) < 20:
            seen.add(sid)
            info = self.styles.get(sid)
            if info is None:
                break
            out.append(info)
            sid = info.based_on
        return out

    def para_style(self, ppr: etree._Element | None) -> str | None:
        return _child_val(ppr, self.w.pStyle, self.w.val) or self.default_para

    def num_pr(self, sid: str | None) -> tuple[str | None, int | None]:
        num_id = ilvl = None
        for info in self.chain(sid):
            if num_id is None:
                num_id = info.num_id
            if ilvl is None:
                ilvl = info.ilvl
        return num_id, ilvl

    def is_heading(self, ppr: etree._Element | None) -> bool:
        outline = _int(_child_val(ppr, self.w.outlineLvl, self.w.val), None)
        chain = self.chain(self.para_style(ppr))
        if outline is None:
            outline = next((s.outline for s in chain if s.outline is not None), None)
        if outline is not None:
            return 0 <= outline <= 8
        return any(_HEADING_NAME.match(s.name.strip()) for s in chain)


@dataclass
class _Level:
    start: int = 1
    fmt: str = "decimal"
    text: str | None = None
    restart: int | None = None  # lvlRestart：None 为默认（上级出现即重新计数），0 为从不重新计数
    is_lgl: bool = False
    pstyle: str | None = None


@dataclass
class _Num:
    abstract_id: str
    starts: dict[int, int]
    levels: dict[int, _Level]


class _Numbering:
    """按 numId、ilvl 和 lvlText 计算自动编号文字（SPEC 8.3）。

    计数器：没有 lvlOverride 的 num 共用其 abstractNum 的计数器（Word 中同一列表的延续）；
    有 lvlOverride（startOverride 或重定义级别）的 num 单独计数。
    """

    def __init__(self, root: etree._Element | None, w: _W, styles: _Styles):
        self.w = w
        self.styles = styles
        self.abstracts: dict[str, tuple[dict[int, _Level], str | None]] = {}
        self.nums: dict[str, _Num] = {}
        self.counters: dict[tuple[str, str], dict[int, int]] = {}
        self._levels_cache: dict[str, dict[int, _Level]] = {}
        if root is None:
            return
        for ab in root.iter(w.abstractNum):
            aid = ab.get(w.abstractNumId)
            if aid is not None:
                levels = {}
                for lvl in ab.findall(w.lvl):
                    ilvl = _int(lvl.get(w.ilvl), None)
                    if ilvl is not None and 0 <= ilvl <= 8:
                        levels[ilvl] = self._level(lvl)
                self.abstracts[aid] = (levels, _child_val(ab, w.numStyleLink, w.val))
        for num in root.iter(w.num):
            num_id = num.get(w.numId)
            aid = _child_val(num, w.abstractNumId, w.val)
            if num_id is None or aid is None:
                continue
            starts: dict[int, int] = {}
            levels: dict[int, _Level] = {}
            for ov in num.findall(w.lvlOverride):
                ilvl = _int(ov.get(w.ilvl), None)
                if ilvl is None or not 0 <= ilvl <= 8:
                    continue
                start = _int(_child_val(ov, w.startOverride, w.val), None)
                if start is not None:
                    starts[ilvl] = start
                lvl = ov.find(w.lvl)
                if lvl is not None:
                    levels[ilvl] = self._level(lvl)
            self.nums[num_id] = _Num(aid, starts, levels)

    def _level(self, lvl: etree._Element) -> _Level:
        w = self.w
        is_lgl = lvl.find(w.isLgl)
        return _Level(
            start=_int(_child_val(lvl, w.start, w.val), 1),
            fmt=_child_val(lvl, w.numFmt, w.val) or "decimal",
            text=_child_val(lvl, w.lvlText, w.val),
            restart=_int(_child_val(lvl, w.lvlRestart, w.val), None),
            is_lgl=is_lgl is not None and is_lgl.get(w.val) not in ("0", "false", "off"),
            pstyle=_child_val(lvl, w.pStyle, w.val),
        )

    def _abstract_levels(self, aid: str, depth: int = 0) -> dict[int, _Level]:
        levels, style_link = self.abstracts.get(aid, ({}, None))
        if levels or not style_link or depth > 5:
            return levels
        # numStyleLink：级别定义在该编号样式所指向的 num 上
        num_id, _ = self.styles.num_pr(style_link)
        target = self.nums.get(num_id) if num_id else None
        if target is None or target.abstract_id == aid:
            return levels
        return self._abstract_levels(target.abstract_id, depth + 1)

    def levels(self, num_id: str) -> dict[int, _Level]:
        if num_id not in self._levels_cache:
            num = self.nums.get(num_id)
            levels: dict[int, _Level] = {}
            if num is not None:
                levels = dict(self._abstract_levels(num.abstract_id))
                levels.update(num.levels)
                for ilvl, start in num.starts.items():
                    if ilvl in levels:
                        levels[ilvl] = replace(levels[ilvl], start=start)
            self._levels_cache[num_id] = levels
        return self._levels_cache[num_id]

    def lookup(self, ppr: etree._Element | None) -> tuple[str, int] | None:
        """段落的 (numId, ilvl)：段落直接设置优先，其次段落样式（含 basedOn 链）；numId=0 表示取消编号。"""
        w = self.w
        num_pr = ppr.find(w.numPr) if ppr is not None else None
        sid = self.styles.para_style(ppr)
        style_num, style_ilvl = self.styles.num_pr(sid)
        num_id = _child_val(num_pr, w.numId, w.val) or style_num
        if num_id is None or _int(num_id, 0) == 0:
            return None
        levels = self.levels(num_id)
        if not levels:
            return None
        ilvl = _int(_child_val(num_pr, w.ilvl, w.val), None)
        if ilvl is None:
            ilvl = style_ilvl
        if ilvl is None:
            ilvl = next((i for i, lv in sorted(levels.items()) if sid and lv.pstyle == sid), 0)
        return (num_id, ilvl) if ilvl in levels else None

    def advance(self, num_id: str, ilvl: int) -> str | None:
        """计数并返回编号文字；项目符号或没有编号文字时返回 None。"""
        levels = self.levels(num_id)
        num = self.nums[num_id]
        key = ("num", num_id) if (num.starts or num.levels) else ("abstract", num.abstract_id)
        counter = self.counters.setdefault(key, {})
        lvl = levels[ilvl]
        counter[ilvl] = counter[ilvl] + 1 if ilvl in counter else lvl.start
        for deeper in [d for d in counter if d > ilvl]:
            restart = levels[deeper].restart if deeper in levels else None
            if restart is None or (restart > 0 and ilvl < restart):
                del counter[deeper]
        if lvl.fmt == "bullet" or not lvl.text:
            return None

        def render(m: re.Match) -> str:
            k = int(m.group(1)) - 1
            ref = levels.get(k)
            if ref is None:
                return ""
            fmt = "decimal" if lvl.is_lgl and ref.fmt != "none" else ref.fmt
            return format_number(fmt, counter.get(k, ref.start))

        text = _LVL_PLACEHOLDER.sub(render, lvl.text).strip()
        return text[:MAX_CLAUSE_LENGTH] or None


# ---------------------------------------------------------------- 解析


@dataclass
class _Inline:
    parts: list[str] = field(default_factory=list)
    rev: bool = False
    side: list[tuple] = field(default_factory=list)  # ("tb", 文字, 有修订) / ("cm", 批注 id)


@dataclass
class _Para:
    text: str
    rev: bool
    mark_deleted: bool
    side: list[tuple]
    numbered: bool = False
    clause: str | None = None
    heading: bool = False


class _DocxParser:
    def __init__(self, data: bytes, file_seq: int):
        self.pkg = _Package(data)
        self.f = f"F{file_seq}"
        self.has_revisions = False
        self.has_embedded = False
        self.p = 0  # 正文段落序号
        self.t = 0  # 正文顶层表格序号
        self.comments: dict[str, etree._Element] = {}
        self.emitted_comments: set[str] = set()

    # -- 总流程

    def parse(self) -> ParsedFile:
        doc_part = self.pkg.main_part()
        root = self.pkg.xml(doc_part)
        if root is None:
            raise ParseError(MSG_CORRUPT)
        ns = etree.QName(root).namespace
        if ns not in _W_NAMESPACES or etree.QName(root).localname != "document":
            raise ParseError(MSG_CORRUPT)
        w = self.w = _W(ns)
        body = root.find(w.body)
        if body is None:
            raise ParseError(MSG_CORRUPT)

        rels = self.pkg.rels(doc_part)
        by_id = {rid: target for rid, _typ, target in rels}

        def first(kind: str) -> str | None:
            return next((target for _rid, typ, target in rels if typ.endswith("/" + kind)), None)

        self.styles = _Styles(self.pkg.xml(first("styles")), w)
        self.numbering = _Numbering(self.pkg.xml(first("numbering")), w, self.styles)
        comments_root = self.pkg.xml(first("comments"))
        if comments_root is not None:
            for c in comments_root.iter(f"{{{ns}}}comment"):
                cid = c.get(w.id)
                if cid is not None and cid not in self.comments:
                    self.comments[cid] = c

        headers, footers = self._header_footer_parts(body, by_id)
        nodes: list[ParsedNode] = []
        for parts, part, prefix in ((headers, "header", "H"), (footers, "footer", "FT")):
            n = 0
            for name in parts:
                story = self.pkg.xml(name)
                if story is None:
                    continue
                items, side = self._flatten(story)
                for text, rev in items:
                    n += 1
                    nodes.append(self._node(f"{self.f}-{prefix}{n}", "paragraph", part, text, rev=rev))
                nodes.extend(self._side_nodes(side, None))
        nodes.extend(self._body(body))
        leftover = [("cm", cid) for cid in self.comments if cid not in self.emitted_comments]
        nodes.extend(self._side_nodes(leftover, None))

        notes = any(self._has_note_text(self.pkg.xml(first(k))) for k in ("footnotes", "endnotes"))
        self._finalize(nodes)
        return ParsedFile(nodes=nodes, page_count=None, has_revisions=self.has_revisions,
                          warnings=self._warnings(nodes, notes))

    def _header_footer_parts(self, body: etree._Element, by_id: dict[str, str]) -> tuple[list[str], list[str]]:
        """按各节 sectPr 中引用的顺序取页眉、页脚部件（去重）。"""
        w = self.w
        headers: list[str] = []
        footers: list[str] = []
        for sect in body.iter(w.sectPr):
            parent = sect.getparent()
            if parent is not None and parent.tag == w.sectPrChange:  # 修订前的旧节属性
                continue
            for ref in sect:
                target_list = headers if ref.tag == w.headerReference else footers if ref.tag == w.footerReference else None
                if target_list is None:
                    continue
                rid = next((ref.get(a) for a in _REL_ID_ATTRS if ref.get(a)), None)
                target = by_id.get(rid) if rid else None
                if target and target not in target_list:
                    target_list.append(target)
        return headers, footers

    def _has_note_text(self, root: etree._Element | None) -> bool:
        if root is None:
            return False
        for note in root:
            if not isinstance(note.tag, str) or etree.QName(note).localname not in ("footnote", "endnote"):
                continue
            if note.get(self.w.type) in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            items, _side = self._flatten(note)
            if items:
                return True
        return False

    def _finalize(self, nodes: list[ParsedNode]) -> None:
        tb = cm = 0
        for i, node in enumerate(nodes, start=1):
            node.ord = i
            if node.part == "textbox":
                tb += 1
                node.loc = f"{self.f}-TB{tb}"
            elif node.part == "comment":
                cm += 1
                node.loc = f"{self.f}-CM{cm}"
            if len(node.loc) > MAX_LOC_LENGTH:
                raise ParseError(MSG_CORRUPT)

    def _warnings(self, nodes: list[ParsedNode], notes: bool) -> list[str]:
        parts = {n.part for n in nodes}
        warnings = []
        if self.has_revisions:
            warnings.append(WARN_REVISIONS)
        if parts & {"header", "footer"}:
            warnings.append(WARN_HEADER_FOOTER)
        if "comment" in parts:
            warnings.append(WARN_COMMENTS)
        if "textbox" in parts:
            warnings.append(WARN_TEXTBOXES)
        if notes:
            warnings.append(WARN_NOTES)
        if self.has_embedded:
            warnings.append(WARN_EMBEDDED)
        if "body" not in parts:
            warnings.append(WARN_EMPTY)
        return warnings

    # -- 节点

    def _node(self, loc: str, kind: str, part: str, text: str, *, clause: str | None = None,
              parent: str | None = None, rev: bool = False) -> ParsedNode:
        return ParsedNode(loc=loc, ord=0, kind=kind, part=part, text=text, clause_no=clause,
                          parent_loc=parent, has_revision=rev)

    def _side_nodes(self, items: list[tuple], parent: str | None) -> list[ParsedNode]:
        """文本框和批注节点；loc 在 _finalize 中按最终顺序编号。"""
        out = []
        for item in items:
            if item[0] == "tb":
                _, text, rev = item
                if text:
                    out.append(self._node("", "paragraph", "textbox", text, parent=parent, rev=rev))
                continue
            cid = item[1]
            if cid in self.emitted_comments or cid not in self.comments:
                continue
            self.emitted_comments.add(cid)
            texts, side = self._flatten(self.comments[cid])
            if texts:
                out.append(self._node("", "paragraph", "comment", "\n".join(t for t, _ in texts), parent=parent,
                                      rev=any(r for _, r in texts)))
            out.extend(self._side_nodes(side, parent))
        return out

    # -- 块级遍历

    def _items(self, el: etree._Element, want: tuple[str, ...]):
        """el 的子元素中 tag 属于 want 的，穿过内容控件、自定义 XML 和 AlternateContent 包装。"""
        w = self.w
        for child in el:
            tag = child.tag
            if tag in want:
                yield child
            elif tag == w.sdt:
                content = child.find(w.sdtContent)
                if content is not None:
                    yield from self._items(content, want)
            elif tag == w.customXml:
                yield from self._items(child, want)
            elif tag == _MC_AC:
                branch = _choose(child)
                if branch is not None:
                    yield from self._items(branch, want)
            elif tag == w.altChunk:  # 嵌入的外部文档（HTML、RTF 等），不解析
                self.has_embedded = True

    def _rows(self, tbl: etree._Element):
        """表格中接受修订后仍存在的行：(tr, 整行是插入的)。"""
        w = self.w
        for tr in self._items(tbl, (w.tr,)):
            tr_pr = tr.find(w.trPr)
            if tr_pr is not None and tr_pr.find(w.del_) is not None:
                self.has_revisions = True
                continue
            inserted = tr_pr is not None and tr_pr.find(w.ins) is not None
            if inserted:
                self.has_revisions = True
            yield tr, inserted

    def _cells(self, tr: etree._Element):
        """行中接受修订后仍存在的单元格：(tc, 所在网格列, 有修订)。"""
        w = self.w
        tr_pr = tr.find(w.trPr)
        col = 1 + max(0, _int(_child_val(tr_pr, w.gridBefore, w.val), 0))
        for tc in self._items(tr, (w.tc,)):
            tc_pr = tc.find(w.tcPr)
            span = max(1, _int(_child_val(tc_pr, w.gridSpan, w.val), 1))
            if tc_pr is not None and tc_pr.find(w.cellDel) is not None:
                self.has_revisions = True
                continue
            inserted = tc_pr is not None and tc_pr.find(w.cellIns) is not None
            if inserted:
                self.has_revisions = True
            yield tc, col, inserted
            col += span

    @staticmethod
    def _absorb(carry: _Para | None, para: _Para) -> _Para:
        """段落标记被删除的段落（carry）并入下一段。"""
        if carry is None:
            return para
        para.text = carry.text + para.text
        para.rev = True
        para.side = carry.side + para.side
        return para

    def _body(self, body: etree._Element) -> list[ParsedNode]:
        w = self.w
        out: list[ParsedNode] = []
        carry: _Para | None = None
        for blk in self._items(body, (w.p, w.tbl)):
            if blk.tag == w.p:
                para = self._absorb(carry, self._paragraph(blk, body=True))
                carry = None
                if para.mark_deleted:
                    carry = para
                    continue
                self._emit_paragraph(para, out)
            else:
                if carry is not None:
                    self._emit_paragraph(carry, out)
                    carry = None
                self.t += 1
                self._table(blk, f"{self.f}-T{self.t}", None, out)
        if carry is not None:
            self._emit_paragraph(carry, out)
        return out

    def _emit_paragraph(self, para: _Para, out: list[ParsedNode]) -> None:
        text = para.text.strip()
        loc = None
        if text:
            self.p += 1
            loc = f"{self.f}-P{self.p}"
            kind = "heading" if para.heading else "list_item" if para.numbered else "paragraph"
            out.append(self._node(loc, kind, "body", text, clause=para.clause or manual_clause_no(text),
                                  rev=para.rev))
        out.extend(self._side_nodes(para.side, loc))

    def _table(self, tbl: etree._Element, prefix: str, parent: str | None, out: list[ParsedNode]) -> None:
        r = 0
        for tr, row_inserted in self._rows(tbl):
            r += 1
            for tc, col, cell_inserted in self._cells(tr):
                self._cell(tc, f"{prefix}-R{r}-C{col}", parent, out, row_inserted or cell_inserted)

    def _cell(self, tc: etree._Element, loc: str, parent: str | None, out: list[ParsedNode], rev: bool) -> None:
        """单元格的直接段落合成一个节点（多段用换行连接）；嵌套表格单独编号，排在该单元格之后。"""
        w = self.w
        texts: list[str] = []
        side: list[tuple] = []
        nested: list[ParsedNode] = []
        k = 0
        carry: _Para | None = None

        def take(para: _Para) -> None:
            nonlocal rev
            if para.text.strip():
                texts.append(para.text.strip())
            rev = rev or para.rev
            side.extend(para.side)

        for blk in self._items(tc, (w.p, w.tbl)):
            if blk.tag == w.p:
                para = self._absorb(carry, self._paragraph(blk, body=True))
                carry = None
                if para.mark_deleted:
                    carry = para
                    continue
                take(para)
            else:
                if carry is not None:
                    take(carry)
                    carry = None
                k += 1
                prefix = f"{loc}-T{k}"
                if len(prefix) + _ROW_COL_RESERVE > MAX_LOC_LENGTH:
                    # 嵌套过深，loc 会超出字段长度：文字并入本单元格，不丢内容
                    items, sub_side = self._flatten_table(blk)
                    texts.extend(t for t, _ in items)
                    rev = rev or any(r for _, r in items)
                    side.extend(sub_side)
                else:
                    self._table(blk, prefix, loc, nested)
        if carry is not None:
            take(carry)
        text = "\n".join(texts)
        if text:
            out.append(self._node(loc, "table_cell", "body", text, parent=parent, rev=rev))
        out.extend(self._side_nodes(side, loc if text else parent))
        out.extend(nested)

    def _flatten(self, container: etree._Element) -> tuple[list[tuple[str, bool]], list[tuple]]:
        """非正文内容（页眉页脚、批注、文本框）：按顺序取出每个非空段落 (文字, 有修订)，表格逐格展开。"""
        w = self.w
        items: list[tuple[str, bool]] = []
        side: list[tuple] = []
        carry: _Para | None = None

        def take(para: _Para) -> None:
            if para.text.strip():
                items.append((para.text.strip(), para.rev))
            side.extend(para.side)

        for blk in self._items(container, (w.p, w.tbl)):
            if blk.tag == w.p:
                para = self._absorb(carry, self._paragraph(blk, body=False))
                carry = None
                if para.mark_deleted:
                    carry = para
                    continue
                take(para)
            else:
                if carry is not None:
                    take(carry)
                    carry = None
                sub_items, sub_side = self._flatten_table(blk)
                items.extend(sub_items)
                side.extend(sub_side)
        if carry is not None:
            take(carry)
        return items, side

    def _flatten_table(self, tbl: etree._Element) -> tuple[list[tuple[str, bool]], list[tuple]]:
        items: list[tuple[str, bool]] = []
        side: list[tuple] = []
        for tr, row_inserted in self._rows(tbl):
            for tc, _col, cell_inserted in self._cells(tr):
                sub_items, sub_side = self._flatten(tc)
                items.extend((t, r or row_inserted or cell_inserted) for t, r in sub_items)
                side.extend(sub_side)
        return items, side

    # -- 段落与行内

    def _paragraph(self, p: etree._Element, *, body: bool) -> _Para:
        """接受修订后的段落文字。body=True 时计算自动编号（推进计数器）和标题判断。"""
        w = self.w
        ppr = p.find(w.pPr)
        mark_rpr = ppr.find(w.rPr) if ppr is not None else None
        mark_deleted = mark_rpr is not None and (
            mark_rpr.find(w.del_) is not None or mark_rpr.find(w.moveFrom) is not None)
        mark_inserted = mark_rpr is not None and (
            mark_rpr.find(w.ins) is not None or mark_rpr.find(w.moveTo) is not None)
        ctx = _Inline()
        self._inline(p, ctx)
        para = _Para(text="".join(ctx.parts), rev=ctx.rev or mark_deleted or mark_inserted,
                     mark_deleted=mark_deleted, side=ctx.side)
        if para.rev:
            self.has_revisions = True
        if body and not mark_deleted:
            num = self.numbering.lookup(ppr)
            if num is not None:
                para.numbered = True
                para.clause = self.numbering.advance(*num)
            para.heading = self.styles.is_heading(ppr)
        return para

    def _inline(self, el: etree._Element, ctx: _Inline) -> None:
        w = self.w
        for child in el:
            tag = child.tag
            if not isinstance(tag, str) or tag in (w.pPr, w.rPr):
                continue
            if tag == w.r:
                self._run(child, ctx)
            elif tag in (w.del_, w.moveFrom):  # 删除和移走的内容不计入
                ctx.rev = True
            elif tag in (w.ins, w.moveTo):
                ctx.rev = True
                self._inline(child, ctx)
            elif tag == _MC_AC:
                branch = _choose(child)
                if branch is not None:
                    self._inline(branch, ctx)
            elif tag == w.sdt:
                content = child.find(w.sdtContent)
                if content is not None:
                    self._inline(content, ctx)
            elif tag == w.txbxContent:
                self._textbox(child, ctx)
            elif tag in _MATH_T:
                ctx.parts.append(child.text or "")
            else:  # 超链接、智能标记、简单域、自定义 XML、公式等容器
                self._inline(child, ctx)

    def _run(self, r: etree._Element, ctx: _Inline) -> None:
        w = self.w
        for c in r:
            tag = c.tag
            if tag == w.t:
                ctx.parts.append(c.text or "")
            elif tag in (w.tab, w.ptab):
                ctx.parts.append("\t")
            elif tag in (w.br, w.cr):
                ctx.parts.append("\n")
            elif tag == w.noBreakHyphen:
                ctx.parts.append("-")
            elif tag == w.commentReference:
                cid = c.get(w.id)
                if cid is not None:
                    ctx.side.append(("cm", cid))
            elif tag in (w.drawing, w.pict, w.object, _MC_AC):
                self._embedded(c, ctx)
            # 其余（delText、instrText 域代码、softHyphen、sym、脚注引用等）不计入

    def _embedded(self, el: etree._Element, ctx: _Inline) -> None:
        """图形、VML、OLE 对象：只取其中的文本框；遇到图片或嵌入对象记下以便提示。"""
        tag = el.tag
        if not isinstance(tag, str):
            return
        if tag == _MC_AC:
            branch = _choose(el)
            if branch is not None:
                for c in branch:
                    self._embedded(c, ctx)
            return
        if tag == self.w.txbxContent:
            self._textbox(el, ctx)
            return
        if etree.QName(el).localname in _EMBEDDED_LOCALNAMES:
            self.has_embedded = True
        for c in el:
            self._embedded(c, ctx)

    def _textbox(self, txbx: etree._Element, ctx: _Inline) -> None:
        items, side = self._flatten(txbx)
        ctx.side.append(("tb", "\n".join(t for t, _ in items), any(r for _, r in items)))
        ctx.side.extend(side)
