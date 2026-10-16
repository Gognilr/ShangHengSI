"""审核提示词拼接（SPEC 11.2，设计 docs/design/M2.md 第八节）。

system 消息：resources/prompts/system.md 模板，{JSON_SCHEMA} 换成 11.3 的结构说明（JSON_SCHEMA_TEXT）。
user 消息：【审核背景】【审核要求】【主体说明】【本地校验摘要】【待审核文件】依次拼接，空内容的部分省略。
用户填写的补充说明、公司说明同样做 document 标签转义，外发文本已在构建时转义（SPEC 17.5）。
修改 JSON_SCHEMA_TEXT 等于修改提示词，须同时更新 system.md 中的版本号。
"""
from dataclasses import dataclass, field

from app.db.models import Task
from app.redaction.outbound import escape_document_tags
from app.review.requirements import Requirement, load_system_template

STANCE_LABELS = {"sales": "销售", "purchase": "采购", "other": "其他"}
SOURCE_LABELS = {"outgoing": "我方发出", "incoming": "对方发来"}
DOC_TYPE_LABELS = {"contract": "合同", "quotation": "报价单", "order": "订单/供货单", "inquiry": "询价函", "letter": "函件"}

JSON_SCHEMA_TEXT = (
    '{"doc_summary": "一句话概括文件内容（使用代号）", '
    '"requirement_results": [{"req_id": "审核要求编号，如 C-06", "verdict": "issue / ok / na / unable 之一", '
    '"note": "简要说明"}], '
    '"findings": [{"title": "问题标题", "category": "表述 / 逻辑 / 法律 / 合规与制裁 / 商务 之一", '
    '"severity": "高 / 中 / 低 之一", "req_ids": ["相关的审核要求编号"], "locs": ["位置编号，如 F1-P23"], '
    '"quote": "从该位置原文中逐字摘录，不超过 100 字", "missing": false, "problem": "问题说明", '
    '"impact": "对我方的影响", "suggestion": "修改建议", "suggested_text": "可直接替换的条款措辞，没有则为 null", '
    '"basis": "依据，如通行商业惯例；不得编造法条编号"}], '
    '"sanctions": {"level": "无明显风险 / 需注意 / 高风险 之一", "reasons": ["原因"]}}'
)

NOTE_REQ_ID = "NOTE"


@dataclass
class ReviewContext:
    task: Task
    stance: str
    source: str
    user_note: str | None
    main_doc_type: str
    attachment_types: list[str]
    requirements: list[Requirement]  # 发送的全部审核要求（内置、公司补充、本次补充说明），顺序即发送顺序
    company_profile: str
    outbound_text: str
    local_summary: str = ""  # M2 中本地校验摘要为空
    prompt_version: str = field(default="")


def note_requirement(user_note: str | None) -> Requirement | None:
    """本次补充说明作为一条审核要求 [NOTE] 发送。"""
    if user_note and user_note.strip():
        return Requirement(id=NOTE_REQ_ID, source="note", dimension=None, text=user_note.strip())
    return None


def system_prompt() -> tuple[str, str]:
    """(prompt_version, system 消息正文)。"""
    version, template = load_system_template()
    return version, template.replace("{JSON_SCHEMA}", JSON_SCHEMA_TEXT)


def _requirement_line(req: Requirement) -> str:
    prefix = {"company": "（公司补充）", "note": "（本次补充说明）"}.get(req.source, "")
    return f"[{req.id}] {prefix}{escape_document_tags(req.text)}"


def _split_outbound(outbound_text: str) -> tuple[str, str]:
    """外发文本的第一行是【主体说明】，其余是正文各行。"""
    head, _, rest = outbound_text.partition("\n")
    if head.startswith("【主体说明】"):
        return head, rest
    return "", outbound_text


def build_user_message(ctx: ReviewContext) -> str:
    background = [f"立场：{STANCE_LABELS.get(ctx.stance, ctx.stance)}",
                  f"来源：{SOURCE_LABELS.get(ctx.source, ctx.source)}",
                  f"主文件类型：{DOC_TYPE_LABELS.get(ctx.main_doc_type, ctx.main_doc_type)}"]
    if ctx.attachment_types:
        background.append("附件：" + "、".join(DOC_TYPE_LABELS.get(t, t) for t in ctx.attachment_types))
    if ctx.company_profile and ctx.company_profile.strip():
        background.append("公司说明：" + escape_document_tags(ctx.company_profile.strip()))
    parties, body = _split_outbound(ctx.outbound_text)
    parts = ["【审核背景】" + "；".join(background)]
    if ctx.requirements:
        parts.append("【审核要求】\n" + "\n".join(_requirement_line(r) for r in ctx.requirements))
    if parties:
        parts.append(parties)
    if ctx.local_summary and ctx.local_summary.strip():
        parts.append("【本地校验摘要】" + escape_document_tags(ctx.local_summary.strip()))
    parts.append(f"【待审核文件】\n<document>\n{body}\n</document>")
    return "\n".join(parts)


def build_messages(ctx: ReviewContext) -> list[dict]:
    version, system = system_prompt()
    ctx.prompt_version = version
    return [{"role": "system", "content": system}, {"role": "user", "content": build_user_message(ctx)}]
