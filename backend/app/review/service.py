"""审核轮次业务逻辑：确认发送（含预检）、轮次列表与详情（还原真实名称）、修改问题处理状态
（SPEC 7.1–7.3、19.5，设计 docs/design/M2.md 第八节）。

- 数据库中的模型文字一律是代号形式；返回前端前用对照表做完整性检查与还原。
- 日志与审计只写数量、轮次号、类别等非敏感信息，不写名称、原文、金额。
- 出参模型就定义在本文件（任务文件范围内没有单独的 schemas 文件）。
"""
import json
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
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
    LlmCall,
    Party,
    RedactionItem,
    RedactionSuspect,
    RequirementResult,
    RequirementVersion,
    ReviewRun,
    Task,
    TaskFile,
    User,
)
from app.models_api import select_model
from app.llm.gateway import describe_reasons
from app.llm import gateway, usage
from app.redaction.engine import build_outbound, learn_confirmed_names, load_redaction_map, load_date_pairs, load_local_values
from app.redaction.recheck import recheck, recheck_context
from app.redaction.restore import check, _LOCATION
from app.review.coverage import describe
from app.review.validate import normalize_unable_reason
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
    model_id: int | None = None
    model_label: str | None = None


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
    unable_reason: Literal["redacted", "absent", "other"] | None = None


class MarkOut(BaseModel):
    model_config = {"from_attributes": True}
    start: int
    end: int
    kind: str


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
    quote_excerpt: str | None = None
    problem: str
    impact: str | None
    suggestion: str | None
    suggested_text: str | None
    basis: str | None
    law_refs: list[dict] = []
    law_unverified: bool = False
    source: str = 'model'
    handling_status: str
    handling_note: str | None
    discussed_text: str | None = None  # 讨论修改稿（已还原），V32-B-03
    discussed_at: UtcDatetime | None = None
    discussed_message_id: int | None = None  # 讨论稿来自哪条回复，对话里据此标“已保存”（V321-E-02）
    use_discussed: bool = False  # 采纳与修订版取讨论修改稿
    marks: dict[str, list[MarkOut]] = {}
    unresolved: int = 0


class Progress(BaseModel):
    done: int
    total: int
    sent: int = 0  # 已通过出口复检并发出的分段数（V31-U5F-01）
    received: int = 0  # 已流式收到的回复字数，各段合计（V31-U5F-03）


class RunDetail(BaseModel):
    receipt: dict | None = None
    restore_stats: dict[str, int] = {}
    template: dict | None = None
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
    model_id: int | None = None
    model_label: str | None = None
    prompt_version: str | None
    builtin_req_version: str | None
    company_req_version: int | None
    queued_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    queue_position: int | None
    chunk_count: int
    progress: Progress | None  # 审核中时的分段进度，其他状态为 null
    coverage: dict | None  # 轮次结束后的 summary 与 notes；处理中和 blocked 为 null
    doc_summary: str | None
    doc_summary_marks: list[MarkOut] = []
    sanctions: Sanctions
    requirement_results: list[RequirementResultOut]
    findings: list[FindingOut]


class FindingPatch(BaseModel):
    handling_status: Literal["pending", "adopted", "ignored"] | None = None
    handling_note: str | None = Field(None, max_length=500)  # 出现且为空表示清除备注
    use_discussed: bool | None = None  # 二选一：原审核建议 / 讨论修改稿（V32-B-03）


class DraftIn(BaseModel):
    """PUT /findings/{fid}/draft：用户核对、编辑后的建议条文（真实文字），本地脱敏后存代号文本；text 为空表示删除。"""
    model_config = {"extra": "forbid"}
    text: str | None = Field(None, max_length=4000)
    message_id: int | None = Field(None, gt=0)


def chosen_text(f) -> tuple[str | None, str]:
    """采纳与导出使用的建议条文及其字段名：选了讨论修改稿且有内容时取讨论稿，否则取原审核建议。"""
    if f.use_discussed and f.discussed_text:
        return f.discussed_text, "discussed_text"
    return f.suggested_text, "suggested_text"


# ---------------------------------------------------------------- 审核上下文


