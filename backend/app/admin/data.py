"""数据页：备份设置与立即备份、备份记录、原件保留期、外发记录、系统状态（SPEC 15.5、16、17.2，设计 M7 第九节）。

- 外发记录只读，不写审计；详情原样返回脱敏后的外发文本与返回内容（17.2 允许管理员查看），管理接口不返回任何任务原文。
- 日期筛选按 Asia/Shanghai 的整天计算（与审核历史相同）。
"""
import re
import shutil
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.admin.settings_store import get_setting, set_setting
from app.audit import service as audit
from app.backup.job import enqueue_backup
from app.config import get_settings
from app.db.models import BackupRecord, Job, LlmCall, User
from app.db.session import get_db
from app.security.deps import require_admin
from app.security.errors import AppError, NotFoundError
from app.security.sessions import client_ip

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

PAGE_SIZE = 20
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().tz or "Asia/Shanghai")


def _free(path: str) -> Optional[int]:
    try:
        return shutil.disk_usage(Path(path)).free
    except OSError:
        return None


# ---------------------------------------------------------------- 备份设置与记录


class BackupSettings(BaseModel):
    time: str
    keep: int
    dir: str


class BackupSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: str
    keep: int = Field(ge=1, le=60)


class BackupRow(BaseModel):
    id: int
    trigger: str
    status: str
    file_name: Optional[str]
    size_bytes: Optional[int]
    sha256: Optional[str]
    error_message: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]

    @classmethod
    def of(cls, r: BackupRecord) -> "BackupRow":
        return cls(id=r.id, trigger=r.trigger, status=r.status, file_name=r.file_name, size_bytes=r.size_bytes,
                   sha256=r.sha256, error_message=r.error_message, started_at=r.started_at,
                   finished_at=r.finished_at)


class BackupRecords(BaseModel):
    dir: str
    free_bytes: Optional[int]
    latest: Optional[BackupRow]
    items: list[BackupRow]


def _backup_settings(db: DbSession) -> BackupSettings:
    return BackupSettings(time=str(get_setting(db, "backup.time")), keep=int(get_setting(db, "backup.keep")),
                          dir=get_settings().backup_dir)


@router.get("/settings/backup", response_model=BackupSettings)
def get_backup_settings(db: DbSession = Depends(get_db)) -> BackupSettings:
    return _backup_settings(db)


@router.put("/settings/backup", response_model=BackupSettings)
def put_backup_settings(
    body: BackupSettingsIn, request: Request, admin: User = Depends(require_admin), db: DbSession = Depends(get_db)
) -> BackupSettings:
    value = body.time.strip()
    if not _TIME.fullmatch(value):
        raise AppError(422, "VALIDATION_ERROR", "请求参数不正确")
    changed = []
    for key, new in (("backup.time", value), ("backup.keep", body.keep)):
        if get_setting(db, key) != new:
            set_setting(db, key, new, admin.id)
            changed.append(key)
    if changed:
        audit.record(db, "setting_update", user_id=admin.id, target_type="setting", target_id="backup",
                     detail={"keys": changed}, ip=client_ip(request))
    db.commit()
    return _backup_settings(db)


@router.post("/backup/run", status_code=202)
def run_backup_now(
    request: Request, admin: User = Depends(require_admin), db: DbSession = Depends(get_db)
) -> dict:
    if enqueue_backup(db, trigger="manual", user_id=admin.id) is None:
        db.rollback()
        raise AppError(409, "BACKUP_RUNNING", "已有备份正在进行")
    db.commit()
    return {"queued": True}


@router.get("/backup/records", response_model=BackupRecords)
def backup_records(db: DbSession = Depends(get_db)) -> BackupRecords:
    rows = db.scalars(select(BackupRecord).order_by(BackupRecord.id.desc()).limit(20)).all()
    items = [BackupRow.of(r) for r in rows]
    directory = get_settings().backup_dir
    return BackupRecords(dir=directory, free_bytes=_free(directory), latest=items[0] if items else None, items=items)


# ---------------------------------------------------------------- 原件保留期


class RetentionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_days: int
    result_days: int
    outbound_days: int
    audit_days: int
    unfinished_days: int


@router.get("/settings/retention", response_model=RetentionSettings)
def get_retention(db: DbSession = Depends(get_db)) -> RetentionSettings:
    return RetentionSettings(**{name: int(get_setting(db, "retention." + name) or 0)
                                for name in RetentionSettings.model_fields})


