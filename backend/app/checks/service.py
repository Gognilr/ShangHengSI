"""本地校验编排、加密持久化、纯文本摘要与本地查询。"""
import re
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select

from app.checks.amounts import QuoteTableCheck, check_tables, check_uppercase
from app.checks.consistency import check_consistency, compared_tables, contract_amount
from app.checks.dates import check_dates
from app.checks.installments import check_installments
from app.checks.references import check_references
from app.checks.model import CheckFile, CheckNode, CheckResult
from app.db.models import DocNode, LocalFinding, TaskFile
from app.redaction.tables import table_cell
from app.security import crypto
from app.laws.index import LawIndex, load as load_laws


@dataclass
class Evaluation:
    results: list[CheckResult]
    tables: list[QuoteTableCheck]


def evaluate(nodes: list[CheckNode], files: list[CheckFile], today: date, laws: LawIndex | None = None) -> Evaluation:
    tables, results = check_tables(nodes, files)
    results += check_uppercase(nodes)
    results += check_consistency(nodes, files, tables)
    results += check_dates(nodes, today)
    results += check_installments(nodes, files)
    results += check_references(nodes, laws=laws)
    order = {code: i for i, code in enumerate(("L-01", "L-02", "L-03", "L-INFO", "L-04", "L-05", "L-06", "L-07", "L-08", "L-09", "L-10", "L-11"))}
    positions = {node.loc: i for i, node in enumerate(nodes)}
    results.sort(key=lambda r: (order[r.code], r.file_seq or 0, positions.get(r.locs[0], -1) if r.locs else -1))
    return Evaluation(results, tables)


def load_inputs(db, task_id: uuid.UUID) -> tuple[list[CheckNode], list[CheckFile]]:
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task_id).order_by(TaskFile.seq)))
    seqs = {f.id: f.seq for f in files}
    rows = db.scalars(select(DocNode).join(TaskFile, TaskFile.id == DocNode.file_id)
                      .where(DocNode.task_id == task_id, DocNode.part == "body").order_by(TaskFile.seq, DocNode.ord))
    nodes = [CheckNode(n.loc, crypto.decrypt_text(n.text_enc) if n.text_enc else "", seqs[n.file_id], n.clause_no) for n in rows]
    return nodes, [CheckFile(f.seq, f.is_main, f.ext, f.doc_type, list(f.warnings or [])) for f in files]


def run_checks(db, task, today: date) -> None:
    nodes, files = load_inputs(db, task.id)
    titles = {title for node in nodes for title in re.findall(r'《([^《》]+)》', node.text)}
    evaluation = evaluate(nodes, files, today, laws=load_laws(db, titles))
    file_ids = dict(db.execute(select(TaskFile.seq, TaskFile.id).where(TaskFile.task_id == task.id)).all())
    loc_files = dict(db.execute(select(DocNode.loc, DocNode.file_id).where(DocNode.task_id == task.id)).all())
    db.execute(delete(LocalFinding).where(LocalFinding.task_id == task.id))
    for result in evaluation.results:
        file_id = loc_files.get(result.locs[0]) if result.locs else None
        db.add(LocalFinding(task_id=task.id, file_id=file_id or file_ids.get(result.file_seq),
                            check_code=result.code, severity=result.severity, locs=result.locs,
                            detail_enc=crypto.encrypt_text(result.detail), ai_summary=result.ai_summary))
    db.flush()


def table_notes(nodes, files) -> dict[str, str]:
    tables, _ = check_tables(nodes, files)
    cross = check_consistency(nodes, files, tables)
    total = contract_amount(nodes, files)
    compared = {t.table for t in compared_tables(nodes, files, tables)}
    notes = {}
    for table in tables:
        if not table.recognized:
            notes[table.table] = "未识别报价表结构，未做金额核验"
            continue
        parts = ["金额未核验" if not table.rows_checked else
                 f"金额计算有 {table.row_errors} 行不一致" if table.row_errors else "金额计算一致"]
        if table.rows_skipped:
            parts.append(f"{len(table.rows_skipped)} 行未核验")
        if table.total_ok is not None:
            parts.append("合计一致" if table.total_ok else "合计不一致")
        if total is not None and table.table in compared:
            codes = {r.code for r in cross if r.locs and table_cell(r.locs[-1])
                     and table_cell(r.locs[-1])[0] == table.table}
            parts.append("币种与合同总价不一致" if "L-06" in codes else
                         "合计与合同总价不一致" if "L-05" in codes else "合计与合同总价一致")
        notes[table.table] = "，".join(parts)
    return notes


def local_summary(db, task) -> str:
    rows = db.scalars(select(LocalFinding).where(LocalFinding.task_id == task.id,LocalFinding.check_code!='L-12').order_by(LocalFinding.id))
    return "\n".join(f"[{r.check_code}] {r.ai_summary}" for r in rows)


def list_findings(db, task) -> list[dict]:
    if task.status in ("parsing", "parse_failed"):
        return []
    files = {f.id: f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id))}
    # 位置只取未加密元数据；报告在原文清理后仍可复用。
    nodes = {r.loc: r for r in db.execute(select(DocNode.loc, DocNode.sheet, DocNode.cell_ref, DocNode.clause_no)
                                         .where(DocNode.task_id == task.id))}
    rows = list(db.scalars(select(LocalFinding).where(LocalFinding.task_id == task.id).order_by(LocalFinding.id)))
    rows.sort(key=lambda r: ({"high": 0, "medium": 1, "low": 2}[r.severity], r.id))
    out = []
    for row in rows:
        file = files.get(row.file_id)
        name = file.original_name if file else ""
        loc = row.locs[0] if row.locs else ""
        node = nodes.get(loc)
        table = re.search(r"-T(\d+)-R(\d+)-C\d+$", loc)
        if node and node.sheet and node.cell_ref:
            shown = f"{node.sheet} {node.cell_ref}"
        elif table:
            shown = f"表{table[1]} 第{table[2]}行"
        else:
            shown = node.clause_no if node and node.clause_no else loc
        out.append(dict(id=row.id, check_code=row.check_code, severity=row.severity, locs=row.locs,
                        file_id=str(row.file_id) if row.file_id else None, file_name=name,
                        location=" ".join(x for x in (name, shown) if x), detail=crypto.decrypt_text(row.detail_enc)))
    return out