def _applies(values: list, value: str) -> bool:
    return "all" in values or value in values


def company_rows(db: DbSession, *, doc_type: str | None, stance: str | None, source: str | None = None) -> list[CompanyRequirement]:
    """启用且适用的公司补充要求，按 sort、code 排序；doc_type 为 None 表示不按类型筛选。"""
    return [
        row
        for row in db.scalars(select(CompanyRequirement).where(CompanyRequirement.enabled.is_(True))
                              .order_by(CompanyRequirement.sort, CompanyRequirement.code))
        if (doc_type is None or _applies(list(row.doc_types or []), doc_type))
        and (stance is None or _applies(list(row.stances or []), stance))
        and (source is None or _applies(list(row.sources or []), source))
    ]


def collect_requirements(db: DbSession, *, doc_type: str, stance: str, source: str,
                         user_note: str | None) -> list[Requirement]:
    """发送给模型的全部审核要求：内置 → 公司补充（启用且适用）→ 本次补充说明 NOTE。"""
    reqs = select_builtin(doc_type, stance, source)
    reqs.extend(Requirement(id=row.code, source="company", dimension=None, text=row.text)
                for row in company_rows(db, doc_type=doc_type, stance=stance, source=source))
    note = note_requirement(user_note)
    if note is not None:
        reqs.append(note)
    return reqs


def build_context(db: DbSession, task: Task, *, stance: str, source: str, user_note: str | None,
                  main_doc_type: str, file_ids: list[str] | None = None) -> ReviewContext:
    """拼出一次审核的上下文。预检（创建轮次时）和 review 处理函数用同一个函数，保证检查的就是将要发送的内容。

    file_ids 为会话审核轮次所选文件（第一个是主文件，V32-A-02）；None 表示审核任务的全部文件。
    """
    from app.checks.service import local_summary
    from app.checks.ratios import section

    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)))
    if file_ids is None:
        attachments = [f.doc_type for f in files if not f.is_main]
        seqs = ids = None
    else:
        chosen = {str(fid) for fid in file_ids}
        attachments = [f.doc_type for f in files if str(f.id) in chosen and str(f.id) != str(file_ids[0])]
        ids = [f.id for f in files if str(f.id) in chosen]
        seqs = {f.seq for f in files if str(f.id) in chosen}
    built = build_outbound(db, task, "review", file_ids=file_ids, stance=stance)
    return ReviewContext(
        task=task, stance=stance, source=source, user_note=user_note, main_doc_type=main_doc_type,
        attachment_types=[t for t in attachments if t],
        requirements=collect_requirements(db, doc_type=main_doc_type, stance=stance, source=source,
                                          user_note=user_note),
        company_profile=str(get_setting(db, "review.company_profile") or ""),
        outbound_text=built.text,
        local_summary=local_summary(db, task, file_ids=ids),
        ratios=section(db, task, seqs),
        boundary_locs=chunk_boundaries(db, task.id, seqs),
        quote_tables=built.quote_tables,
    )


def chunk_boundaries(db: DbSession, task_id: uuid.UUID, seqs: set[int] | None = None) -> set[str]:
    """分段的首选切分点（设计 docs/design/M5.md 第二节）：每个文件的第一个正文节点、标题、一级条款（clause_no 不含“.”）。"""
    rows = db.execute(
        select(DocNode.loc, DocNode.kind, DocNode.clause_no, TaskFile.seq)
        .join(TaskFile, TaskFile.id == DocNode.file_id)
        .where(DocNode.task_id == task_id, DocNode.part == "body").order_by(TaskFile.seq, DocNode.ord))
    out: set[str] = set()
    seen_files: set[int] = set()
    for loc, kind, clause_no, seq in rows:
        if seqs is not None and seq not in seqs:
            continue
        if seq not in seen_files or kind == "heading" or (clause_no and "." not in clause_no):
            out.add(loc)
        seen_files.add(seq)
    return out


# ---------------------------------------------------------------- 确认发送


def _conflict(code: str, message: str) -> AppError:
    return AppError(409, code, message)


