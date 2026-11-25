"""专职人员沉淀公司可复用的脱敏审核内容。"""
import hashlib
import json
import re
import uuid

from sqlalchemy import String, cast, exists, select, update
from sqlalchemy.dialects.postgresql import insert

from app.audit import service as audit
from app.chat.service import _redact, _terms
from app.db import jobs
from app.db.base import utcnow
from app.db.models import BidBidder, BidCategory, ContentDraft, Job
from app.parsing.model import strip_invisible
from app.redaction.engine import _load_full_map
from app.redaction.placeholders import NEUTRAL_CATEGORIES
from app.redaction.recheck import recheck
from app.redaction.restore import check, normalize_bid_codes
from app.security.crypto import decrypt_text, encrypt_text
from app.security.errors import AppError, NotFoundError

_BARE_CODE = re.compile(
    r"(?<![A-Za-z0-9_\[［])(?:" + "|".join(NEUTRAL_CATEGORIES) + r")_\d+(?![A-Za-z0-9_\]］])"
)


def _generalize(text: str) -> str:
    """任务内代号、位置和实际日期不能带到其他任务。"""
    text = normalize_bid_codes(text)
    checked = check(_BARE_CODE.sub(lambda m: f"[[{m[0]}]]", text), {}, clause=True, dates={}, bid=True)
    text = checked.text
    for mark in reversed(checked.marks):
        if mark.kind == "note":
            replacement = ""
        elif mark.raw.strip("[［【〔]］】〕") in ("我方", "OUR"):
            replacement = "我方"
        elif mark.raw.strip("[［【〔]］】〕").startswith("投标人"):
            replacement = "投标人"
        else:
            replacement = "（待填写）"
        text = text[:mark.start] + replacement + text[mark.end:]
    return text.strip()


def _clean(db, task, text, project_names):
    text = strip_invisible(text).strip()
    mapping = {k: v for k, v in _load_full_map(db, task.id).items() if not k.startswith("@") and "#D" not in k}
    terms = [*_terms(db), *project_names]
    protected = _redact(text, mapping, terms)
    if protected.suspects:
        decisions = [{"start": s["start"], "end": s["end"], "decision": "replace", "category": "CUSTOM"}
                     for s in protected.suspects]
        protected = _redact(text, mapping, terms, decisions)
    value = _generalize(protected.text)
    if not value or not value.replace("（待填写）", "").strip():
        raise AppError(422, "CONTENT_DRAFT_EMPTY", "脱敏后没有可沉淀的条文")
    if recheck(value, real_values=mapping.values(), terms=[name for name, _ in terms]):
        raise AppError(422, "CONTENT_DRAFT_SENSITIVE", "条文仍含疑似敏感信息，请核对后再沉淀")
    return value


def _active(kind):
    # 已启用的旧标书条目仍可复用。旧范本条文已写入其目标范本。
    return ContentDraft.status.in_(("active", "approved")) if kind == "bid" else ContentDraft.status == "active"


def shared_sources(db, kind, sources):
    return set(db.scalars(select(ContentDraft.source_id).where(
        ContentDraft.kind == kind, _active(kind), ContentDraft.source_id.in_(sources))))


def publish(db, task, source_id, text, kind, *, doc_type=None, stance=None, category_id=None, project_names=()):
    if shared_sources(db, kind, [source_id]):
        return {"active": True}
    if not text or not text.strip():
        raise AppError(422, "CONTENT_DRAFT_EMPTY", "这条建议没有可沉淀的条文")
    if kind == "bid":
        from app.bid.folders import choose_category
        if category_id is None:
            raise AppError(422, "CONTENT_DRAFT_CATEGORY", "请先为标书项目选择分类")
        choose_category(db, category_id)
    value = _clean(db, task, text, project_names)
    digest = hashlib.sha256(json.dumps([value, doc_type, stance, category_id], ensure_ascii=False).encode()).hexdigest()
    columns = [ContentDraft.kind, ContentDraft.source_id, ContentDraft.content_hash]
    now = utcnow()
    stmt = insert(ContentDraft).values(
        kind=kind, source_id=source_id, content_hash=digest, text_enc=encrypt_text(value),
        doc_type=doc_type, stance=stance, category_id=category_id, status="active", approved_at=now)
    draft_id = db.scalar(stmt.on_conflict_do_update(
        index_elements=columns, set_={"status": "active", "approved_at": now}, where=~_active(kind)
    ).returning(ContentDraft.id))
    if draft_id is not None:
        if kind == "template":
            draft = db.get(ContentDraft, draft_id)
            if draft.embedding is None and not db.scalar(select(_preparing(str(draft_id)))):
                jobs.enqueue(db, "template_embed", {"content_id": str(draft_id)})
        audit.record(db, "content_share", user_id=task.owner_id, target_type="content_draft",
                     target_id=str(draft_id), detail={"kind": kind})
    return {"active": True}


