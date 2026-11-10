"""只在内存生成批注副本，保留原包部件并校验全部正文文字。"""
from io import BytesIO
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from zipfile import ZipFile

from docx import Document
from lxml import etree
from sqlalchemy import select

from app.checks.service import list_findings
from app.db.models import DocNode, Finding, Task, TaskFile
from app.export.docx_anchor import AnchorMismatch, MISMATCH, load_anchors, split_runs
from app.export.report_docx import CATEGORY, CHECKS, SEVERITY, _clean
from app.redaction.engine import load_redaction_map, load_date_pairs, load_local_values
from app.redaction.restore import restore, check
from app.review.service import run_detail
from app.security import crypto
from app.security.errors import AppError
from app.tasks import storage

logger = logging.getLogger(__name__)
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MUTABLE = {"word/document.xml", "word/comments.xml", "[Content_Types].xml", "word/_rels/document.xml.rels"}


class AnnotationValidationError(Exception):
    """原因代码不含文档内容。"""


def _parts(data):
    with ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def safe_xml(xml: bytes):
    """与解析时相同的加固解析器（V31Q-114）：不解析实体、不加载 DTD、不访问网络。"""
    return etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                                                 huge_tree=False))


def _text(xml):
    root = safe_xml(xml)
    return "".join(e.text or "" for e in root.iter()
                   if e.tag in {_W + "t", _W + "delText", _W + "instrText"})


def validate(original: bytes, result: bytes) -> None:
    Document(BytesIO(result))
    before, after = _parts(original), _parts(result)
    if _text(before["word/document.xml"]) != _text(after["word/document.xml"]):
        raise AnnotationValidationError("BODY_TEXT_CHANGED")
    if set(before) - set(after) or any(
        after.get(name) != value for name, value in before.items() if name not in _MUTABLE
    ) or set(after) - set(before) - _MUTABLE:
        raise AnnotationValidationError("PART_CHANGED")


def _save(anchors, original, validator=None):
    stream = BytesIO()
    anchors.document.save(stream)
    generated = _parts(stream.getvalue())
    # python-docx 会重序列化样式、页眉等 XML；这些部件直接保留原始字节。
    output = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(output, "w") as target:
        for info in source.infolist():
            target.writestr(info, generated[info.filename] if info.filename in _MUTABLE else source.read(info.filename))
        for name in generated.keys() - set(source.namelist()):
            if name not in _MUTABLE:
                raise AnnotationValidationError("UNEXPECTED_PART")
            target.writestr(name, generated[name])
    result = output.getvalue()
    (validator or validate)(original, result)
    return result


def _belongs(locs, seq, is_main=False):
    first = locs[0] if locs else ""
    match = re.match(r"^F(\d+)-", first)
    return int(match[1]) == seq if match else is_main


def check_available(run, file):
    if run.status not in ("completed", "partial"):
        raise AppError(409, "ANNOTATED_UNAVAILABLE", "本轮尚未完成")
    if file.ext not in ("docx", "doc", "wps", "rtf"):
        raise AppError(409, "ANNOTATED_UNAVAILABLE", "该文件格式不支持批注版，只能导出审核报告")
    if file.original_deleted_at is not None:
        raise AppError(409, "ORIGINAL_DELETED", "原件已按保留期删除，不能生成批注版")
    if file.ext != "docx" and not file.converted_storage_key:
        raise AppError(409, "ANNOTATED_UNAVAILABLE", "该文件在升级前上传，没有保存转换结果，请重新上传后审核")


def load_source(db, file):
    try:
        original = storage.load_original(file.storage_key if file.ext == "docx" else file.converted_storage_key)
    except FileNotFoundError:
        raise AppError(409, "ORIGINAL_DELETED", "原件已按保留期删除，不能生成批注版") from None
    stored = {n.loc: crypto.decrypt_text(n.text_enc) if n.text_enc is not None else ""
              for n in db.scalars(select(DocNode).where(DocNode.file_id == file.id))}
    try:
        return original, load_anchors(original, file.seq, stored)
    except AnchorMismatch:
        raise AppError(409, "ANCHOR_MISMATCH", MISMATCH) from None


