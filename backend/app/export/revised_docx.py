"""在内存副本写入可靠修订，其余问题转批注；双视图校验后才输出。"""
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import logging
import re
from zoneinfo import ZoneInfo

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree
from sqlalchemy import select

from app.db.models import Finding
from app.export import annotated_docx as shared
from app.export.docx_anchor import split_runs
from app.export.report_docx import _clean
from app.redaction.engine import load_redaction_map
from app.redaction.restore import restore
from app.review.service import run_detail
from app.security.errors import AppError

logger = logging.getLogger(__name__)
_TEXT = {qn("w:t"), qn("w:delText"), qn("w:instrText")}
_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_CODES = re.compile(
    r"\[[^\[\]\r\n]+\]|［[^［］\r\n]+］|【[^【】\r\n]*[:：][^【】\r\n]*】|"
    r"(?:客户|供应商|买方|卖方|其他|收货人|通知方|受益人|银行|公司)_[A-Z]+|"
    r"(?:项目|人员|编号|账号|SWIFT|金额|单价|数量|敏感词)_(?:Q)?\d+"
)


class RevisionValidationError(shared.AnnotationValidationError):
    """原因代码不含文档内容。"""


def _inserted_paragraph(p):
    return p.find("./" + qn("w:pPr") + "/" + qn("w:rPr") + "/" + qn("w:ins")) is not None


def _paragraphs(root):
    return list(root.iter(qn("w:p")))


def _paragraph_text(p, accept=True):
    return "".join(e.text or "" for e in p.iter() if e.tag in _TEXT and not any(
        a.tag == qn("w:del" if accept else "w:ins") for a in e.iterancestors()))


def _views(data):
    root = etree.fromstring(shared._parts(data)["word/document.xml"])
    rejected = "".join(e.text or "" for e in root.iter() if e.tag in _TEXT and not any(
        a.tag == qn("w:ins") or (a.tag == qn("w:p") and _inserted_paragraph(a))
        for a in e.iterancestors()))
    return rejected, [_paragraph_text(p) for p in _paragraphs(root)]


def validate(original: bytes, result: bytes, expected: list[str]) -> None:
    Document(BytesIO(result))
    before, after = shared._parts(original), shared._parts(result)
    rejected, accepted = _views(result)
    if rejected != shared._text(before["word/document.xml"]):
        raise RevisionValidationError("REJECT_TEXT_CHANGED")
    if accepted != expected:
        raise RevisionValidationError("ACCEPT_TEXT_CHANGED")
    if set(before) - set(after) or any(
        after.get(name) != value for name, value in before.items() if name not in shared._MUTABLE
    ) or set(after) - set(before) - shared._MUTABLE:
        raise RevisionValidationError("PART_CHANGED")