@router.put("/settings/retention", response_model=RetentionSettings)
def put_retention(
    body: RetentionSettings, request: Request, admin: User = Depends(require_admin), db: DbSession = Depends(get_db)
) -> RetentionSettings:
    values = body.model_dump()
    if any(value not in (0, 30, 90, 180, 365) for value in values.values()):
        raise AppError(422, "VALIDATION_ERROR", "请为数据保留期选择 30、90、180、365 天或永久")
    if body.result_days != 0 and (body.original_days == 0 or body.original_days > body.result_days):
        raise AppError(422, "RETENTION_ORDER", "原件与原文的保留期不能长于审核结果")
    changed = []
    for name, value in values.items():
        key = "retention." + name
        if get_setting(db, key) != value:
            set_setting(db, key, value, admin.id)
            changed.append(key)
    pending = bool(get_setting(db, "retention.review_pending"))
    if pending:
        set_setting(db, "retention.review_pending", False, admin.id)
    if changed or pending:
        audit.record(db, "setting_update", user_id=admin.id, target_type="setting", target_id="retention",
                     detail={"keys": changed or ["retention.review_pending"]}, ip=client_ip(request))
    db.commit()
    return body


# ---------------------------------------------------------------- 外发记录


class OutboundRow(BaseModel):
    id: int
    created_at: datetime
    user_id: Optional[uuid.UUID]
    user_name: Optional[str]
    task_title: Optional[str]
    purpose: str
    model_name: str
    chars: int
    status: str
    tokens_in: int
    tokens_out: int


class OutboundDetail(OutboundRow):
    request_text: Optional[str]
    response_text: Optional[str]
    block_reason: Optional[str]
    error_message: Optional[str]
    body_notice: Optional[str] = None


class OutboundPage(BaseModel):
    items: list[OutboundRow]
    total: int
    page: int
    page_size: int


def _outbound_fields(call: LlmCall, user_name: Optional[str]) -> dict:
    return dict(
        id=call.id, created_at=call.created_at, user_id=call.user_id, user_name=user_name,
        task_title=call.task_title, purpose=call.purpose, model_name=call.model_name,
        chars=len(call.request_text) if call.status != "blocked" and call.request_text else 0,
        status=call.status, tokens_in=call.tokens_in, tokens_out=call.tokens_out,
    )


@router.get("/outbound-logs", response_model=OutboundPage)
def list_outbound(
    user_id: Optional[uuid.UUID] = Query(None),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    status: Optional[Literal["sent", "blocked", "error"]] = Query(None),
    page: int = Query(1, ge=1, le=100000),
    db: DbSession = Depends(get_db),
) -> OutboundPage:
    conds = []
    if user_id is not None:
        conds.append(LlmCall.user_id == user_id)
    if status is not None:
        conds.append(LlmCall.status == status)
    tz = _tz()
    if date_from is not None:
        conds.append(LlmCall.created_at >= datetime.combine(date_from, time.min, tz))
    if date_to is not None:
        conds.append(LlmCall.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tz))
    total = db.scalar(select(func.count()).select_from(LlmCall).where(*conds)) or 0
    rows = db.execute(
        select(LlmCall, User.display_name)
        .outerjoin(User, User.id == LlmCall.user_id)
        .where(*conds)
        .order_by(LlmCall.created_at.desc(), LlmCall.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return OutboundPage(items=[OutboundRow(**_outbound_fields(c, name)) for c, name in rows],
                        total=total, page=page, page_size=PAGE_SIZE)


@router.get("/outbound-logs/{log_id}", response_model=OutboundDetail)
def get_outbound(log_id: str, db: DbSession = Depends(get_db)) -> OutboundDetail:
    not_found = NotFoundError("OUTBOUND_LOG_NOT_FOUND", "外发记录不存在")
    if not log_id.isdigit() or int(log_id) > 2**63 - 1:
        raise not_found
    row = db.execute(
        select(LlmCall, User.display_name).outerjoin(User, User.id == LlmCall.user_id)
        .where(LlmCall.id == int(log_id))
    ).first()
    if row is None:
        raise not_found
    call, name = row
    # 网关 sent/error 必存请求正文；两段正文均为 NULL 表示已清理。
    # blocked 原本就不存正文，不误标；之后改保留设置也不让已清理的说明消失。
    cleared = call.status in ("sent", "error") and call.request_text is None and call.response_text is None
    return OutboundDetail(**_outbound_fields(call, name), request_text=call.request_text,
                          response_text=call.response_text, block_reason=call.block_reason,
                          error_message=call.error_message,
                          body_notice="正文已按保留期清除" if cleared else None)


# ---------------------------------------------------------------- 系统状态


class SystemStatus(BaseModel):
    version: str
    queued_jobs: int
    data_free_bytes: Optional[int]


@router.get("/system/status", response_model=SystemStatus)
def system_status(db: DbSession = Depends(get_db)) -> SystemStatus:
    s = get_settings()
    queued = db.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")) or 0
    return SystemStatus(version=s.app_version, queued_jobs=int(queued), data_free_bytes=_free(s.files_dir))