def bid_names(db, task, project):
    from app.bid.invitees import names
    from app.bid.requirements import load_state
    values = [decrypt_text(value) for value in db.scalars(select(BidBidder.name_enc).where(BidBidder.task_id == task.id))]
    values.extend(names(load_state(project)))
    return [(task.title, "PROJECT"), *((name, "ORG") for name in values if name)]


def _preparing(content_id):
    return exists().where(Job.type == "template_embed", Job.payload["content_id"].astext == content_id,
                          Job.status.in_(("queued", "running")))


def retry_template(db, content_id, user_id):
    row = db.get(ContentDraft, content_id, with_for_update=True, populate_existing=True)
    if row is None or row.kind != "template" or row.status != "active":
        raise NotFoundError("CONTENT_DRAFT_NOT_FOUND", "这条内容已撤下或不存在")
    if row.embedding is not None or db.scalar(select(_preparing(str(content_id)))):
        raise AppError(409, "CONTENT_DRAFT_PREPARING", "这条内容已可复用或正在准备")
    jobs.enqueue(db, "template_embed", {"content_id": str(content_id)})
    audit.record(db, "content_retry", user_id=user_id, target_type="content_draft", target_id=str(content_id))
    return {"reuse_status": "preparing"}


def listing(db, kind):
    query = (select(ContentDraft, BidCategory.name).outerjoin(BidCategory, ContentDraft.category_id == BidCategory.id)
             .where(ContentDraft.kind == kind, _active(kind)).order_by(ContentDraft.approved_at.desc(), ContentDraft.id))
    if kind == "template":
        # 状态与向量在同一数据库快照内读取，避免准备完成时短暂误报失败。
        query = query.add_columns(_preparing(cast(ContentDraft.id, String)))
    items = []
    for row, category, *preparing in db.execute(query):
        item = {"id": row.id, "text": decrypt_text(row.text_enc), "doc_type": row.doc_type, "stance": row.stance,
                "category": category, "shared_at": row.approved_at}
        if kind == "template":
            item["reuse_status"] = "ready" if row.embedding is not None else "preparing" if preparing[0] else "failed"
        items.append(item)
    return items


def withdraw(db, kind, user_id, *, source_id=None, content_id: uuid.UUID | None = None):
    target = ContentDraft.id == content_id if content_id is not None else ContentDraft.source_id == source_id
    ids = db.scalars(update(ContentDraft).where(ContentDraft.kind == kind, _active(kind), target)
                     .values(status="withdrawn").returning(ContentDraft.id)).all()
    if content_id is not None and not ids:
        raise NotFoundError("CONTENT_DRAFT_NOT_FOUND", "这条内容已撤下或不存在")
    for id_ in ids:
        audit.record(db, "content_withdraw", user_id=user_id, target_type="content_draft",
                     target_id=str(id_), detail={"kind": kind})
    return {"active": False}


def template_clauses(db, run):
    if not run.use_shared_clauses:
        return []
    return db.scalars(select(ContentDraft).where(
        ContentDraft.kind == "template", _active("template"), ContentDraft.doc_type == run.main_doc_type,
        ContentDraft.stance == run.stance, ContentDraft.approved_at <= run.queued_at,
        ContentDraft.embedding.is_not(None),
    ).order_by(ContentDraft.approved_at.desc(), ContentDraft.id)).all()


def bid_checks(db, category_id, created_at):
    if category_id is None:
        return []
    rows = db.scalars(select(ContentDraft.text_enc).where(
        ContentDraft.kind == "bid", _active("bid"), ContentDraft.category_id == category_id,
        ContentDraft.approved_at <= created_at,
    ).order_by(ContentDraft.approved_at, ContentDraft.id))
    return list(dict.fromkeys(decrypt_text(value) for value in rows))