def finding_text(f, run):
    lines = [f"【{SEVERITY.get(f.severity, f.severity)} · {CATEGORY.get(f.category, f.category)}】{f.title}",
             f"问题：{f.problem}", f"建议：{f.suggestion or '—'}"]
    if f.suggested_text:
        lines.append(f"建议条文：{f.suggested_text}")
    if f.basis:
        lines.append(f"依据：{f.basis}")
    for ref in f.law_refs:
        lines.append(f"依据：《{ref['title']}》{ref.get('article_no') or ''} {ref['text']}")
    if f.unresolved:
        examples = list(dict.fromkeys(getattr(f, field)[mark.start:mark.end]
                                      for field, marks in f.marks.items() for mark in marks))[:3]
        shown = '、'.join(f'“{raw}”' for raw in examples)
        lines.append(f"※ 有 {f.unresolved} 处文字未能还原（如{shown}），请核对。")
    lines.append(f"—— 商衡SI 第 {run.run_no} 轮 · 问题 {f.seq}")
    return "\n".join(lines)


def build_annotated(db, run, file: TaskFile):
    check_available(run, file)
    task = db.get(Task, run.task_id)
    detail = run_detail(db, run)
    # 没有文件位置的任务级问题挂在主文件；跨文件问题只按首个位置归属。
    findings = sorted((f for f in detail.findings if f.handling_status != "ignored" and
                       _belongs(f.locs, file.seq, file.is_main)), key=lambda f: f.seq)
    local = sorted((r for r in list_findings(db, task) if
                    (_belongs(r["locs"], file.seq) or
                     (not r["locs"] and (r["file_id"] == str(file.id) or
                                        (r["file_id"] is None and file.is_main))))), key=lambda r: r["id"])
    if not findings and not local:
        raise AppError(409, "ANNOTATED_UNAVAILABLE", "本轮没有可批注的问题")
    original, anchors = load_source(db, file)
    mapping = load_redaction_map(db, task.id)
    dates = load_date_pairs(db, task.id)
    quotes = {row.id: row for row in db.execute(select(Finding.id, Finding.quote_model, Finding.quote_orig_enc)
                                               .where(Finding.run_id == run.id))}
    unanchored = 0

    def add(loc, quote, text, location, count_fallback=True):
        nonlocal unanchored
        anchor = anchors.locate(loc, quote)
        runs = split_runs(anchor)
        if not anchor.precise:
            if count_fallback:
                unanchored += 1
                text = f"【未能精确定位，原位置：{location or loc or '无位置'}】\n" + text
            paragraph = anchor.paragraph
            runs = paragraph.runs[:1]
            if not runs:
                run = paragraph.add_run()
                paragraph._p.insert(1 if paragraph._p.pPr is not None else 0, run._r)
                runs = [run]
        anchors.document.add_comment(runs, _clean(text), author="商衡SI", initials="SI")

    for f in findings:
        loc = f.locs[0] if f.locs else None
        row = quotes[f.id]
        quote = (crypto.decrypt_text(row.quote_orig_enc) if row.quote_orig_enc is not None else
                 check(row.quote_model or '', mapping, dates=dates,
                       local=load_local_values(db, task.id, loc)).text)
        add(loc, quote, finding_text(f, run), f.location)
    for row in local:
        text = (f"【本地校验 · {SEVERITY.get(row['severity'], row['severity'])}】"
                f"{CHECKS.get(row['check_code'], row['check_code'])}\n"
                f"{restore(row['detail'], mapping)}\n—— 商衡SI 本地校验")
        # 本地校验没有 quote，按设计挂在其位置段首，不计模型引用定位失败。
        add(row["locs"][0] if row["locs"] else None, None, text, row["location"], False)
    try:
        content = _save(anchors, original)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, AnnotationValidationError) else "PACKAGE_INVALID"
        logger.error("批注版校验失败 task=%s reason=%s", task.id, reason)
        raise AppError(500, "ANNOTATED_VALIDATION_FAILED", "生成批注版时校验未通过，未输出文件") from None
    stem = file.original_name.rsplit(".", 1)[0]
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", stem) or "文件"
    filename = f"{stem}_商衡SI批注_{datetime.now(ZoneInfo('Asia/Shanghai')):%Y%m%d}.docx"
    return filename, content, len(findings) + len(local), unanchored
