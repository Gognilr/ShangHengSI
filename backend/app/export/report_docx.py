"""SPEC 14 Word 报告；只读轮次快照、校验结果和节点位置元数据。"""
import io
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from sqlalchemy import select

from app.checks.service import list_findings
from app.db.models import ReviewRun, Task, TaskFile, User
from app.review.coverage import ai_instruction_notice
from app.review.prompt import DOC_TYPE_LABELS, SOURCE_LABELS, STANCE_LABELS
from app.review.service import run_detail

DECLARATION = "本报告由 AI 辅助生成，仅供内部参考，不构成法律意见。重大事项及高风险交易请由法务、合规人员或律师确认。"
SANCTIONS_NOTE = "未联网核查，基于模型自身知识，需合规人员确认最新制裁与出口管制规定"
SEVERITY = {"high": "高", "medium": "中", "low": "低"}
CATEGORY = {"wording": "表述", "logic": "逻辑", "legal": "法律", "compliance": "合规与制裁", "commercial": "商务"}
CITE = {"verified": "引用已校验", "unverified": "待核实", "missing": "缺失型"}
HANDLING = {"pending": "待处理", "adopted": "已采纳", "ignored": "已忽略"}
VERDICT = {"issue": "有问题", "ok": "未发现问题", "na": "不适用", "unable": "无法判断"}
CHECKS = dict(zip(("L-01", "L-02", "L-03", "L-04", "L-05", "L-06", "L-07", "L-08", "L-INFO"),
                  ("行金额", "合计", "税额", "大小写一致", "跨文件金额", "币种一致", "报价有效期", "交期与签约日", "提示")))
CHECKS.update({'L-09': '分期金额', 'L-10': '废止法律', 'L-11': '增值税税率'})
SANCTIONS = {"none": "无明显风险", "attention": "需注意", "high": "高风险（建议合规人员或律师确认）"}
# 页眉标志：「衡线」单色稿（docs/design/v35/brand/mark-mono.svg 的位图）
MARK = Path(__file__).with_name("assets") / "mark-mono.png"


def _clean(value) -> str:
    text = str(value) if value is not None and value != "" else "—"
    return "".join(c for c in text if c in "\t\n\r" or "\x20" <= c <= "\ud7ff" or "\ue000" <= c <= "\ufffd" or "\U00010000" <= c <= "\U0010ffff")


def _font(run, size, bold=False):
    run.font.name = "Calibri"
    run.font.size = Pt(size)
    run.bold = bold
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "宋体")


def header(doc, text="商衡SI"):
    """页眉：单色标志 + 一行小字；导出各处共用。"""
    paragraph = doc.sections[0].header.paragraphs[0]
    paragraph.clear()
    paragraph.add_run().add_picture(str(MARK), height=Cm(0.42))
    _font(paragraph.add_run(" " + text), 9)


def _new_document(title_text="商务文件审核报告"):
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Cm(29.7), Cm(21)
    section.top_margin = section.bottom_margin = Cm(1.5)
    section.left_margin = section.right_margin = Cm(1.5)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10.5)
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "宋体")
    normal.paragraph_format.space_after = Pt(6)
    header(doc)
    title = doc.add_paragraph(style="Title")
    # 清除 python-docx 默认 Title 的主题色和底边线，报告标题只用黑色文字。
    title_style = doc.styles["Title"]
    for border in list(title_style.element.xpath("./w:pPr/w:pBdr")):
        border.getparent().remove(border)
    title_run = title.add_run(title_text)
    _font(title_run, 16, True)
    title_run.font.color.rgb = RGBColor(0, 0, 0)
    return doc


def _heading(doc, title):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(8)
    _font(paragraph.add_run(title), 10.5, True)


def _marked(value, marks=()):
    """按检查结果的码点位置分段，再清理 XML 不接受的字符，避免高亮偏移。"""
    if not value:
        return [(_clean(value), False)]
    parts, cursor = [], 0
    for mark in marks:
        if cursor < mark.start:
            parts.append((_clean(value[cursor:mark.start]), False))
        parts.append((_clean(value[mark.start:mark.end]), True))
        cursor = mark.end
    if cursor < len(value):
        parts.append((_clean(value[cursor:]), False))
    return parts


def _lines(lines):
    result = []
    for line in lines:
        if result:
            result.append(('\n', False))
        result.extend(line)
    return result or [('—', False)]


