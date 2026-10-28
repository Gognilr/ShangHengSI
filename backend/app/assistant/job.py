"""助手 worker：分段提交代号正文，停止与失败均保留已生成部分，不自动重发。"""
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import prompt, service
from app.redaction import engine
from app.db.models import AssistantMessage, Task
from app.llm import gateway
from app.security.errors import AppError
from app.worker import job_handler


@job_handler("assist")
def handle_assist(db: Session, payload: dict) -> None:
    tid, mid = uuid.UUID(str(payload["task_id"])), int(payload["message_id"])
    task = db.scalar(select(Task).where(Task.id == tid, Task.kind == "assistant").with_for_update())
    row = db.get(AssistantMessage, mid, with_for_update=True)
    if task is None or row is None or row.task_id != tid or row.role != "assistant" or row.status not in ("queued", "streaming"):
        db.rollback()
        return
    if row.content_redacted or row.status == "streaming":
        row.status, row.error_code = "failed", "ASSIST_INTERRUPTED"
        db.commit()
        return
    content, stored, last = "", 0, time.monotonic()
    compare_tokens = None
    stopped = False
    try:
        service._writable(db, task)
        user_row = service.owned_message(db, task, int(row.extras.get("user_message_id", 0)))
        if user_row.role != "user":
            raise AppError(409, "ASSIST_INTERRUPTED", "原始消息不存在")
        owner_id, title = task.owner_id, task.title
        template, params = user_row.template, user_row.params
        if template == "translate":
            from app.assistant.translate import task_segments
            service._preflight(db, task, template, params)
            parts = task_segments(db, task, params)
            row.extras = {**row.extras, "segments_total": len(parts), "segments_done": 0,
                          "translation": {}, "translation_unaligned": []}
        else:
            parts = [None]
        if template == "compare":
            messages = prompt.build_messages(db, task, user_row)
            seqs = [file.seq for file in prompt.selected_files(db, task, params)]
            row.status = "streaming"
            db.commit()
            outcome = gateway.call_model(db, purpose="assist", messages=messages, user_id=owner_id,
                                         task_id=tid, task_title=title, json_mode=True, retry=False)
            compare_tokens = (outcome.tokens_in, outcome.tokens_out)
            try:
                from app.assistant.compare import normalize
                comparison = normalize(outcome.content, engine.load_redaction_map(db, tid), seqs)
            except (ValueError, TypeError, KeyError):
                raise gateway.GatewayError("BAD_RESPONSE") from None
            row = db.scalar(select(AssistantMessage).where(AssistantMessage.id == mid).with_for_update()
                            .execution_options(populate_existing=True))
            if row is not None:
                row.content_redacted = comparison["notes"]
                row.extras = {**row.extras, "compare": comparison}
                if row.status == "streaming":
                    row.status = "stopped" if row.extras.get("stop_requested") else "done"
                row.tokens_in, row.tokens_out = outcome.tokens_in, outcome.tokens_out
                db.commit()
            return
        row.status = "streaming"
        db.commit()

        def write(delta):
            nonlocal content, stored, last, stopped
            content += delta
            current = db.scalar(select(AssistantMessage).where(AssistantMessage.id == mid).with_for_update()
                                .execution_options(populate_existing=True))
            if current is None:
                stopped = True
                db.rollback()
                return False
            stopped = bool(current.extras.get("stop_requested")) or current.status != "streaming"
            now = time.monotonic()
            if stopped or len(content) - stored >= 200 or now - last >= 0.5:
                current.content_redacted = content
                db.commit()
                stored, last = len(content), now
            else:
                db.rollback()
            return not stopped

        for number, part in enumerate(parts, 1):
            row = db.scalar(select(AssistantMessage).where(AssistantMessage.id == mid).with_for_update()
                            .execution_options(populate_existing=True))
            if row is None:
                db.rollback()
                return
            if row.status != "streaming" or row.extras.get("stop_requested"):
                stopped = True
                db.rollback()
                break
            # 本段消息在开始发送前完成组装，之后不持有消息行锁。
            messages = prompt.build_messages(db, task, user_row, nodes=part)
            db.commit()
            if number > 1:
                content += "\n"
            outcome = gateway.stream_model(db, purpose="assist", messages=messages, user_id=owner_id,
                                           task_id=tid, task_title=title, on_delta=write)
            row = db.scalar(select(AssistantMessage).where(AssistantMessage.id == mid).with_for_update()
                            .execution_options(populate_existing=True))
            if row is None:
                db.rollback()
                return
            row.content_redacted = content
            row.tokens_in += outcome.tokens_in
            row.tokens_out += outcome.tokens_out
            stopped = stopped or bool(row.extras.get("stop_requested")) or row.status != "streaming"
            if template == "translate" and not stopped:
                from app.assistant.translate import align
                aligned, unaligned = align(outcome.content, part)
                row.extras = {**row.extras, "segments_done": number,
                              "translation": {**row.extras["translation"], **aligned},
                              "translation_unaligned": row.extras["translation_unaligned"] + ([number] if unaligned else [])}
            db.commit()
            if stopped:
                break
        row = db.scalar(select(AssistantMessage).where(AssistantMessage.id == mid).with_for_update()
                        .execution_options(populate_existing=True))
        if row is not None:
            row.content_redacted = content
            if row.status == "streaming":
                row.status = "stopped" if stopped or row.extras.get("stop_requested") else "done"
            db.commit()
    except (gateway.GatewayError, AppError) as exc:
        db.rollback()
        row = db.get(AssistantMessage, mid, with_for_update=True)
        if row is not None:
            row.content_redacted = content or row.content_redacted
            row.status, row.error_code = "failed", exc.code
            if compare_tokens is not None:
                row.tokens_in, row.tokens_out = compare_tokens
            db.commit()
    except Exception:
        db.rollback()
        row = db.get(AssistantMessage, mid, with_for_update=True)
        if row is not None:
            row.content_redacted = content or row.content_redacted
            row.status, row.error_code = "failed", "ASSIST_INTERRUPTED"
            db.commit()
        raise
