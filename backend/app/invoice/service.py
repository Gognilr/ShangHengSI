"""批次的开始、查看、导出与清除（设计第五、六节）。

识别任务用 jobs 一行：类型 invoice，payload 只有批次随机 id 与 owner_id，progress 只有计数。
每人同时只有一批，开始新的一批即清除上一批；清除时目录与 jobs 行一并删掉，不留记录。
"""
import logging
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.audit import service as audit
from app.db import jobs
from app.db.base import utcnow
from app.db.models import Job, User
from app.invoice import excel, intake, store
from app.invoice.model import Invoice, Unrecognized
from app.security.errors import AppError, NotFoundError

log = logging.getLogger(__name__)

DONE_TTL = timedelta(minutes=30)  # 识别完成后 30 分钟
UPLOAD_TTL = timedelta(hours=2)  # 上传后 2 小时
ORPHAN_SECONDS = 600
MSG_FAILED = "识别时发生内部错误，请重新上传；仍然失败请联系管理员"
SH = ZoneInfo("Asia/Shanghai")


def _own_jobs(db: DbSession, user: User):
    return db.scalars(select(Job).where(Job.type == "invoice", Job.payload["owner_id"].astext == str(user.id))).all()


def _clear_job(db: DbSession, job: Job) -> None:
    bid = str((job.payload or {}).get("batch_id", ""))
    if re.fullmatch(r"[0-9a-f]{32}", bid):
        store.clear(bid)
    db.execute(delete(Job).where(Job.id == job.id))


def start_batch(db: DbSession, user: User, raw: list[tuple[str | None, bytes]]) -> str:
    items, skipped = intake.collect(raw)
    for job in _own_jobs(db, user):
        _clear_job(db, job)
    db.commit()
    bid = uuid.uuid4().hex
    store.save(bid, [(i.name, i.ext, i.data) for i in items], [{"name": s.name, "reason": s.reason} for s in skipped])
    try:
        job = jobs.enqueue(db, "invoice", {"batch_id": bid, "owner_id": str(user.id)})
        job.progress = {"done": 0, "total": len(items)}
        db.commit()
    except Exception:
        db.rollback()
        store.clear(bid)
        raise
    log.info("发票批次 %s 已收：文件 %s 个", bid, len(items) + len(skipped))
    return bid


def owned_batch(db: DbSession, user: User, bid: str) -> Job:
    """本人的批次，否则 404（不区分“不存在”与“不是你的”，也不区分“已清除”）。"""
    if not re.fullmatch(r"[0-9a-f]{32}", bid or ""):
        raise NotFoundError("INVOICE_BATCH_NOT_FOUND", "这一批不存在或已清除")
    job = db.scalar(select(Job).where(Job.type == "invoice", Job.payload["batch_id"].astext == bid))
    if job is None or (job.payload or {}).get("owner_id") != str(user.id) or not store.batch_dir(bid).is_dir():
        raise NotFoundError("INVOICE_BATCH_NOT_FOUND", "这一批不存在或已清除")
    return job


def _expires(job: Job):
    out = job.created_at + UPLOAD_TTL
    if job.finished_at is not None:
        out = min(out, job.finished_at + DONE_TTL)
    return out


def _result(job: Job) -> tuple[list[Invoice], list[Unrecognized]] | None:
    data = store.load_result(job.payload["batch_id"]) if job.status == "done" else None
    if data is None:
        return None
    return ([Invoice.from_json(r) for r in data["invoices"]],
            [Unrecognized(u["name"], u["reason"]) for u in data["unrecognized"]])


def _money(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def view(job: Job) -> dict:
    status = job.status
    out = {"id": job.payload["batch_id"], "status": status, "progress": job.progress or {"done": 0, "total": 0},
           "expires_at": _expires(job).isoformat()}
    if status != "done":
        if status == "failed":
            out["error"] = MSG_FAILED
        return out
    got = _result(job)
    if got is None:
        return {**out, "status": "failed", "error": MSG_FAILED}
    rows, unrecognized = got
    out["rows"] = [{"seq": i, "kind": r.kind, "code": r.code, "number": r.number, "date": r.date,
                    "buyer_name": r.buyer_name, "buyer_tax": r.buyer_tax, "seller_name": r.seller_name,
                    "seller_tax": r.seller_tax, "item": r.item_text, "amount": _money(r.amount), "tax": _money(r.tax),
                    "total": _money(r.total), "rates": "、".join(r.rates), "remark": r.remark, "files": r.files,
                    "source": r.source, "hints": r.hints} for i, r in enumerate(rows, start=1)]
    out["unrecognized"] = [{"name": u.name, "reason": u.reason} for u in unrecognized]
    out["summary"] = _summary(rows)
    return out


def _summary(rows: list[Invoice]) -> dict:
    def total(key):
        return str(sum((getattr(r, key) for r in rows if getattr(r, key) is not None), Decimal("0.00")))
    return {"count": len(rows), "amount": total("amount"), "tax": total("tax"), "total": total("total"),
            "hinted": sum(1 for r in rows if r.hints)}


def export(db: DbSession, user: User, job: Job, ip: str | None) -> tuple[bytes, str]:
    got = _result(job)
    if got is None:
        raise AppError(409, "INVOICE_NOT_READY", "这一批还没有识别完")
    rows, unrecognized = got
    data = excel.build(rows, unrecognized)
    audit.record(db, "invoice_export", user_id=user.id, detail={"count": len(rows)}, ip=ip)
    db.commit()
    name = f"发票清单_{utcnow().astimezone(SH).date().isoformat()}_{len(rows)}张.xlsx"
    return data, name


def clear_batch(db: DbSession, job: Job) -> None:
    _clear_job(db, job)
    db.commit()


def sweep(db: DbSession) -> None:
    """定时清除（每分钟）：完成后 30 分钟、上传后 2 小时；内存盘已清空（服务重启）的行；没有行的目录。"""
    now = utcnow()
    keep = set()
    n = 0
    for job in db.scalars(select(Job).where(Job.type == "invoice")).all():
        bid = str((job.payload or {}).get("batch_id", ""))
        expired = now >= _expires(job)
        missing = not store.batch_dir(bid).is_dir() and now - job.created_at > timedelta(seconds=ORPHAN_SECONDS)
        if expired or missing:
            _clear_job(db, job)
            n += 1
        else:
            keep.add(bid)
    db.commit()
    for bid, age in store.list_dirs():
        if bid not in keep and age > ORPHAN_SECONDS:
            store.clear(bid)
    if n:
        log.info("已清除发票批次 %s 个", n)