def create_run(db: DbSession, task: Task, user: User, ip: str | None, use_template: bool = True, model_id: int | None = None) -> ReviewRun:
    """POST /tasks/{id}/runs：前置检查 → 预检出口复检 → 建轮次 → 入队 → 任务进入 reviewing。"""
    # 锁住任务行并重读：两个并发的确认请求只有一个能通过状态检查
    task = db.execute(select(Task).where(Task.id == task.id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one()
    # 不外发的任务永远不会进入 awaiting_confirm，先判断策略才能给出准确的错误码（A-04）
    if task.policy == "local_only":
        raise _conflict("LOCAL_ONLY", "该任务的外发策略为不外发，不能提交 AI 审核")
    if task.status != "awaiting_confirm":
        raise _conflict("TASK_STATE_CONFLICT", "当前状态不能提交审核")
    from app.tasks.service import task_busy
    if task_busy(db, task.id):
        raise _conflict('FILES_PARSING', '还有文件正在解析，请稍候')
    if db.scalar(select(exists().where(TaskFile.task_id == task.id, TaskFile.parse_error.is_not(None)))):
        raise _conflict('FILE_PARSE_FAILED', '请先删除解析失败的文件')
    from app.redaction import engine as redaction_engine
    redaction_engine.ensure_default_replacements(db, task)
    our =db.scalar(select(func.count()).select_from(Party)
                    .where(Party.task_id == task.id, Party.is_our_party.is_(True)))
    if our != 1:
        raise _conflict("OUR_PARTY_REQUIRED", "请先确认哪一方是我方")
    main = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.is_main.is_(True))).first()
    if main is None or main.doc_type is None:
        raise _conflict("TASK_STATE_CONFLICT", "文件类型尚未识别，不能提交审核")

    run = _launch(db, task, user, ip, stance=task.stance, source=task.source, user_note=task.user_note_redacted,
                  main_doc_type=main.doc_type, model_id=model_id, use_template=use_template)
    task.status = "reviewing"
    for file in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.confirmed_at.is_(None))):
        file.confirmed_at = task.last_activity_at
    db.commit()
    return run


class SessionRunIn(BaseModel):
    """POST /assistant/sessions/{id}/runs（V32-A-02）。file_ids 不传为全部已确认文件；main_file_id 不传时取会话主文件。"""
    model_config = {"extra": "forbid"}
    stance: Literal["sales", "purchase", "other"]
    source: Literal["incoming", "outgoing"]
    model_id: int | None = Field(None, gt=0)
    user_note: str | None = Field(None, max_length=2000)
    file_ids: list[str] | None = Field(None, min_length=1, max_length=10)
    main_file_id: str | None = None
    expected_material_version: int | None = None
    use_template: bool = True
    confirm_pending: bool = False  # 同一事务里先确认待确认的文件再发起（V321-E-06）


def session_review_active(db: DbSession, task_id: uuid.UUID) -> bool:
    """会话的“审核进行中”：存在排队或进行中的轮次（会话的 task.status 不进入 reviewing）。"""
    return bool(db.scalar(select(exists().where(ReviewRun.task_id == task_id,
                                                ReviewRun.status.in_(("queued", "running"))))))


