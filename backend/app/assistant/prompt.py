"""助手外发包：只用已确认文件的代号正文，标题与文件名不进入模型请求。"""
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.db.models import AssistantMessage, DocNode, Party, Task, TaskFile
from app.redaction.outbound import escape_document_tags
from app.redaction.placeholders import NEUTRAL
from app.redaction.dictionaries import find_country
from app.redaction.engine import load_redaction_map
from app.security.errors import AppError

VERSION = "assistant-2026.11-1"
_PATH = Path(__file__).resolve().parents[2] / "resources/prompts/assistant.md"
_TYPES = {"contract": "合同", "quotation": "报价单", "order": "订单", "inquiry": "询价单", "letter": "函件"}
_LABELS = {"ORG": "企业", "PER": "人名", "PRJ": "项目", "REF": "编号", "TEL": "电话", "EMAIL": "邮箱",
           "URL": "网址", "ZIP": "邮编", "ACCT": "账号", "SWIFT": "银行代码", "AMT": "金额", "PRICE": "单价",
           "QTY": "数量", "TERM": "敏感词", "IM": "联系方式", "ID": "证件号", "ADDR": "地址"}


def system_prompt() -> str:
    return _PATH.read_text(encoding="utf-8").strip()


def selected_files(db: Session, task: Task, params: dict) -> list[TaskFile]:
    files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.confirmed_at.is_not(None))
                            .order_by(TaskFile.seq)))
    selected = params.get("file_ids")
    if selected is not None:
        try:
            if not isinstance(selected, list) or any(not isinstance(fid, str) for fid in selected):
                raise ValueError()
            ids = {uuid.UUID(fid) for fid in selected}
        except (ValueError, TypeError, AttributeError):
            raise AppError(422, "VALIDATION_ERROR", "所选文件不正确") from None
        if len(ids) != len(selected) or not ids <= {f.id for f in files}:
            raise AppError(422, "VALIDATION_ERROR", "所选文件不正确")
        files = [f for f in files if f.id in ids]
    return files


def file_block(db: Session, task: Task, params: dict) -> str:
    files = selected_files(db, task, params)
    blocks, size = [], 0
    for index, file in enumerate(files, 1):
        nodes = list(db.scalars(select(DocNode).where(DocNode.file_id == file.id, DocNode.part == "body").order_by(DocNode.ord)))
        text = "\n".join(f"⟦{n.loc}⟧{n.redacted_text or ''}" for n in nodes)
        size += len(text)
        blocks.append(f"【文件{file.seq}：{_TYPES.get(file.doc_type, '资料')}，{file.ext}，第{index}/{len(files)}个】"
                      f"<document>{escape_document_tags(text)}</document>")
    limit = int(get_setting(db, "model.chunk_chars") or 60000)
    if size > limit:
        raise AppError(409, "ASSIST_TOO_LONG", f"所选文件合计约 {size} 字，超过单次上限 {limit} 字，请减少使用的文件")
    return "\n".join(blocks)


def build_messages(db: Session, task: Task, user_message: AssistantMessage) -> list[dict]:
    block = file_block(db, task, user_message.params)
    history = list(reversed(list(db.scalars(select(AssistantMessage).where(
        AssistantMessage.task_id == task.id, AssistantMessage.id < user_message.id, AssistantMessage.status == "done")
        .order_by(AssistantMessage.id.desc()).limit(10)))))
    used = set(NEUTRAL.findall("\n".join([block, user_message.content_redacted] + [m.content_redacted for m in history])))
    parties = {p.code: p for p in db.scalars(select(Party).where(Party.task_id == task.id))}
    mapping = load_redaction_map(db, task.id)
    descriptions = []
    for code in sorted(used):
        if code == "[[OUR]]":
            label = "我方"
        else:
            label = _LABELS.get(code[2:-2].rsplit("_", 1)[0], "敏感信息")
            party = parties.get(code)
            if party:
                country = find_country(party.country or "")
                label += "（" + "，".join(filter(None, (party.role_label, country))) + "）"
            elif code.startswith("[[ADDR_"):
                country = find_country(mapping.get(code, ""))
                if country:
                    label += f"（{country}）"
        descriptions.append(f"{code}：{label}")
    return [{"role": "system", "content": system_prompt()},
            {"role": "user", "content": "【代号说明】\n" + "\n".join(descriptions) + "\n" + block}] + [
                {"role": m.role, "content": escape_document_tags(m.content_redacted)} for m in history
            ] + [{"role": "user", "content": escape_document_tags(user_message.content_redacted)}]