def build_revised(db, run, file):
    shared.check_available(run, file)
    if file.has_revisions:
        raise AppError(409, "REVISED_UNAVAILABLE", "原件含修订记录，请在 Word 中接受或拒绝全部修订后重新上传")
    findings = sorted((f for f in run_detail(db, run).findings
                       if f.handling_status == "adopted" and f.suggested_text
                       and shared._belongs(f.locs, file.seq, file.is_main)), key=lambda f: f.seq)
    if not findings:
        raise AppError(409, "REVISED_UNAVAILABLE", "本轮没有已采纳且有建议条文的问题")
    original, anchors = shared.load_source(db, file)
    mapping = load_redaction_map(db, run.task_id)
    raw = {row.id: row for row in db.execute(select(Finding.id, Finding.quote_model, Finding.suggested_text)
                                            .where(Finding.run_id == run.id))}
    document = anchors.document
    # 预期文本单独维护；不可从生成后的修订 XML 反推，避免校验成为自我比较。
    expected = [(p, _paragraph_text(p)) for p in _paragraphs(document.element)]
    roots = [document.element]
    if document.part.rels:
        for rel in document.part.rels.values():
            if not rel.is_external and rel.reltype.endswith("/comments"):
                roots.append(rel.target_part.element)
    counter = max((int(e.get(qn("w:id"))) for root in roots for e in root.iter()
                   if (e.get(qn("w:id"))) and e.get(qn("w:id")).isdigit()), default=-1)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def revision(kind):
        nonlocal counter
        counter += 1
        e = OxmlElement("w:" + kind)
        for key, value in (("id", str(counter)), ("author", "商衡SI"), ("date", date)):
            e.set(qn("w:" + key), value)
        return e

    def text_run(text, source=None):
        r = OxmlElement("w:r")
        if source is not None and source.find(qn("w:rPr")) is not None:
            r.append(deepcopy(source.find(qn("w:rPr"))))
        t = OxmlElement("w:t")
        t.text = text
        t.set(_SPACE, "preserve")
        r.append(t)
        return r

    fallbacks, tails = [], {}
    revised = 0
    for f in findings:
        loc = f.locs[0] if f.locs else None
        quote = restore(raw[f.id].quote_model or "", mapping)
        # run_detail 已还原展示文字；修订从库中原值还原一次，避免真实值被二次替换。
        text = restore(raw[f.id].suggested_text, mapping)
        if not text.strip() or _CODES.search(text):
            fallbacks.append((f, loc, quote))
            continue
        paragraphs = anchors.node_paragraphs.get(loc)
        if f.missing:
            if not paragraphs:
                fallbacks.append((f, loc, None))
                continue
            source = paragraphs[-1]._p
            tail = tails.get(loc, source)
            exemplar = next((r for r in source.findall(qn("w:r"))
                             if r.find(qn("w:t")) is not None
                             and all(c.tag in (qn("w:rPr"), qn("w:t")) for c in r)), None)
            for line in text.splitlines():
                if not line.strip():
                    continue
                p = OxmlElement("w:p")
                props = deepcopy(source.pPr) if source.pPr is not None else OxmlElement("w:pPr")
                # 不复制分节符，否则接受后多出一个分节。
                for sect in props.findall(qn("w:sectPr")):
                    props.remove(sect)
                # 段落标记与文字各有独立修订编号。
                rp = props.find(qn("w:rPr"))
                if rp is None:
                    rp = OxmlElement("w:rPr")
                    props.append(rp)
                rp.append(revision("ins"))
                p.append(props)
                ins = revision("ins")
                ins.append(text_run(line, exemplar))
                p.append(ins)
                tail.addnext(p)
                index = max(i for i, (e, _) in enumerate(expected)
                            if e is tail or tail in e.iterancestors())
                expected.insert(index + 1, (p, line))
                tail = p
            tails[loc] = tail
        else:
            anchor = anchors.locate(loc, quote, skip_inserted=True)
            if text == quote or "\n" in text or "\r" in text or not anchor.precise:
                fallbacks.append((f, loc, quote))
                continue
            runs = split_runs(anchor)
            p = anchor.paragraph._p
            children = list(p)
            if not runs or any(e.tag != qn("w:r") for e in
                               children[children.index(runs[0]._r):children.index(runs[-1]._r) + 1]):
                fallbacks.append((f, loc, quote))
                continue
            # 从已接受视图中的前缀确定这一次出现，保留先前插入文字的偏移。
            offset = 0
            for child in p:
                if child is runs[0]._r:
                    break
                offset += len(_paragraph_text(child))
            index = next(i for i, (e, _) in enumerate(expected) if e is p)
            old = expected[index][1]
            expected[index] = (p, old[:offset] + text + old[offset + len(quote):])
            deleted, inserted = revision("del"), revision("ins")
            inserted.append(text_run(text, runs[0]._r))
            runs[0]._r.addprevious(deleted)
            deleted.addnext(inserted)
            for r in runs:
                deleted.append(r._r)
                for t in r._r.iter(qn("w:t")):
                    t.tag = qn("w:delText")
        revised += 1
    if not revised:
        raise AppError(409, "REVISED_UNAVAILABLE", "已采纳的问题都无法写成修订，请改用批注版")
    for f, loc, quote in fallbacks:
        anchor = anchors.locate(loc, None if f.missing else quote, skip_inserted=True)
        runs = split_runs(anchor)
        if not anchor.precise:
            runs = anchor.paragraph.runs[:1]
            if not runs:
                r = anchor.paragraph.add_run()
                anchor.paragraph._p.insert(1 if anchor.paragraph._p.pPr is not None else 0, r._r)
                runs = [r]
        comment = document.add_comment(runs, _clean("【未能写成修订，请人工修改】\n" + shared.finding_text(f, run)),
                                       author="商衡SI", initials="SI")
        old_id = str(comment.comment_id)
        counter += 1
        comment._element.set(qn("w:id"), str(counter))
        for e in document.element.iter():
            if e.tag in {qn("w:commentRangeStart"), qn("w:commentRangeEnd"), qn("w:commentReference")} and e.get(qn("w:id")) == old_id:
                e.set(qn("w:id"), str(counter))
    try:
        content = shared._save(anchors, original, lambda a, b: validate(a, b, [t for _, t in expected]))
    except Exception as exc:
        reason = str(exc) if isinstance(exc, shared.AnnotationValidationError) else "PACKAGE_INVALID"
        logger.error("修订版校验失败 task=%s reason=%s", run.task_id, reason)
        raise AppError(500, "REVISED_VALIDATION_FAILED", "生成修订版时校验未通过，未输出文件") from None
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", file.original_name.rsplit(".", 1)[0]) or "文件"
    filename = f"{stem}_商衡SI修订_{datetime.now(ZoneInfo('Asia/Shanghai')):%Y%m%d}.docx"
    return filename, content, revised, len(fallbacks)