def create_session_run(db: DbSession, task: Task, user: User, ip: str | None, body: SessionRunIn) -> ReviewRun:
    """会话发起审核：复用审核任务的预检与入队；会话状态保持 active / awaiting_confirm，不重复确认文件。"""
    from app.assistant import service as assistant
    from app.assistant.prompt import selected_files
    from app.db.models import AssistantMessage
    from app.redaction import engine as redaction_engine
    from app.tasks.service import check_material, set_user_note, task_busy
    task = assistant.lock(db, task)
    if task.kind != "assistant" or task.purpose != "chat":
        from app.security.errors import NotFoundError
        raise NotFoundError("TASK_NOT_FOUND", "任务不存在")
    if task.policy == "local_only":
        raise _conflict("LOCAL_ONLY", "该会话的外发策略为不外发，不能提交 AI 审核，可查看本地校验结果")
    assistant._writable(db, task)  # 原件层到期后禁止审核
    check_material(task, body.expected_material_version)
    if task.status not in ("active", "awaiting_confirm") or task_busy(db, task.id):
        raise _conflict("FILES_PARSING" if task.status == "parsing" else "SESSION_BUSY",
                        "还有文件正在解析，请稍候" if task.status == "parsing" else "当前会话状态不能发起审核")
    if session_review_active(db, task.id):
        raise _conflict("SESSION_BUSY", "审核进行中，完成后再发起")
    if db.scalar(select(exists().where(AssistantMessage.task_id == task.id, AssistantMessage.role == "assistant",
                                       AssistantMessage.status.in_(("queued", "streaming"))))):
        raise _conflict("SESSION_BUSY", "回复生成中，完成后再发起审核")
    if body.confirm_pending and task.status == "awaiting_confirm":
        # 确认与发起同一事务：任何一步失败都整体回滚，不会留下“已确认、没发起”的半截状态
        task = assistant.confirm_files(db, task, user, ip)
    files = selected_files(db, task, {} if body.file_ids is None else {"file_ids": body.file_ids})
    files = [f for f in files if not f.media]  # 录音逐字稿不参与审核
    if not files:
        raise AppError(422, "NO_FILES", "请先加入并确认要审核的文件")
    main = next((f for f in files if str(f.id) == body.main_file_id), None) if body.main_file_id else (
        next((f for f in files if f.is_main), files[0]))
    if main is None:
        raise AppError(422, "VALIDATION_ERROR", "主文件不在所选文件中")
    if main.doc_type is None:
        raise _conflict("TASK_STATE_CONFLICT", "文件类型尚未识别，不能提交审核")
    note = (body.user_note or "").strip() or None
    current = crypto.decrypt_text(task.user_note_enc) if task.user_note_enc is not None else None
    if note != current:
        # 补充说明随本轮外发：写入后按会话规则重算（代号只增），材料版本随之 +1
        set_user_note(db, task, note)
        existing = redaction_engine._existing_parties(db, task)
        redaction_engine._run(db, task, redaction_engine._current_overrides(existing)
                              if any(p.confirmed for p in existing.values()) else None, existing)
    # 立场只影响主体说明里的角色文字，不改代号；记为会话当前立场
    task.stance, task.source = body.stance, body.source
    redaction_engine.ensure_default_replacements(db, task)
    our = db.scalar(select(func.count()).select_from(Party)
                    .where(Party.task_id == task.id, Party.is_our_party.is_(True)))
    if our != 1:
        raise _conflict("OUR_PARTY_REQUIRED", "请先确认哪一方是我方")
    file_ids = [str(main.id)] + [str(f.id) for f in files if f.id != main.id]  # 第一个是主文件
    run = _launch(db, task, user, ip, stance=body.stance, source=body.source, user_note=task.user_note_redacted,
                  main_doc_type=main.doc_type, model_id=body.model_id, use_template=body.use_template,
                  file_ids=file_ids)
    db.commit()
    return run


