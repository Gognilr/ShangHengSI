"""审核确认页本地数量统计；不记录文字，仅从 run_create 审计汇总。"""
from datetime import timedelta
from statistics import median

from sqlalchemy import func, select

from app.db.base import utcnow
from app.db.models import AuditLog, RedactionItem, RedactionSuspect, TaskFile, RequirementResult, ReviewRun


def enter(task) -> None:
    task.status = 'awaiting_confirm'
    if task.kind == 'review' and task.awaiting_since is None:
        task.awaiting_since = utcnow()


def operation(task) -> None:
    if task.kind == 'review' and task.status == 'awaiting_confirm':
        task.confirm_ops = (task.confirm_ops or 0) + 1


def finish(db, task) -> dict:
    from app.redaction import engine, parties as P
    texts = engine._texts_with_note(engine._body_rows(db, task.id), task)
    rows = list(db.scalars(select(RedactionSuspect).where(RedactionSuspect.task_id == task.id)))
    views = engine.suspect_views(db, task)
    groups = {}
    for row in rows:
        word = texts.get(row.loc, '')[row.char_start:row.char_end]
        groups.setdefault(P.normalize(word), set()).add(row.decision)
    items = list(db.scalars(select(RedactionItem).where(RedactionItem.task_id == task.id, RedactionItem.detected_by == 'user')))
    user_words = {P.normalize(texts.get(i.loc, '')[i.char_start:i.char_end]) for i in items}
    from_suspects = {P.normalize(texts.get(i.loc, '')[i.char_start:i.char_end]) for i in items
                     if any(r.decision == 'replace' and r.loc == i.loc and i.char_start <= r.char_start
                            and i.char_end >= r.char_end for r in rows)}
    result = dict(ops=task.confirm_ops or 0,
                  seconds=max(0, int((utcnow() - task.awaiting_since).total_seconds())) if task.awaiting_since else 0,
                  uncertain=len(groups), kept_user=sum(v['kept_by'] == 'user' for v in views),
                  kept_memory=sum(v['kept_by'] == 'memory' for v in views),
                  replaced_user=sum('replace' in decisions for decisions in groups.values()), manual=len(user_words - from_suspects),
                  files=db.scalar(select(func.count()).select_from(TaskFile).where(TaskFile.task_id == task.id)))
    # V31Q-29：向量保留与术语记忆分开，只有实际命中时才增加内部审计字段。
    vector_count = sum(v['kept_by'] == 'vector' for v in views)
    if vector_count:
        result['kept_vector'] = vector_count
    task.confirm_ops, task.awaiting_since = 0, None
    return result


def aggregate(db, days: int) -> dict:
    from app.review.validate import normalize_unable_reason

    # 按轮次创建时间计全部逐条结论；旧 unable 的空原因归 other，不复制要求文字。
    counts = list(db.execute(select(RequirementResult.verdict, RequirementResult.unable_reason,
                                   RequirementResult.req_id, func.count())
                            .join(ReviewRun, ReviewRun.id == RequirementResult.run_id)
                            .where(ReviewRun.queued_at >= utcnow() - timedelta(days=days))
                            .group_by(RequirementResult.verdict, RequirementResult.unable_reason, RequirementResult.req_id)))
    reasons = dict.fromkeys(('redacted', 'absent', 'other'), 0)
    redacted = {}
    total = sum(count for _, _, _, count in counts)
    for verdict, reason, req_id, count in counts:
        if verdict == 'unable':
            reason = normalize_unable_reason(verdict, reason)
            reasons[reason] += count
            if reason == 'redacted':
                redacted[req_id] = redacted.get(req_id, 0) + count
    top = sorted(redacted.items(), key=lambda row: (-row[1], row[0]))[:5]
    rows = db.scalars(select(AuditLog.detail).where(AuditLog.action == 'run_create', AuditLog.created_at >= utcnow() - timedelta(days=days)))
    data = [row['confirm'] for row in rows if isinstance(row, dict) and isinstance(row.get('confirm'), dict)]
    n = len(data)
    uncertain = sum(r['uncertain'] for r in data)
    return dict(unable_ratio=sum(reasons.values()) / total if total else 0, unable_by_reason=reasons,
                redacted_top_requirements=[dict(req_id=req_id, count=count) for req_id, count in top],
                tasks=n, zero_ops_ratio=sum(r['ops'] == 0 for r in data) / n if n else 0,
                avg_ops=sum(r['ops'] for r in data) / n if n else 0,
                median_seconds=median(r['seconds'] for r in data) if n else 0,
                avg_uncertain=uncertain / n if n else 0,
                kept_ratio=sum(r['kept_user'] + r['kept_memory'] + r.get('kept_vector', 0) for r in data) / uncertain if uncertain else 0,
                memory_ratio=sum(r['kept_memory'] for r in data) / uncertain if uncertain else 0)
