"""按问题讨论的上下文（V32-B-02）：只含该问题的结论、相关原文（代号文本）与该问题下的对话。

普通对话与各问题的历史互不混入；总量超过模型的单次字符预算时，先丢最早的对话，
问题与相关条款本身放不下则返回 CONTEXT_TOO_LONG，不截断条款。
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AssistantMessage, DocNode, Finding, Task, TaskFile
from app.models_api import select_model
from app.redaction.outbound import escape_document_tags
from app.security.errors import AppError, NotFoundError

NEAR = 2          # 问题位置前后各取的段落数
MAX_LOCS = 5      # 一个问题最多取几处位置
CLAUSE_CAP = 12   # 同一条款号最多补充的段落数
HISTORY_LIMIT = 10

CATEGORY = {"wording": "措辞", "logic": "逻辑", "legal": "法律", "compliance": "合规", "commercial": "商务"}
LABELS = ("【审核问题】", "【问题相关原文】", "标题：", "位置：", "问题：", "影响：", "审核建议：", "建议条文：", "依据：",
          "（缺失型问题，无对应原文）", *CATEGORY.values())
DISCUSS_NOTE = ("本次对话围绕【审核问题】中的一个问题展开：先对照【问题相关原文】回答，不要泛泛展开其他条款；"
                "不要在回复中写分节名或位置编号，请用自然语言说明对应条款；"
                "用户要求改写时给出完整的替换条文，并说明改动理由；替换条文本身放在【修改条文】与【/修改条文】之间，"
                "标记内只写可直接替换原文的条文，不写说明。")


def owned_finding(db: Session, task: Task, finding_id: uuid.UUID) -> Finding:
    """问题必须属于本会话；别的会话、别人的问题一律按不存在处理。"""
    finding = db.get(Finding, finding_id)
    if finding is None or finding.task_id != task.id:
        raise NotFoundError("FINDING_NOT_FOUND", "问题不存在")
    return finding


def _related(db: Session, finding: Finding) -> list[DocNode]:
    """finding.locs 对应节点及前后各 NEAR 段、同一条款号的节点，按文件与顺序去重。"""
    anchors = list(db.scalars(select(DocNode).where(DocNode.task_id == finding.task_id, DocNode.part == "body",
                                                    DocNode.loc.in_([str(x) for x in (finding.locs or [])][:MAX_LOCS]))))
    found: dict[int, DocNode] = {}
    for node in anchors:
        rows = db.scalars(select(DocNode).where(DocNode.file_id == node.file_id, DocNode.part == "body",
                                                DocNode.ord.between(node.ord - NEAR, node.ord + NEAR)))
        found.update({row.id: row for row in rows})
        if node.clause_no:
            rows = db.scalars(select(DocNode).where(DocNode.file_id == node.file_id, DocNode.part == "body",
                                                    DocNode.clause_no == node.clause_no).order_by(DocNode.ord).limit(CLAUSE_CAP))
            found.update({row.id: row for row in rows})
    seqs = {f.id: f.seq for f in db.scalars(select(TaskFile).where(TaskFile.id.in_({n.file_id for n in found.values()})))} if found else {}
    return sorted(found.values(), key=lambda n: (seqs.get(n.file_id, 0), n.ord))


def finding_block(db: Session, task: Task, finding: Finding, *, omit_advice: bool = False) -> str:
    from app.assistant.meeting import render
    nodes = _related(db, finding)
    files = {f.id: f for f in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id))}
    where = "、".join(str(x) for x in (finding.locs or [])[:MAX_LOCS]) or "（缺失型问题，无对应原文）"
    lines = [f"标题：{finding.title}（{CATEGORY.get(finding.category, '')}，{finding.severity}）", f"位置：{where}",
             f"问题：{finding.problem}"]
    for label, value in (("影响：", finding.impact), ("审核建议：", finding.suggestion),
                         ("建议条文：", finding.suggested_text), ("依据：", finding.basis)):
        if value:
            lines.append(label + value)
    text = "【审核问题】\n" + escape_document_tags("\n".join(lines) if not omit_advice else f"位置：{where}（本轮未附问题结论与建议条文）")
    if nodes:
        text += "\n【问题相关原文】<document>" + escape_document_tags(render(db, task, files, nodes, {})) + "</document>"
    return text


def anchored_history(db: Session, task: Task, user_message: AssistantMessage, budget: int) -> list[AssistantMessage]:
    """该问题下最近的已完成消息；放不下时丢最早的。"""
    rows = list(db.scalars(select(AssistantMessage).where(
        AssistantMessage.task_id == task.id, AssistantMessage.finding_id == user_message.finding_id,
        AssistantMessage.id < user_message.id, AssistantMessage.status == "done",
        AssistantMessage.local_result_enc.is_(None)).order_by(AssistantMessage.id.desc()).limit(HISTORY_LIMIT)))
    kept, used = [], len(user_message.content_redacted)
    for row in rows:  # 由新到旧
        used += len(row.content_redacted)
        if used > budget:
            break
        kept.append(row)
    return list(reversed(kept))


def build(db: Session, task: Task, user_message: AssistantMessage, *, model_id: int | None = None) -> tuple[str, list[AssistantMessage]]:
    """返回（问题上下文，该问题下的历史消息）；问题与条款超出预算时 CONTEXT_TOO_LONG。"""
    return build_for(db, task, user_message.finding_id, user_message, model_id=model_id,
                     omit_advice=user_message.params.get('omit_finding_advice', False))


def build_for(db: Session, task: Task, finding_id: uuid.UUID, user_message: AssistantMessage | None, *,
              model_id: int | None = None, omit_advice: bool = False) -> tuple[str, list[AssistantMessage]]:
    finding = owned_finding(db, task, finding_id)
    block = finding_block(db, task, finding, omit_advice=omit_advice)
    limit = select_model(db, model_id, "assist").chunk_chars
    question = len(user_message.content_redacted) if user_message is not None else 0
    if len(block) + question > limit:
        raise AppError(409, "CONTEXT_TOO_LONG", f"该问题涉及的条款过长（约 {len(block)} 字，单次上限 {limit} 字），"
                                                "请换用上下文更长的模型")
    history = anchored_history(db, task, user_message, limit - len(block)) if user_message is not None else []
    return block, history
