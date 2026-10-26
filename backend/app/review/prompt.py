"""审核提示词拼接（SPEC 11.2，设计 docs/design/M2.md 第八节）。

system 消息：resources/prompts/system.md 模板，{JSON_SCHEMA} 换成 11.3 的结构说明（JSON_SCHEMA_TEXT）。
user 消息：【审核背景】【审核要求】【主体说明】【本地校验摘要】【待审核文件】依次拼接，空内容的部分省略。
用户填写的补充说明、公司说明同样做 document 标签转义，外发文本已在构建时转义（SPEC 17.5）。
修改 JSON_SCHEMA_TEXT 等于修改提示词，须同时更新 system.md 中的版本号。
分段审核（M5，设计 docs/design/M5.md 第二节）时每段的消息只把 <document> 换成本段的行，并在【审核背景】末尾加分段说明；
不分段时消息与 M2 逐字相同。
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.db.models import CompanyRequirement, Task
from app.redaction.quotes import QuoteTable
from app.review.chunking import Chunk
from app.redaction.outbound import escape_document_tags
from app.review.requirements import Requirement, _builtin_data, load_system_template

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
    '"basis": "依据，如通行商业惯例；不得编造法条编号", "law_refs": ["参考法条编号，如 LAW3"], "source": "范本对照 或省略"}], '
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
    boundary_locs: set[str] = field(default_factory=set)  # 分段的首选切分点（M5）
    quote_tables: list[QuoteTable] = field(default_factory=list)  # 外发文本中被汇总的报价明细表（M5）
    law_refs: list[dict] = field(default_factory=list)
    law_stats: dict = field(default_factory=lambda: dict(enabled=False,sent=0,dropped=0,chars=0))
    template_candidates: list[dict] = field(default_factory=list)
    template_section: str = ''
    template_stats: dict = field(default_factory=lambda: dict(total=0,sent=0,blocked=False))


def note_requirement(user_note: str | None) -> Requirement | None:
    """本次补充说明作为一条审核要求 [NOTE] 发送。"""
    if user_note and user_note.strip():
        return Requirement(id=NOTE_REQ_ID, source="note", dimension=None, text=user_note.strip())
    return None


def system_prompt() -> tuple[str, str]:
    """(prompt_version, system 消息正文)。"""
    version, template = load_system_template()
    return version, template.replace("{JSON_SCHEMA}", JSON_SCHEMA_TEXT)


CHAT_ANSWER_RULE = "8. 用简体中文自然语言回答，不输出 JSON。"


def chat_system_prompt() -> tuple[str, str]:
    """追问用的 system 消息（SPEC 13）：模板第 1–7 条原样，第 8 条（严重程度）、第 9 条（JSON 输出）和 JSON 结构行换成自然语言回答。"""
    version, template = load_system_template()
    lines = [line for line in template.strip().splitlines()
             if not line.startswith(("8.", "9.", "JSON 结构"))]
    while lines and not lines[-1].strip():
        lines.pop()
    return version, "\n".join(lines + [CHAT_ANSWER_RULE])


def _requirement_line(req: Requirement) -> str:
    prefix = {"company": "（公司补充）", "note": "（本次补充说明）"}.get(req.source, "")
    return f"[{req.id}] {prefix}{escape_document_tags(req.text)}"


def _split_outbound(outbound_text: str) -> tuple[str, str]:
    """外发文本的第一行是【主体说明】，其余是正文各行。"""
    head, _, rest = outbound_text.partition("\n")
    if head.startswith("【主体说明】"):
        return head, rest
    return "", outbound_text


def chunk_note(chunk: Chunk) -> str:
    return (f"分段：第 {chunk.index}/{chunk.total} 段（文件较长，分段审核，其余段落另行审核；只对本段内容给出问题，"
            "缺少某项内容的问题只在本段属于该内容应在的位置时提出）")


def split_outbound(outbound_text: str) -> tuple[str, str]:
    """(【主体说明】行, 正文各行)。"""
    return _split_outbound(outbound_text)


def build_user_message(ctx: ReviewContext, chunk: Chunk | None = None) -> str:
    background = [f"立场：{STANCE_LABELS.get(ctx.stance, ctx.stance)}",
                  f"来源：{SOURCE_LABELS.get(ctx.source, ctx.source)}",
                  f"主文件类型：{DOC_TYPE_LABELS.get(ctx.main_doc_type, ctx.main_doc_type)}"]
    if ctx.attachment_types:
        background.append("附件：" + "、".join(DOC_TYPE_LABELS.get(t, t) for t in ctx.attachment_types))
    if ctx.company_profile and ctx.company_profile.strip():
        background.append("公司说明：" + escape_document_tags(ctx.company_profile.strip()))
    parties, body = _split_outbound(ctx.outbound_text)
    if chunk is not None and chunk.total > 1:
        background.append(chunk_note(chunk))
        body = "\n".join(chunk.lines)
    parts = ["【审核背景】" + "；".join(background)]
    if ctx.requirements:
        parts.append("【审核要求】\n" + "\n".join(_requirement_line(r) for r in ctx.requirements))
    if parties:
        parts.append(parties)
    if ctx.local_summary and ctx.local_summary.strip():
        parts.append("【本地校验摘要】" + escape_document_tags(ctx.local_summary.strip()))
    parts.append(f"【待审核文件】\n<document>\n{body}\n</document>")
    if ctx.law_refs:
        from app.laws.retrieve import section
        parts.append(section(ctx.law_refs))
    if ctx.template_section:
        parts.append(ctx.template_section)
    return "\n".join(parts)


def build_messages(ctx: ReviewContext, chunk: Chunk | None = None) -> list[dict]:
    version, system = system_prompt()
    ctx.prompt_version = version
    return [{"role": "system", "content": system}, {"role": "user", "content": build_user_message(ctx, chunk)}]


def fixed_prompt_text(db: Session) -> str:
    """每次发送都会出现在提示词中的固定文字（设计 docs/design/M5.md 第六节，Q-77 ③）。只读，不调用模型。

    system 消息全文 + 全部内置要求文字（不按类型筛选）+ 启用的公司补充要求文字 + 公司说明 + 【审核背景】等固定用词。
    手动补充和疑似遗漏“替换”的文字若出现在其中，设为敏感信息后出口复检必然拦截，须在写入前拒绝。
    """
    _, system = system_prompt()
    parts = [system, *(item["text"] for item in _builtin_data()["requirements"])]
    parts += db.scalars(select(CompanyRequirement.text).where(CompanyRequirement.enabled.is_(True)))
    parts.append(str(get_setting(db, "review.company_profile") or ""))
    parts += [*STANCE_LABELS.values(), *SOURCE_LABELS.values(), *DOC_TYPE_LABELS.values()]
    parts += ["【审核背景】立场：；来源：；主文件类型：；附件：；公司说明：", "【审核要求】（公司补充）（本次补充说明）",
              "【主体说明】【本地校验摘要】【待审核文件】【参考法条】【公司范本对照】<document></document>",
              chunk_note(Chunk(index=1, total=2, lines=[], first_loc="", last_loc="", locs=frozenset()))]
    return "\n".join(parts)