def _table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.autofit = False
    for col, width in zip(table.columns, widths):
        col.width = Cm(width)
    for cell, text in zip(table.rows[0].cells, headers):
        _font(cell.paragraphs[0].add_run(_clean(text)), 9, True)
    repeat = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for values in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, values):
            parts = value if isinstance(value, list) else [(_clean(value), False)]
            for text, highlight in parts:
                run = cell.paragraphs[0].add_run(text)
                _font(run, 9)
                if highlight:
                    run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    # 列与单元格宽度同时设置，避免 Word 使用默认等宽布局。
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = Cm(width)
    return table


def _files(db, task):
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
    main = next((file for file in files if file.is_main), None)
    return files, main


def _filename(main, task, prefix="审核报告"):
    name = main.original_name if main else task.title
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", stem) or "文件"
    return f"{prefix}_{stem}_{datetime.now(ZoneInfo('Asia/Shanghai')):%Y%m%d}.docx"


def _basic(doc, task, files, main, user, run=None):
    _heading(doc, "一 基本信息")
    filenames = [main.original_name if main else task.title] + [f"附件：{file.original_name}" for file in files if file != main]
    if run is None:
        version, when = "—", "—"
    else:
        version = f"内置 {run.builtin_req_version or '—'}；" + (
            f"公司补充要求 第 {run.company_req_version} 版" if run.company_req_version is not None else "公司补充要求：无")
        when = run.finished_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M") if run.finished_at else "—"
    rows = [("文件名", "\n".join(filenames)),
            ("文件类型", DOC_TYPE_LABELS.get(run.main_doc_type if run else main.doc_type if main else None, "—")),
            ("立场", STANCE_LABELS.get(run.stance if run else task.stance, "—")),
            ("来源", SOURCE_LABELS.get(run.source if run else task.source, "—")),
            ("审核人", user.display_name), ("审核时间", when), ("模型", (f"{run.model_label}（{run.model_name}）" if run.model_label else run.model_name) if run else "—"),
            ("审核要求版本", version), ("轮次", f"第 {run.run_no} 轮" if run else "—")]
    _table(doc, ("项目", "内容"), rows, (3.5, 23.2))
    if run is None:
        doc.add_paragraph("未做 AI 审核")


def _local(doc, db, task):
    _heading(doc, "五 本地校验结果")
    items = list_findings(db, task)
    if not items:
        doc.add_paragraph("本地校验未发现问题")
    else:
        _table(doc, ("校验项", "严重程度", "位置", "说明"),
               [(CHECKS.get(row["check_code"], row["check_code"]), SEVERITY.get(row["severity"], row["severity"]),
                 row["location"], row["detail"]) for row in items], (3, 2, 5.5, 16.2))


def _finish(doc):
    _heading(doc, "七 固定声明")
    doc.add_paragraph(DECLARATION)
    data = io.BytesIO()
    doc.save(data)
    return data.getvalue()


CARRY = {"new": "本轮新增", "persists": "仍存在", "unchanged": "仍存在（条款未改动）", "resolved": "已解决"}


def _changes(doc, db, run, main):
    """增量轮：结论摘要后加“与上一稿的变化”（计数和未留痕清单，diff.md 3.5）。现算，原文已清理时只写计数。"""
    from app.review import delta as delta_mod, drafts
    doc.add_paragraph().add_run("与上一稿的变化").bold = True
    summary = (run.coverage or {}).get("delta", {}).get("summary") or {}
    doc.add_paragraph(f"对照第 {run.coverage['delta'].get('prev_no')} 稿：修改 {summary.get('mod', 0)} 处 · "
                      f"新增 {summary.get('add', 0)} 段 · 删除 {summary.get('del', 0)} 段 · 位置调整 {summary.get('move', 0)} 处 · "
                      f"未留痕 {summary.get('untracked', 0)} 处")
    base = db.get(ReviewRun, run.base_run_id) if run.base_run_id is not None else None
    prev = delta_mod.main_file(db, base) if base is not None and base.task_id == run.task_id else None
    old = drafts.body_nodes(db, prev) if prev is not None else None
    new = drafts.body_nodes(db, main) if main is not None else None
    result = drafts.compare(old, new, mode=drafts._mode(main)) if old is not None and new is not None else None
    rows = []
    for c in (result or {}).get("changes", []):
        if not c.get("untracked") or c["type"] == "format":
            continue
        b = c["brief"]
        excerpt = b.get("text") or f"{b['ctx']}{b['old']} → {b['new']}"
        rows.append((c["clause"] or "—", {"mod": "修改", "add": "新增", "del": "删除", "move": "位置调整"}.get(c["type"], ""),
                     _clean(excerpt)))
    if rows:
        _table(doc, ("位置", "类型", "未留痕的改动"), rows, (5, 2.5, 19.2))


