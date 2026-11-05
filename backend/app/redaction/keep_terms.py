"""术语记忆只保留不确定项；规则、词条、签约方与高置信识别始终优先。"""
import uuid

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.base import utcnow
from app.db.models import KeepTerm, RedactionSuspect, RedactionTerm
from app.redaction import parties as P, suspects as S
from app.security import crypto


def eligible(text: str, reason: str) -> bool:
    """写入记忆与向量候选共用的企业后缀、人名结构判断。"""
    return (2 <= len(text.strip()) <= 50 and S.default_category(text, reason) in ('NAME', 'TERM')
            and not P._EN_SUFFIX_RE.search(text)
            and not any(suffix in text for suffix in (*P._BROAD_SUFFIXES, '股份有限公司', '有限责任公司', '有限公司'))
            and not S._is_person_name(text.strip()) and reason not in ('person_name', 'person_like', 'checksum_fail'))


def blocked(db) -> set[str]:
    return {P.normalize(crypto.decrypt_text(blob)) for blob in db.scalars(
        select(RedactionTerm.term_enc).where(RedactionTerm.enabled.is_(True)))}


def effective(db, user_id: uuid.UUID) -> dict[str, str]:
    shared = select(KeepTerm.term_hash).group_by(KeepTerm.term_hash).having(func.count(func.distinct(KeepTerm.user_id)) >= 2)
    rows = db.scalars(select(KeepTerm).where(or_(KeepTerm.user_id == user_id, KeepTerm.user_id.is_(None),
                                               KeepTerm.term_hash.in_(shared))))
    excluded = blocked(db)
    words = (crypto.decrypt_text(r.term_enc) for r in rows)
    return {P.normalize(t): t for t in words if P.normalize(t) not in excluded}


def remember(db, task, user_id: uuid.UUID) -> None:
    from app.redaction import engine
    texts = engine.task_texts(db, task)
    excluded = blocked(db)
    for row in db.scalars(select(RedactionSuspect).where(RedactionSuspect.task_id == task.id,
                                                      RedactionSuspect.decision == 'ignore')):
        word = texts.get(row.loc, '')[row.char_start:row.char_end].strip()
        if eligible(word, row.reason) and P.normalize(word) not in excluded:
            db.execute(insert(KeepTerm).values(id=uuid.uuid4(), term_hash=engine.term_hash(word),
                       term_enc=crypto.encrypt_text(word), user_id=user_id, created_at=utcnow())
                       .on_conflict_do_nothing())
    db.flush()


def forget_personal(db, user_id, word: str) -> None:
    if user_id is None:
        return
    from app.redaction.engine import term_hash
    db.execute(delete(KeepTerm).where(KeepTerm.user_id == user_id, KeepTerm.term_hash == term_hash(word)))


def count_hits(db, user_id, hashes: set[str]) -> None:
    for digest in hashes:
        rows = list(db.scalars(select(KeepTerm).where(KeepTerm.term_hash == digest).order_by(KeepTerm.id)))
        # V31Q-17：每个词每次解析只计一次；本人行优先，其次认可行，再取一条共享来源行。
        row = min(rows, key=lambda r: (r.user_id != user_id, r.user_id is not None)) if rows else None
        if row is not None:
            db.execute(update(KeepTerm).where(KeepTerm.id == row.id)
                       .values(hit_count=KeepTerm.hit_count + 1, last_hit_at=utcnow()))
