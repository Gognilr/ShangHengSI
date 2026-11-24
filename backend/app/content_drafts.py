"""把审核建议和标书要求脱敏后交给相应的内容维护人。"""
import hashlib
import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.audit import service as audit
from app.chat.service import _redact, _terms
from app.db.base import utcnow
from app.db.models import BidBidder, BidCategory, ContentDraft
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
        raise AppError(422, "CONTENT_DRAFT_EMPTY", "脱敏后没有可提交的条文")
    if recheck(value, real_values=mapping.values(), terms=[name for name, _ in terms]):
        raise AppError(422, "CONTENT_DRAFT_SENSITIVE", "条文仍含疑似敏感信息，请核对后再提交")
    return value


def submit(db, task, source_id, text, kind, *, doc_type=None, stance=None, category_id=None, project_names=()):
    if not text or not text.strip():
        raise AppError(422, "CONTENT_DRAFT_EMPTY", "这条建议没有可提交的条文")
    if kind == "bid":
        from app.bid.folders import choose_category
        if category_id is None:
            raise AppError(422, "CONTENT_DRAFT_CATEGORY", "请先为标书项目选择分类")
        choose_category(db, category_id)
    value = _clean(db, task, text, project_names)
    digest = hashlib.sha256(json.dumps([value, doc_type, stance, category_id], ensure_ascii=False).encode()).hexdigest()
    columns = [ContentDraft.kind, ContentDraft.source_id, ContentDraft.content_hash]
    stmt = insert(ContentDraft).values(kind=kind, source_id=source_id, content_hash=digest,
                                       text_enc=encrypt_text(value), doc_type=doc_type, stance=stance,
                                       category_id=category_id)
    draft_id = db.scalar(stmt.on_conflict_do_nothing(index_elements=columns).returning(ContentDraft.id))
    if draft_id is None:
        status = db.scalar(select(ContentDraft.status).where(
            ContentDraft.kind == kind, ContentDraft.source_id == source_id, ContentDraft.content_hash == digest))
        return {"status": status}
    audit.record(db, "content_draft_submit", user_id=task.owner_id, target_type="content_draft",
                 target_id=str(draft_id), detail={"kind": kind})
    return {"status": "pending"}


def bid_names(db, task, project):
    from app.bid.invitees import names
    from app.bid.requirements import load_state
    values = [decrypt_text(value) for value in db.scalars(select(BidBidder.name_enc).where(BidBidder.task_id == task.id))]
    values.extend(names(load_state(project)))
    return [(task.title, "PROJECT"), *((name, "ORG") for name in values if name)]


def pending(db, kind):
    rows = db.execute(
        select(ContentDraft, BidCategory.name).outerjoin(BidCategory, ContentDraft.category_id == BidCategory.id)
        .where(ContentDraft.kind == kind, ContentDraft.status == "pending")
        .order_by(ContentDraft.created_at, ContentDraft.id)
    )
    return [{"id": row.id, "text": decrypt_text(row.text_enc), "doc_type": row.doc_type, "stance": row.stance,
             "category": category, "created_at": row.created_at} for row, category in rows]


def decide(db, draft_id: uuid.UUID, kind, admin_id, *, approve: bool, template_id=None):
    draft = db.get(ContentDraft, draft_id, with_for_update=True)
    if draft is None or draft.kind != kind:
        raise NotFoundError("CONTENT_DRAFT_NOT_FOUND", "草稿不存在")
    if draft.status != "pending":
        raise AppError(409, "CONTENT_DRAFT_DECIDED", "这条草稿已处理，请刷新列表")
    if approve:
        text = decrypt_text(draft.text_enc)
        if recheck(text, real_values=[], terms=[term for term, _ in _terms(db)]):
            raise AppError(422, "CONTENT_DRAFT_SENSITIVE", "草稿未通过脱敏复检")
        if kind == "template":
            from app.standards.service import append_clause
            append_clause(db, template_id, text, draft.doc_type, draft.stance)
        else:
            from app.bid.folders import choose_category
            if draft.category_id is None:
                raise AppError(422, "CONTENT_DRAFT_CATEGORY", "草稿的标书分类已不存在")
            choose_category(db, draft.category_id)
        draft.approved_at = utcnow()
    draft.status = "approved" if approve else "returned"
    audit.record(db, f"content_draft_{draft.status}", user_id=admin_id, target_type="content_draft",
                 target_id=str(draft.id), detail={"kind": kind})


def bid_checks(db, category_id, created_at):
    if category_id is None:
        return []
    rows = db.scalars(select(ContentDraft.text_enc).where(
        ContentDraft.kind == "bid", ContentDraft.status == "approved", ContentDraft.category_id == category_id,
        ContentDraft.approved_at <= created_at,
    ).order_by(ContentDraft.approved_at, ContentDraft.id))
    return list(dict.fromkeys(decrypt_text(value) for value in rows))