def _launch(db: DbSession, task: Task, user: User, ip: str | None, *, stance: str, source: str,
            user_note: str | None, main_doc_type: str, model_id: int | None, use_template: bool,
            file_ids: list[str] | None = None) -> ReviewRun:
    """审核任务与会话共用：模型与上限 → 预检出口复检 → 记住名称 → 建轮次 → 入队（不提交，不改任务状态）。"""
    from app.redaction import engine as redaction_engine
    model = select_model(db, model_id, "review")
    reached = usage.cap_reached(db, user.id, "review", model.id)
    if reached:
        raise _conflict(reached, gateway.MESSAGES[reached])

    def precheck():
        ctx = build_context(db, task, stance=stance, source=source, user_note=user_note,
                            main_doc_type=main_doc_type, file_ids=file_ids)
        rc = recheck_context(db, task.id)
        return ctx, recheck("\n".join(m["content"] for m in build_messages(ctx)), real_values=rc.real_values,
                            terms=rc.terms, placeholders=rc.placeholders, kept=rc.kept)

    ctx, reasons = precheck()
    if reasons:
        # 已存结果过不了复检（如规则升级前的旧结果）：按当前规则重算一次再查（V31-U5F-02）。
        # 先查后重算，正常情况下外发全文只扫一遍（V31-U5F-03 测试机：10 万字每遍 2～4 秒）
        redaction_engine.refresh_if_blocked(db, task)
        ctx, reasons = precheck()
    if reasons:  # 只报类别和位置，不报真实值
        raise _conflict("OUTBOUND_BLOCKED", f"出口复检发现未脱敏的信息，已拦截，未发送（{describe_reasons(reasons)}）")
    # 9.10：预检通过后写入已记住的名称，与轮次创建同一事务；预检拦截时不写入（Q-73 ①）
    learn_confirmed_names(db, task, user.id)
    from app.redaction.keep_terms import remember
    remember(db, task, user.id)

    run_no = (db.scalar(select(func.max(ReviewRun.run_no)).where(ReviewRun.task_id == task.id)) or 0) + 1
    run = ReviewRun(
        task_id=task.id, run_no=run_no, status="queued", stance=stance, source=source,
        user_note=user_note, main_doc_type=main_doc_type,
        model_id=model.id, model_label=model.display_name, model_name=model.model_name,
        prompt_version=load_system_template()[0], builtin_req_version=builtin_version(),
        company_req_version=db.scalar(select(func.max(RequirementVersion.version))),
        created_by=user.id, queued_at=utcnow(), receipt=_receipt(db, task, model.display_name, file_ids),
        file_ids=file_ids, material_version=task.material_version,
    )
    db.add(run)
    if use_template:
        from app.standards.service import choose
        template=choose(db,task,main_doc_type)
        if template:
            run.template_id=template.id
            run.template_name_snapshot_enc=template.name_enc
            run.template_hash=template.content_hash
    db.flush()
    jobs.enqueue(db, "review", {"task_id": str(task.id), "run_id": str(run.id)})
    task.last_activity_at = utcnow()
    task.latest_run_id = run.id
    task.error_message = None
    audit.record(db, "run_create", user_id=user.id, target_type="run", target_id=str(run.id),
                 detail={"run_no": run_no, "requirements": len(ctx.requirements), "confirm": _confirm_stats(db, task)}, ip=ip)
    return run


def _confirm_stats(db, task):
    from app.redaction.stats import finish
    return finish(db, task)


def _receipt(db, task, model_label, file_ids=None):
    from app.parsing.docx import WARN_COMMENTS, WARN_HEADER_FOOTER
    from app.parsing.xlsx import WARN_HIDDEN_SHEET
    from app.redaction.engine import suspect_views

    protected = dict(db.execute(select(RedactionItem.category, func.count()).where(
        RedactionItem.task_id == task.id).group_by(RedactionItem.category)).all())
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id)))
    if file_ids is not None:
        files = [f for f in files if str(f.id) in {str(fid) for fid in file_ids}]
    not_sent = dict(images=0, comments=0, headers_footers=0, hidden_sheets=0)
    for file in files:
        # V31Q-57：无精确数量的旧警告按含该类内容的文件计数，不猜对象数量。
        warnings = file.warnings or []
        for key, warning in [('comments', WARN_COMMENTS), ('headers_footers', WARN_HEADER_FOOTER),
                             ('hidden_sheets', WARN_HIDDEN_SHEET)]:
            not_sent[key] += int(warning in warnings)
        for warning in warnings:
            match = re.search(r'文件中有 (\d+) 个图片或嵌入对象', warning)
            if match:
                not_sent['images'] += int(match[1])
    return dict(model=model_label, protected=protected, protected_total=sum(protected.values()),
                kept=sum(v['kept_by'] is not None for v in suspect_views(db, task)),
                date_shifted=load_date_pairs(db, task.id) is not None,
                ratios_sent=False, files=len(files), not_sent=not_sent,
                sent_at=None, calls=0, chars=0, rechecks=0, blocked=0)


