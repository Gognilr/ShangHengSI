"""审核轮次业务逻辑：确认发送（含预检）、轮次列表与详情（还原真实名称）、修改问题处理状态
（SPEC 7.1–7.3、19.5，设计 docs/design/M2.md 第八节）。

- 数据库中的模型文字一律是代号形式；返回前端前用对照表 restore 还原（19.1）。
- 日志与审计只写数量、轮次号、类别等非敏感信息，不写名称、原文、金额。
- 出参模型就定义在本文件（任务文件范围内没有单独的 schemas 文件）。
"""
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.admin.settings_store import get_setting
from app.audit import service as audit
from app.db import jobs
from app.db.base import utcnow
from app.db.models import (
    CompanyRequirement,
    DocNode,
    Finding,
    Job,
    Party,
    RedactionSuspect,
    RequirementResult,
    RequirementVersion,
    ReviewRun,
    Task,
    TaskFile,
    User,
)
from app.llm.gateway import format_reasons
from app.redaction.engine import build_outbound_text, load_redaction_map
from app.redaction.recheck import recheck, recheck_context
from app.redaction.restore import restore
from app.review.prompt import ReviewContext, build_messages, note_requirement
from app.review.requirements import Requirement, builtin_version, load_system_template, select_builtin
from app.security import crypto
from app.security.errors import AppError
from app.tasks.schemas import UtcDatetime

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_LOC_FILE_SEQ = re.compile(r"^F(\d+)-")


# ---------------------------------------------------------------- 出参


class RunCreated(BaseModel):
    id: uuid.UUID
    run_no: int
    status: str


class RunListItem(BaseModel):
    id: uuid.UUID
    run_no: int
    status: str
    queued_at: UtcDatetime
    finished_at: UtcDatetime | None
    model_name: str | None


class RunList(BaseModel):
    items: list[RunListItem]


class Sanctions(BaseModel):
    level: str | None
    reasons: list[str]


class RequirementResultOut(BaseModel):
    req_id: str
    req_source: str
    req_text: str
    dimension: str | None
    verdict: str
    note: str | None


class FindingOut(BaseModel):
    id: uuid.UUID
    seq: int
    title: str
    category: str
    severity: str
    req_ids: list[str]
    locs: list[str]
    location: str | None
    missing: bool
    cite_status: str
    quote: str | None
    problem: str
    impact: str | None
    suggestion: str | None
    suggested_text: str | None
    basis: str | None
    handling_status: str
    handling_note: str | None


