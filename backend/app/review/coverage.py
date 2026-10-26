"""审核覆盖说明的结构化记录和中文描述（SPEC 11.6，设计 docs/design/M5.md 第四节）。"""
import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocNode, Task, TaskFile
from app.parsing.model import LOW_OCR_CONF
from app.review.prompt import ReviewContext
from app.review.validate import ValidatedReview

_FILE_SEQ = re.compile(r"^F(\d+)-")
_PART_LABELS = {
    "header": "页眉",
    "footer": "页脚",
    "comment": "批注",
    "textbox": "文本框",
    "hidden_sheet": "隐藏工作表的单元格",
}
_PART_ORDER = {part: index for index, part in enumerate(_PART_LABELS)}
_LABEL_ORDER = {label: index for index, label in enumerate(_PART_LABELS.values())}
_VERDICTS = ("issue", "ok", "na", "unable")


def build_coverage(db: Session, task: Task, ctx: ReviewContext, review: ValidatedReview | None,
                   chunk_count: int, failed_chunks: list[dict]) -> dict:
    """只保存统计、位置编号和固定警告，不复制节点原文或审核文件名。"""
    requirement_counts = dict.fromkeys(_VERDICTS, 0)
    unable: list[dict] = []
    if review is not None:
        for result in review.requirement_results:
            if result.verdict in requirement_counts:
                requirement_counts[result.verdict] += 1
            if result.verdict == "unable":
                unable.append({"req_id": result.req_id, "note": result.note})

    excluded: Counter[tuple[int, str]] = Counter()
    low_pages: dict[int, set[int]] = defaultdict(set)
    rows = db.execute(
        select(TaskFile.seq, DocNode.part, DocNode.kind, DocNode.page, DocNode.ocr_conf)
        .join(TaskFile, TaskFile.id == DocNode.file_id)
        .where(DocNode.task_id == task.id)
        .order_by(TaskFile.seq, DocNode.ord)
    )
    for seq, part, kind, page, confidence in rows:
        if part != "body":
            excluded[(seq, part)] += 1
        if (part == "body" and kind == "ocr_block" and confidence is not None
                and confidence < LOW_OCR_CONF and page is not None):
            low_pages[seq].add(page)

    excluded_rows = [
        {"file": seq, "part": part, "count": count}
        for (seq, part), count in sorted(
            excluded.items(), key=lambda item: (item[0][0], _PART_ORDER.get(item[0][1], 99), item[0][1])
        )
    ]
    ocr_low = [{"file": seq, "pages": sorted(pages)} for seq, pages in sorted(low_pages.items()) if pages]

    quote_tables = []
    for table in ctx.quote_tables:
        match = _FILE_SEQ.match(table.loc)
        if match and not table.detailed:
            quote_tables.append({"file": int(match.group(1)), "loc": table.loc, "rows": table.rows})

    warnings: list[dict] = []
    files = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq))
    for file in files:
        for warning in file.warnings or []:
            warnings.append({"file": file.seq, "text": str(warning)})

    is_chunked = chunk_count > 1
    failed = [
        {key: chunk.get(key) for key in ("index", "first_loc", "last_loc", "reason")}
        for chunk in failed_chunks
    ]
    return {
        "requirements": {"total": len(ctx.requirements), **requirement_counts},
        "unable": unable,
        "excluded": excluded_rows,
        "quote_tables": quote_tables,
        "ocr_low": ocr_low,
        "warnings": warnings,
        "chunked": is_chunked,
        "chunk_count": chunk_count,
        "failed_chunks": failed,
        "no_cross_chunk_check": is_chunked,
        "sanctions_offline": True,
        "law_refs": dict(ctx.law_stats),
    }


def describe(coverage: dict, file_names: dict[int, str]) -> list[str]:
    """把结构化覆盖信息转成结果页和报告共用的固定中文句子。"""
    notes: list[str] = []
    laws = coverage.get('law_refs', {})
    if laws.get('enabled'):
        notes.append(f"本次附带相关法条 {int(laws.get('sent',0))} 条（因出口复检丢弃 {int(laws.get('dropped',0))} 条）")

    def file_name(seq: int) -> str:
        return file_names.get(seq, f"文件{seq}")

    grouped: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for item in coverage.get("excluded", []):
        part = item.get("part")
        label = _PART_LABELS.get(part)
        if label and int(item.get("count", 0)) > 0:
            grouped[int(item["file"])].append((label, int(item["count"])))
    for seq in sorted(grouped):
        grouped[seq].sort(key=lambda row: _LABEL_ORDER.get(row[0], 99))
        parts = "、".join(
            f"{label} {count} {'个' if label == '隐藏工作表的单元格' else '处'}"
            for label, count in grouped[seq]
        )
        notes.append(f"{file_name(seq)}：{parts}未参与 AI 审核")

    for item in coverage.get("quote_tables", []):
        notes.append(
            f"{file_name(int(item['file']))} {item['loc']} 报价明细表共 {int(item['rows'])} 行，"
            "超过 200 行，只把汇总信息发给 AI，行级数据未逐行审核"
        )

    low_files: set[int] = set()
    for item in coverage.get("ocr_low", []):
        seq = int(item["file"])
        pages = sorted({int(page) for page in item.get("pages", [])})
        if pages:
            low_files.add(seq)
            page_text = "、".join(str(page) for page in pages)
            notes.append(f"{file_name(seq)} 第 {page_text} 页 OCR 识别置信度低，请核对原件")

    for item in coverage.get("warnings", []):
        seq, warning = int(item["file"]), str(item["text"])
        if "OCR 识别置信度低" in warning and seq in low_files:
            continue
        notes.append(f"{file_name(seq)}：{warning}")

    unable = coverage.get("unable", [])
    if unable:
        entries = []
        for item in unable:
            req_id = str(item["req_id"])
            note = item.get("note")
            # note 允许为 null；此时只展示编号，避免构造模型未提供的解释。
            entries.append(f"{req_id}（{note}）" if note else req_id)
        notes.append("无法判断的审核要求：" + "、".join(entries))

    if coverage.get("chunked"):
        notes.append(f"文件较长，分 {int(coverage.get('chunk_count', 0))} 段审核，未做跨分段的一致性检查")

    for item in coverage.get("failed_chunks", []):
        notes.append(
            f"第 {item['index']} 段（{item['first_loc']} 至 {item['last_loc']}）审核失败："
            f"{item['reason']}，该部分没有审核结果"
        )
    return notes