def sent_text(db, run, user, ip):
    rows = list(db.scalars(select(LlmCall).where(LlmCall.run_id == run.id, LlmCall.status == 'sent')
                           .order_by(LlmCall.created_at, LlmCall.id)))
    audit.record(db, 'view_sent', user_id=user.id, target_type='run', target_id=str(run.id), ip=ip)
    db.commit()
    if any(row.request_text is None for row in rows):
        return {'available': False}
    items = []
    for seq, row in enumerate(rows, 1):
        text = '\n'.join(m['content'] for m in json.loads(row.request_text)['messages'] if m['role'] == 'user')
        items.append(dict(seq=seq, chars=len(text), text=text))
    return items


# ---------------------------------------------------------------- 轮次列表与详情


def list_runs(db: DbSession, task: Task) -> RunList:
    rows = db.scalars(select(ReviewRun).where(ReviewRun.task_id == task.id).order_by(ReviewRun.run_no.desc()))
    return RunList(items=[RunListItem(id=r.id, run_no=r.run_no, status=r.status, queued_at=r.queued_at,
                                      finished_at=r.finished_at, model_name=r.model_name, model_id=r.model_id, model_label=r.model_label) for r in rows])


class _View:
    """一个任务共用的代号、日期还原与文件位置上下文。"""

    def __init__(self, db: DbSession, task_id: uuid.UUID):
        self.db = db
        self.task_id = task_id
        self.mapping = load_redaction_map(db, task_id)
        self.dates = load_date_pairs(db, task_id)
        self.locals = {}
        self.restore_stats = dict(restored=0, marked=0)
        self.file_names = dict(db.execute(select(TaskFile.seq, TaskFile.original_name)
                                          .where(TaskFile.task_id == task_id)).all())

    def text(self, value: str | None) -> str | None:
        return self.checked(value).text if value else value

    def checked(self, value: str | None, *, clause=False, loc=None):
        locs = {m.group().strip('[]') for m in _LOCATION.finditer(value or '')}
        if loc and loc not in self.locals:
            self.locals[loc] = load_local_values(self.db, self.task_id, loc)
        result = check(value, self.mapping, clause=clause, locations=self.readable(locs), dates=self.dates,
                       local=self.locals.get(loc))
        self.restore_stats['restored'] += result.restored
        self.restore_stats['marked'] += len(result.marks)
        return result

    def locations(self, findings: list[Finding]) -> dict[str, str]:
        return self.readable({f.locs[0] for f in findings if f.locs})

    def readable(self, locs) -> dict[str, str]:
        """位置集合 → “原文件名 + 空格 + 可读位置”（设计 docs/design/M3.md 6.3）。

        可读位置：有 clause_no 时为 clause_no；否则 PDF、图片节点为“第 N 页”，XLSX 节点为“工作表!单元格”，
        其他为 loc。节点已被删除时只显示 loc。
        """
        if not locs:
            return {}
        rows = {r.loc: r for r in self.db.execute(
            select(DocNode.loc, DocNode.file_id, DocNode.clause_no, DocNode.page, DocNode.sheet, DocNode.cell_ref)
            .where(DocNode.task_id == self.task_id).order_by(DocNode.file_id, DocNode.ord))}
        parents = {}
        nearest = {}
        for row in rows.values():
            clause = row.clause_no or ''
            if re.fullmatch(r'第[^条章\n]{1,20}[条章]', clause):
                nearest[row.file_id] = clause
            elif re.fullmatch(r'[（(]?(?:\d+|[A-Za-z]|[一二三四五六七八九十百]+)[）).、]?', clause):
                parents[row.loc] = nearest.get(row.file_id)
        out = {}
        for loc in locs:
            r = rows.get(loc)
            if r is None:
                shown = loc
            elif r.clause_no:
                shown = f'{parents[loc]} {r.clause_no}' if parents.get(loc) else r.clause_no
            elif r.sheet and r.cell_ref:
                shown = f"{r.sheet}!{r.cell_ref}"
            elif r.page:
                shown = f"第 {r.page} 页"
            else:
                shown = loc
            m = _LOC_FILE_SEQ.match(loc)
            name = self.file_names.get(int(m.group(1))) if m else None
            out[loc] = f"{name} {shown}" if name else shown
        return out

    def finding(self, f: Finding, locations: dict[str, str]) -> FindingOut:
        quote = crypto.decrypt_text(f.quote_snapshot_enc) if f.quote_snapshot_enc is not None else None
        locs = [str(x) for x in (f.locs or [])]
        checked = {key: self.checked(getattr(f, key), clause=key in ('suggested_text', 'discussed_text'),
                                     loc=locs[0] if locs else None)
                   for key in ('title', 'problem', 'impact', 'suggestion', 'suggested_text', 'basis', 'discussed_text')}
        marks = {key: list(value.marks) for key, value in checked.items() if value.marks}
        values = {key: value.text if getattr(f, key) is not None else None for key, value in checked.items()}
        return FindingOut(
            id=f.id, seq=f.seq, category=f.category, severity=f.severity,
            req_ids=[str(x) for x in (f.req_ids or [])], locs=locs,
            location=locations.get(locs[0]) if locs else None, missing=f.missing, cite_status=f.cite_status,
            quote=quote, quote_excerpt=crypto.decrypt_text(f.quote_orig_enc) if f.quote_orig_enc is not None else None,
            **values, marks=marks, unresolved=sum(len(v) for v in marks.values()),
            handling_status=f.handling_status, handling_note=f.handling_note,
            discussed_at=f.discussed_at, discussed_message_id=f.discussed_message_id if f.discussed_text else None, use_discussed=bool(f.use_discussed and f.discussed_text),
            law_refs=f.law_refs, law_unverified=f.law_unverified,
            source=f.source,
        )


