"""原件定位层：只匹配唯一的直接文本 run；拆分只改内存中的副本。"""
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from app.parsing.docx import TrackedDocx, parse_docx

MISMATCH = "原件解析结果与任务记录不一致，无法生成批注版"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


class AnchorMismatch(Exception):
    """重新解析结果与任务记录不同；消息不含文档内容。"""


@dataclass
class Anchor:
    paragraph: Paragraph
    precise: bool = False
    start_run: Run | None = None
    end_run: Run | None = None
    start_offset: int = 0
    end_offset: int = 0  # 右开区间


def _simple(run) -> bool:
    """允许复制 rPr，但文字必须是唯一的直接子元素 w:t。"""
    return (len(run.findall(qn("w:t"))) == 1
            and all(c.tag in (qn("w:rPr"), qn("w:t")) for c in run))


def _occurrences(text: str, quote: str):
    start = text.find(quote)
    while start >= 0:
        yield start
        start = text.find(quote, start + 1)  # 重叠的出现也不是唯一匹配


class Anchors:
    def __init__(self, document: DocxDocument, node_paragraphs: dict[str, list[Paragraph] | None]):
        self.document = document
        self.node_paragraphs = node_paragraphs

    def _first(self) -> Paragraph:
        # 包含空段落，排除文本框；无段落的空正文才新增一个空段落作为批注落点。
        for p in self.document.element.body.iter(qn("w:p")):
            if not any(a.tag == qn("w:txbxContent") for a in p.iterancestors()):
                return Paragraph(p, self.document)
        return self.document.add_paragraph()

    def _field_runs(self) -> set:
        """复杂域的显示结果虽是直接 run，也不可用于精确定位；域可跨段落。"""
        unsafe = set()
        depth = 0
        for r in self.document.element.body.iter(qn("w:r")):
            markers = list(r.iter(qn("w:fldChar")))
            if depth or markers or r.find(qn("w:instrText")) is not None:
                unsafe.add(r)
            for marker in markers:
                kind = marker.get(qn("w:fldCharType"))
                if kind == "begin":
                    depth += 1
                elif kind == "end":
                    depth = max(0, depth - 1)
        return unsafe

    def locate(self, loc, quote, skip_inserted: bool = False) -> Anchor:
        paragraphs = self.node_paragraphs.get(loc)
        if not paragraphs:
            return Anchor(self._first())
        fallback = Anchor(paragraphs[0])
        if not quote:
            return fallback
        unsafe = self._field_runs()
        candidates = []
        visible_count = 0
        for paragraph in paragraphs:
            p = paragraph._p
            # 计算包含超链接、修订、控件的可见文字出现次数，避免把另一次出现当成唯一。
            visible = []
            for el in p.iter():
                if any(a.tag in (qn("w:del"), qn("w:moveFrom"), qn("w:txbxContent"))
                       or (skip_inserted and a.tag == qn("w:ins"))
                       for a in el.iterancestors()):
                    continue
                if el.tag == qn("w:t"):
                    visible.append(el.text or "")
                elif el.tag in (qn("w:tab"), qn("w:br"), qn("w:cr")):
                    visible.append("\t" if el.tag == qn("w:tab") else "\n")
            visible_count += sum(1 for _ in _occurrences("".join(visible), quote))
            if any(a.tag in (qn("w:sdt"), qn("w:ins"), qn("w:moveTo")) for a in p.iterancestors()):
                continue
            segments = []
            segment = []
            for child in p:
                if child.tag == qn("w:r") and child not in unsafe and _simple(child):
                    segment.append(child)
                elif child.tag == qn("w:pPr") or child.tag in (
                    qn("w:bookmarkStart"), qn("w:bookmarkEnd"),
                    qn("w:commentRangeStart"), qn("w:commentRangeEnd"),
                ):
                    continue
                else:
                    segments.append(segment)
                    segment = []
            segments.append(segment)
            for runs in segments:
                text = "".join(r.find(qn("w:t")).text or "" for r in runs)
                for start in _occurrences(text, quote):
                    end = start + len(quote)
                    offset = 0
                    first = last = None
                    for r in runs:
                        length = len(r.find(qn("w:t")).text or "")
                        if offset <= start < offset + length:
                            first = (r, start - offset)
                        if offset < end <= offset + length:
                            last = (r, end - offset)
                        offset += length
                    if first and last:
                        candidates.append(Anchor(paragraph, True, Run(first[0], paragraph),
                                                 Run(last[0], paragraph), first[1], last[1]))
        return candidates[0] if visible_count == 1 and len(candidates) == 1 else fallback


def load_anchors(data: bytes, file_seq: int, stored: dict[str, str]) -> Anchors:
    tracked = parse_docx(data, file_seq, track=True)
    assert isinstance(tracked, TrackedDocx)
    if {n.loc: n.text for n in tracked.nodes} != stored:
        raise AnchorMismatch(MISMATCH)
    document = Document(BytesIO(data))
    # 两次读取同一个 XML，按元素顺序对应；不以文字查找段落，保留重复段落的身份。
    originals = list(tracked.document.iter())
    loaded = list(document.element.iter())
    originals = [e for e in originals if isinstance(e.tag, str)]
    loaded = [e for e in loaded if isinstance(e.tag, str)]
    if [e.tag for e in originals] != [e.tag for e in loaded]:
        raise AnchorMismatch(MISMATCH)
    mapping = dict(zip(originals, loaded))
    return Anchors(document, {
        loc: None if ps is None else [Paragraph(mapping[p], document) for p in ps]
        for loc, ps in tracked.node_paragraphs.items()
    })


def _cut(run, offset: int):
    """在文字内部切分，原 run 留左半，新 run 留右半；格式与空格保留属性一并复制。"""
    t = run.find(qn("w:t"))
    text = t.text or ""
    if offset == 0:
        return run
    if offset == len(text):
        return run.getnext()
    right = deepcopy(run)
    t.text = text[:offset]
    right.find(qn("w:t")).text = text[offset:]
    t.set(_XML_SPACE, "preserve")
    right.find(qn("w:t")).set(_XML_SPACE, "preserve")
    run.addnext(right)
    return right


def split_runs(anchor: Anchor) -> list[Run]:
    if not anchor.precise:
        return []
    p = anchor.paragraph._p
    first, last = anchor.start_run._r, anchor.end_run._r
    children = list(p)
    if first not in children or last not in children:
        anchor.precise = False
        return []
    covered = children[children.index(first):children.index(last) + 1]
    if any(r.tag == qn("w:r") and not _simple(r) for r in covered):
        anchor.precise = False
        return []
    # 先切右端，同一个 run 上的左端偏移仍然有效；校验全部通过后才修改 XML。
    after = _cut(last, anchor.end_offset)
    first = _cut(first, anchor.start_offset)
    result = []
    current = first
    while current is not after:
        if current.tag == qn("w:r"):
            result.append(Run(current, anchor.paragraph))
        current = current.getnext()
    return result