def _run_files(db, run):
    task = db.get(Task, run.task_id)
    files, main = _files(db, task)
    if run.file_ids:
        files_by_id = {str(file.id): file for file in files}
        files = [files_by_id[str(fid)] for fid in run.file_ids if str(fid) in files_by_id]
        main = files_by_id.get(str(run.file_ids[0]))
    return task, files, main


def _problem_lines(finding):
    problem = [_marked(finding.title, finding.marks.get('title', ())),
               _marked(finding.problem, finding.marks.get('problem', ()))]
    if finding.source == 'template':
        problem.insert(0, [('范本对照', False)])
    for field, label in (('impact', '对我方的影响：'), ('basis', '依据：')):
        value = getattr(finding, field)
        if value:
            problem.append([(label, False)] + _marked(value, finding.marks.get(field, ())))
    for ref in finding.law_refs:
        problem.append([(f"依据：《{_clean(ref['title'])}》{_clean(ref.get('article_no') or '')}\n{_clean(ref['text'])}", False)])
    if finding.law_unverified:
        problem.append([('引用未经法规库核实', False)])
    return problem


def build_issue_list(db, run: ReviewRun) -> tuple[str, bytes]:
    """按所选轮次导出可供人工修改的条文，生成过程只使用审核记录。"""
    task, _, main = _run_files(db, run)
    detail = run_detail(db, run, include_pages=True)
    doc = _new_document("文件审核问题清单")
    doc.add_paragraph(f"{_clean(main.original_name if main else task.title)} · 第 {run.run_no} 轮")
    doc.add_paragraph("请对照原件核实问题与建议条文。标黄的文字未能还原，请核对。")
    if run.status == "partial":
        doc.add_paragraph("本轮仅完成部分审核，请查看审核报告中的覆盖说明。")
    groups = {code: [] for code in SEVERITY}
    for finding in detail.findings:
        if finding.handling_status == "ignored" or finding.carry == "resolved":
            continue
        location = finding.location or ("缺失项" if finding.missing else "—")
        problem = _problem_lines(finding)
        if finding.cite_status == "unverified":
            problem.append([("原文引用待核实", False)])
        field = "discussed_text" if finding.use_discussed else "suggested_text"
        suggestion = _marked(getattr(finding, field), finding.marks.get(field, ()))
        groups[finding.severity].append((location, _lines(problem), suggestion))
    for severity, rows in groups.items():
        if rows:
            _heading(doc, f"{SEVERITY[severity]}风险问题 {len(rows)} 条")
            _table(doc, ("原页码与条款", "问题与依据", "建议条文"), rows, (7.0, 11.2, 8.5))
    if not any(groups.values()):
        doc.add_paragraph("本轮没有可列出的问题。")
    doc.add_paragraph(DECLARATION)
    data = io.BytesIO()
    doc.save(data)
    return _filename(main, task, "问题清单"), data.getvalue()