class RunDetail(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    run_no: int
    status: str
    error_message: str | None
    stance: str
    source: str
    user_note: str | None
    main_doc_type: str
    model_name: str | None
    prompt_version: str | None
    builtin_req_version: str | None
    company_req_version: int | None
    queued_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    queue_position: int | None
    coverage: dict | None  # M5 才有覆盖说明
    doc_summary: str | None
    sanctions: Sanctions
    requirement_results: list[RequirementResultOut]
    findings: list[FindingOut]


class FindingPatch(BaseModel):
    handling_status: Literal["pending", "adopted", "ignored"] | None = None
    handling_note: str | None = Field(None, max_length=500)  # 出现且为空表示清除备注


# ---------------------------------------------------------------- 审核上下文


def _applies(values: list, value: str) -> bool:
    return "all" in values or value in values


def collect_requirements(db: DbSession, *, doc_type: str, stance: str, source: str,
                         user_note: str | None) -> list[Requirement]:
    """发送给模型的全部审核要求：内置 → 公司补充（启用且适用）→ 本次补充说明 NOTE。"""
    reqs = select_builtin(doc_type, stance, source)
    for row in db.scalars(select(CompanyRequirement).where(CompanyRequirement.enabled.is_(True))
                          .order_by(CompanyRequirement.sort, CompanyRequirement.code)):
        if _applies(list(row.doc_types or []), doc_type) and _applies(list(row.stances or []), stance):
            reqs.append(Requirement(id=row.code, source="company", dimension=None, text=row.text))
    note = note_requirement(user_note)
    if note is not None:
        reqs.append(note)
    return reqs


def build_context(db: DbSession, task: Task, *, stance: str, source: str, user_note: str | None,
                  main_doc_type: str) -> ReviewContext:
    """拼出一次审核的上下文。预检（创建轮次时）和 review 处理函数用同一个函数，保证检查的就是将要发送的内容。"""
    attachments = list(db.scalars(
        select(TaskFile.doc_type).where(TaskFile.task_id == task.id, TaskFile.is_main.is_(False))
        .order_by(TaskFile.seq)))
    return ReviewContext(
        task=task, stance=stance, source=source, user_note=user_note, main_doc_type=main_doc_type,
        attachment_types=[t for t in attachments if t],
        requirements=collect_requirements(db, doc_type=main_doc_type, stance=stance, source=source,
                                          user_note=user_note),
        company_profile=str(get_setting(db, "review.company_profile") or ""),
        outbound_text=build_outbound_text(db, task),
    )


# ---------------------------------------------------------------- 确认发送


def _conflict(code: str, message: str) -> AppError:
    return AppError(409, code, message)


def create_run(db: DbSession, task: Task, user: User, ip: str | None) -> ReviewRun:
    """POST /tasks/{id}/runs：前置检查 → 预检出口复检 → 建轮次 → 入队 → 任务进入 reviewing。"""
    # 锁住任务行并重读：两个并发的确认请求只有一个能通过状态检查
    task = db.execute(select(Task).where(Task.id == task.id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one()
    # 不外发的任务永远不会进入 awaiting_confirm，先判断策略才能给出准确的错误码（A-04）
    if task.policy == "local_only":
        raise _conflict("LOCAL_ONLY", "该任务的外发策略为不外发，不能提交 AI 审核")
    if task.status != "awaiting_confirm":
        raise _conflict("TASK_STATE_CONFLICT", "当前状态不能提交审核")
    pending = db.scalar(select(func.count()).select_from(RedactionSuspect)
                        .where(RedactionSuspect.task_id == task.id, RedactionSuspect.decision == "pending"))
    if pending:
        raise _conflict("SUSPECTS_PENDING", "还有疑似遗漏的敏感信息未处理，请先逐项处理")
    our = db.scalar(select(func.count()).select_from(Party)
                    .where(Party.task_id == task.id, Party.is_our_party.is_(True)))
    if our != 1:
        raise _conflict("OUR_PARTY_REQUIRED", "请先确认哪一方是我方")
    main = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.is_main.is_(True))).first()
    if main is None or main.doc_type is None:
        raise _conflict("TASK_STATE_CONFLICT", "文件类型尚未识别，不能提交审核")

    ctx = build_context(db, task, stance=task.stance, source=task.source, user_note=task.user_note,
                        main_doc_type=main.doc_type)
    rc = recheck_context(db, task.id)
    reasons = recheck("\n".join(m["content"] for m in build_messages(ctx)), real_values=rc.real_values,
                      terms=rc.terms)
    if reasons:  # 只报类别和位置，不报真实值
        raise _conflict("OUTBOUND_BLOCKED", f"出口复检发现未脱敏的信息，已拦截，未发送（{format_reasons(reasons)}）")

    run_no = (db.scalar(select(func.max(ReviewRun.run_no)).where(ReviewRun.task_id == task.id)) or 0) + 1
    run = ReviewRun(
        task_id=task.id, run_no=run_no, status="queued", stance=task.stance, source=task.source,
        user_note=task.user_note, main_doc_type=main.doc_type,
        model_name=str(get_setting(db, "model.name") or "").strip()[:100] or None,
        prompt_version=load_system_template()[0], builtin_req_version=builtin_version(),
        company_req_version=db.scalar(select(func.max(RequirementVersion.version))),
        created_by=user.id, queued_at=utcnow(),
    )
    db.add(run)
    db.flush()
    jobs.enqueue(db, "review", {"task_id": str(task.id), "run_id": str(run.id)})
    task.status = "reviewing"
    task.latest_run_id = run.id
    task.error_message = None
    audit.record(db, "run_create", user_id=user.id, target_type="run", target_id=str(run.id),
                 detail={"run_no": run_no, "requirements": len(ctx.requirements)}, ip=ip)
    db.commit()
    return run


# ---------------------------------------------------------------- 轮次列表与详情


def list_runs(db: DbSession, task: Task) -> RunList:
    rows = db.scalars(select(ReviewRun).where(ReviewRun.task_id == task.id).order_by(ReviewRun.run_no.desc()))
    return RunList(items=[RunListItem(id=r.id, run_no=r.run_no, status=r.status, queued_at=r.queued_at,
                                      finished_at=r.finished_at, model_name=r.model_name) for r in rows])


class _View:
    """一个任务的还原上下文：对照表和文件名只读一次。"""

    def __init__(self, db: DbSession, task_id: uuid.UUID):
        self.db = db
        self.task_id = task_id
        self.mapping = load_redaction_map(db, task_id)
        self.file_names = dict(db.execute(select(TaskFile.seq, TaskFile.original_name)
                                          .where(TaskFile.task_id == task_id)).all())

    def text(self, value: str | None) -> str | None:
        return restore(value, self.mapping) if value else value

    def locations(self, findings: list[Finding]) -> dict[str, str]:
        """第一个位置 → “原文件名 + 空格 + (clause_no 或 loc)”。节点已被清理时只显示 loc。"""
        firsts = {f.locs[0] for f in findings if f.locs}
        if not firsts:
            return {}
        clause = dict(self.db.execute(select(DocNode.loc, DocNode.clause_no)
                                      .where(DocNode.task_id == self.task_id, DocNode.loc.in_(firsts))).all())
        out = {}
        for loc in firsts:
            shown = clause.get(loc) or loc
            m = _LOC_FILE_SEQ.match(loc)
            name = self.file_names.get(int(m.group(1))) if m else None
            out[loc] = f"{name} {shown}" if name else shown
        return out

    def finding(self, f: Finding, locations: dict[str, str]) -> FindingOut:
        quote = crypto.decrypt_text(f.quote_snapshot_enc) if f.quote_snapshot_enc is not None else None
        locs = [str(x) for x in (f.locs or [])]
        return FindingOut(
            id=f.id, seq=f.seq, title=self.text(f.title) or "", category=f.category, severity=f.severity,
            req_ids=[str(x) for x in (f.req_ids or [])], locs=locs,
            location=locations.get(locs[0]) if locs else None, missing=f.missing, cite_status=f.cite_status,
            quote=quote, problem=self.text(f.problem) or "", impact=self.text(f.impact),
            suggestion=self.text(f.suggestion), suggested_text=self.text(f.suggested_text), basis=self.text(f.basis),
            handling_status=f.handling_status, handling_note=f.handling_note,
        )


def run_detail(db: DbSession, run: ReviewRun) -> RunDetail:
    view = _View(db, run.task_id)
    findings = sorted(db.scalars(select(Finding).where(Finding.run_id == run.id)),
                      key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), f.seq))
    locations = view.locations(findings)
    results = db.scalars(select(RequirementResult).where(RequirementResult.run_id == run.id)
                         .order_by(RequirementResult.id))
    queue_position = None
    job_id = db.scalar(select(Job.id).where(Job.type == "review", Job.payload["run_id"].astext == str(run.id)))
    if job_id is not None:
        queue_position = jobs.queue_position(db, job_id)
    return RunDetail(
        id=run.id, task_id=run.task_id, run_no=run.run_no, status=run.status, error_message=run.error_message,
        stance=run.stance, source=run.source, user_note=run.user_note, main_doc_type=run.main_doc_type,
        model_name=run.model_name, prompt_version=run.prompt_version, builtin_req_version=run.builtin_req_version,
        company_req_version=run.company_req_version, queued_at=run.queued_at, started_at=run.started_at,
        finished_at=run.finished_at, queue_position=queue_position, coverage=run.coverage,
        doc_summary=view.text(run.doc_summary),
        sanctions=Sanctions(level=run.sanctions_level,
                            reasons=[view.text(str(r)) or "" for r in (run.sanctions_reasons or [])]),
        requirement_results=[RequirementResultOut(req_id=r.req_id, req_source=r.req_source, req_text=r.req_text,
                                                  dimension=r.dimension, verdict=r.verdict, note=view.text(r.note))
                             for r in results],
        findings=[view.finding(f, locations) for f in findings],
    )


# ---------------------------------------------------------------- 修改问题处理状态


def update_finding(db: DbSession, finding: Finding, user: User, patch: FindingPatch) -> FindingOut:
    fields = patch.model_fields_set
    if "handling_status" in fields and patch.handling_status is None:
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    if not fields & {"handling_status", "handling_note"}:
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    if "handling_status" in fields:
        finding.handling_status = patch.handling_status
    if "handling_note" in fields:
        note = (patch.handling_note or "").strip()
        finding.handling_note = note or None
    finding.updated_by = user.id
    finding.updated_at = utcnow()
    db.commit()
    view = _View(db, finding.task_id)
    return view.finding(finding, view.locations([finding]))