def _progress(run: ReviewRun) -> Progress | None:
    """处理中的进度暂存在 coverage.progress（Q-78 ③）。"""
    if run.status != "running" or not isinstance(run.coverage, dict):
        return None
    p = run.coverage.get("progress")
    if not isinstance(p, dict):
        return None
    return Progress(done=int(p.get("done", 0)), total=int(p.get("total", run.chunk_count)), sent=int(p.get("sent", 0)), received=int(p.get("received", 0)))


def _coverage(run: ReviewRun, view: _View) -> dict | None:
    if (run.status in ("queued", "running", "blocked") or not isinstance(run.coverage, dict)
            or "progress" in run.coverage):
        return None
    raw = run.coverage
    requirements = raw.get("requirements")
    if not isinstance(requirements, dict):
        return None
    total = int(requirements.get("total", 0))
    issue = int(requirements.get("issue", 0))
    ok = int(requirements.get("ok", 0))
    na = int(requirements.get("na", 0))
    unable = int(requirements.get("unable", 0))
    reasons = raw.get("unable_by_reason", {"redacted": 0, "absent": 0, "other": unable})
    # 无法判断的原因是模型输出（使用代号），与审核要求列表一样还原为真实名称后显示
    raw = {**raw, "unable": [{**item, "note": view.text(item.get("note"))} for item in raw.get("unable", [])]}
    return {
        "summary": (f"本次计划审核 {total} 条要求，审核失败，未取得有效 AI 结论" if run.status == 'failed' else
                    f"本次按 {total} 条审核要求审核：有问题 {issue} 条，未发现问题 {ok} 条，"
                    f"不适用 {na} 条，无法判断 {unable} 条（信息已脱敏 {reasons['redacted']}、"
                    f"文件未写 {reasons['absent']}、其他 {reasons['other']}）"),
        "unable_by_reason": reasons,
        "ai_instructions": int(raw.get("ai_instructions", 0)),
        "notes": describe(raw, view.file_names),
    }


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
    from app.standards.service import snapshot
    summary = view.checked(run.doc_summary)
    detail = RunDetail(
        receipt=run.receipt,
        template=snapshot(db,run),
        id=run.id, task_id=run.task_id, run_no=run.run_no, status=run.status, error_message=run.error_message,
        stance=run.stance, source=run.source, user_note=view.text(run.user_note), main_doc_type=run.main_doc_type,
        model_name=run.model_name, model_id=run.model_id, model_label=run.model_label, prompt_version=run.prompt_version, builtin_req_version=run.builtin_req_version,
        company_req_version=run.company_req_version, queued_at=run.queued_at, started_at=run.started_at,
        finished_at=run.finished_at, queue_position=queue_position,
        chunk_count=run.chunk_count, progress=_progress(run), coverage=_coverage(run, view),
        doc_summary=summary.text if run.doc_summary is not None else None, doc_summary_marks=list(summary.marks),
        sanctions=Sanctions(level=run.sanctions_level,
                            reasons=[view.text(str(r)) or "" for r in (run.sanctions_reasons or [])]),
        requirement_results=[RequirementResultOut(req_id=r.req_id, req_source=r.req_source, req_text=view.text(r.req_text) or "",
                                                  dimension=r.dimension, verdict=r.verdict, note=view.text(r.note),
                                                  unable_reason=normalize_unable_reason(r.verdict, r.unable_reason))
                             for r in results],
        findings=[view.finding(f, locations) for f in findings],
    )
    # 临时问题只供阅读：返回不可用于写接口或讨论的独立标识，最终合并后重新生成正式问题。
    if run.status in ("queued", "running"):
        for finding, output in zip(findings, detail.findings):
            if finding.seq < 0:
                output.id = uuid.uuid5(uuid.NAMESPACE_URL, "provisional-finding:" + str(finding.id))
    detail.restore_stats = view.restore_stats
    return detail