def build_run_report(db, run: ReviewRun, user: User) -> tuple[str, bytes]:
    task, files, main = _run_files(db, run)
    detail = run_detail(db, run)  # 复用结果页的位置、覆盖说明和还原，不读取节点原文。
    # run_detail 已对模型字段、NOTE 及要求文字做一次还原，引用快照也是原文；不能再次替换已还原值。
    text = _clean

    doc = _new_document()
    _basic(doc, task, files, main, user, run)
    if detail.template:
        state={'modified':'（范本已修改）','deleted':'（范本已删除）'}.get(detail.template['status'],'')
        doc.add_paragraph('本轮对照公司范本：'+text(detail.template['name'])+state)
    _heading(doc, "二 覆盖说明与限制事项")
    if detail.coverage:
        doc.add_paragraph(text(detail.coverage["summary"]))
        if detail.coverage.get("ai_instructions"):
            _font(doc.add_paragraph().add_run(ai_instruction_notice(detail.coverage["ai_instructions"])), 10.5, True)
        for note in detail.coverage["notes"]:
            doc.add_paragraph(text(note))
    else:
        doc.add_paragraph("本轮次没有覆盖说明")
    _heading(doc, "三 结论摘要")
    live = [f for f in detail.findings if getattr(f, "carry", None) != "resolved"]  # 已解决的不计入问题数（diff.md 4.3）
    doc.add_paragraph("；".join(f"{label}问题 {sum(f.severity == code for f in live)} 条" for code, label in SEVERITY.items()))
    delta = (detail.coverage or {}).get("delta")
    if delta:
        doc.add_paragraph("本轮新增 {} 条 · 仍存在 {} 条 · 已解决 {} 条".format(
            sum(f.carry == "new" for f in detail.findings), sum(f.carry in ("persists", "unchanged") for f in detail.findings),
            sum(f.carry == "resolved" for f in detail.findings)))
    doc.add_paragraph("制裁结论：" + SANCTIONS.get(detail.sanctions.level, "—"))
    for reason in detail.sanctions.reasons:
        doc.add_paragraph(text(reason))
    doc.add_paragraph(SANCTIONS_NOTE)
    if delta:
        _changes(doc, db, run, main)
    _heading(doc, "四 问题清单")
    rows = []
    for finding in detail.findings:
        problem = _problem_lines(finding)
        if finding.unresolved:
            problem.append([(f'注：本问题有 {finding.unresolved} 处文字未能还原（已标黄），请核对。', False)])
        suggestion = ([_marked(finding.suggestion, finding.marks.get('suggestion', ()))] if finding.suggestion else [])
        if finding.suggested_text:
            label = '原审核建议措辞：' if finding.use_discussed else '建议措辞：'
            suggestion.append([(label, False)] + _marked(finding.suggested_text, finding.marks.get('suggested_text', ())))
        if finding.use_discussed and finding.discussed_text:  # V32-B-03：所选的讨论修改稿
            suggestion.append([('讨论修改稿（已选用）：', False)]
                              + _marked(finding.discussed_text, finding.marks.get('discussed_text', ())))
        handling = HANDLING.get(finding.handling_status, finding.handling_status)
        if finding.handling_note:
            handling += "\n" + _clean(finding.handling_note)
        row = (finding.seq, SEVERITY.get(finding.severity, finding.severity), CATEGORY.get(finding.category, finding.category),
               finding.location or ("缺失项" if finding.missing else "—"), text(finding.quote), _lines(problem),
               _lines(suggestion), CITE.get(finding.cite_status, finding.cite_status), handling)
        if delta:  # 增量轮加一列“本轮状态”
            state = CARRY.get(finding.carry or "new", "")
            row += (state + ("\n" + _clean(finding.carry_note) if finding.carry_note else ""),)
        rows.append(row)
    if rows and delta:
        _table(doc, ("序号", "等级", "类别", "位置", "原文", "问题说明", "修改建议", "引用状态", "处理状态与备注", "本轮状态"),
               rows, (1, 1, 1.3, 2.6, 4, 5, 4.6, 1.8, 2.6, 2.6))
    elif rows:
        _table(doc, ("序号", "等级", "类别", "位置", "原文", "问题说明", "修改建议", "引用状态", "处理状态与备注"),
               rows, (1, 1, 1.5, 3, 4.5, 5.5, 5, 2.2, 3))
    else:
        doc.add_paragraph("本次没有列出问题条目。结果由 AI 生成，仅供参考，请以人工复核为准。")
    _local(doc, db, task)
    _heading(doc, "六 审核要求逐条结论")
    if detail.requirement_results:
        _table(doc, ("编号", "内容", "结论", "备注"),
               [(row.req_id, text(row.req_text), VERDICT.get(row.verdict, row.verdict), text(row.note))
                for row in detail.requirement_results], (2, 13, 2.7, 9))
    else:
        doc.add_paragraph("本轮次没有审核要求逐条结论")
    return _filename(main, task), _finish(doc)


def build_local_report(db, task: Task, user: User) -> tuple[str, bytes]:
    files, main = _files(db, task)
    doc = _new_document()
    _basic(doc, task, files, main, user)
    _local(doc, db, task)
    return _filename(main, task), _finish(doc)