# ---------------------------------------------------------------- 修改问题处理状态


def update_finding(db: DbSession, finding: Finding, user: User, patch: FindingPatch) -> FindingOut:
    if finding.seq < 0:
        raise AppError(409, "REVIEW_IN_PROGRESS", "审核完成后才能处理问题")
    fields = patch.model_fields_set
    if "handling_status" in fields and patch.handling_status is None:
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    if not fields & {"handling_status", "handling_note", "use_discussed"}:
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    if "use_discussed" in fields:
        if patch.use_discussed is None or (patch.use_discussed and not finding.discussed_text):
            raise AppError(422, "VALIDATION_ERROR", "该问题还没有讨论修改稿")
        finding.use_discussed = patch.use_discussed
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


def save_draft(db: DbSession, finding: Finding, user: User, body: DraftIn) -> FindingOut:
    """讨论稿存为该问题的修改建议（V32-B-03）。

    用户核对后的文字是真实文字：与提问相同，按本会话对照表与识别规则在本地脱敏，新代号只增写入对照表，
    以代号文本存入 discussed_text；原 suggested_text 不变。导出时与原建议走同一条还原与 clause 校验路径。
    """
    if finding.seq < 0:
        raise AppError(409, "REVIEW_IN_PROGRESS", "审核完成后才能保存讨论稿")
    from app.assistant import service as assistant
    from app.db.models import AssistantMessage, RedactionMap
    from app.redaction import engine
    task = assistant.lock(db, db.get(Task, finding.task_id))
    if task.kind != "assistant" or task.purpose != "chat":
        from app.security.errors import NotFoundError
        raise NotFoundError("FINDING_NOT_FOUND", "问题不存在")
    assistant._writable(db, task)
    text = (body.text or "").strip()
    if not text:
        finding.discussed_text = finding.discussed_message_id = finding.discussed_at = None
        finding.use_discussed = False
    else:
        if body.message_id is not None:
            message = db.get(AssistantMessage, body.message_id)
            if (message is None or message.task_id != task.id or message.role != "assistant"
                    or message.finding_id != finding.id):
                from app.security.errors import NotFoundError
                raise NotFoundError("MESSAGE_NOT_FOUND", "消息不存在")
        db.scalar(select(RedactionMap).where(RedactionMap.task_id == task.id).with_for_update())
        result = assistant._question(db, task, text)
        if result.new_entries:
            engine.write_full_map(db, task.id, {**engine._load_full_map(db, task.id), **result.new_entries})
        finding.discussed_text = result.text
        finding.discussed_message_id = body.message_id
        finding.discussed_at = utcnow()
        finding.use_discussed = True  # 刚保存的讨论稿默认作为采纳来源，可在问题卡改回原审核建议
    finding.updated_by = user.id
    finding.updated_at = utcnow()
    task.last_activity_at = utcnow()
    db.commit()
    view = _View(db, finding.task_id)
    return view.finding(finding, view.locations([finding]))
