"""出口复检拦截清单（V31-U5F-02）：把拦截原因落到具体段落，供本人在确认页定位、处理。

逐段对脱敏后的文本做与出口复检相同的检查，命中位置按“原文与脱敏文本逐段对齐”换回原文位置，
再给出前后各 20 字的预览。预览只经 HTTPS 返回给任务所有者本人的浏览器，不写日志、不进 llm_calls。
"""
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RedactionExclusion, RedactionItem, Task
from app.llm.gateway import REASON_LABELS
from app.redaction import engine, parties as P, placeholders as PH
from app.redaction.recheck import recheck, recheck_context
from app.security import crypto

PREVIEW = 20
# 只有格式化信息、人名、地址的重扫命中可以由用户确认“可以发送”；对照表真实值、词条、金额不能豁免（recheck.allowed）
KEEPABLE = {"PHONE", "EMAIL", "ACCOUNT", "IDNO", "SWIFT", "URL", "IM", "POSTCODE", "REFNO", "PERSON", "ADDR"}
_USER_CATEGORY = {"PHONE": "IM", "EMAIL": "IM", "URL": "IM", "IM": "IM", "ACCOUNT": "ACCOUNT", "SWIFT": "ACCOUNT",
                  "IDNO": "IDNO", "REFNO": "REFNO", "POSTCODE": "REFNO", "AMOUNT": "AMOUNT", "PERSON": "PERSON",
                  "ADDR": "ADDR", "TERM": "CUSTOM", "ORG": "ORG", "OUR": "ORG", "NAME": "ORG", "PROJECT": "PROJECT",
                  "CUSTOM": "CUSTOM", "PRICE": "AMOUNT"}


@dataclass
class Leak:
    loc: str
    char_start: int  # 原文中的位置，可直接用于手动补充（/redaction/manual）
    char_end: int
    category: str
    label: str
    replace_category: str | None  # 替换为代号时默认的手动补充类别；None 表示不能就地替换（如不可见字符）
    keepable: bool
    before: str
    text: str
    after: str
    section: str | None = None  # 命中在系统生成的附加内容中时：主体说明 / 报价汇总 / 日期说明；loc 为所在行的位置（如有）


_LINE_LOC = re.compile(r"^\[([^\]]+)\] ")


def _section(text: str, pos: int) -> tuple[str, str]:
    """(所在行的 loc, 区块名)。外发文本第一行为主体说明，节点行与报价汇总行以“[loc] ”开头。"""
    start = text.rfind("\n", 0, pos) + 1
    stop = text.find("\n", pos)
    line = text[start:stop if stop >= 0 else len(text)]
    if start == 0:
        return "", "主体说明"
    m = _LINE_LOC.match(line)
    if not m:
        return "", "日期说明"
    return m.group(1), "报价汇总" if line[m.end():].startswith("【报价明细表") else "正文"


_LABEL_CATEGORY = {**{fmt.strip("[]").split("_", 1)[0]: cat for cat, fmt in PH._NUMBERED.items()},
                   **{label: cat for cat, label in PH.CHAT_LABELS.items()}, "地址": "ADDR"}


def _code_category(code: str) -> str:
    if PH.NEUTRAL.fullmatch(code.split("#", 1)[0]):  # 会话的中性代号（含旧会话的 [[OUR]]）
        return PH.NeutralAllocator.category(code)
    label = code.strip("[]").split("_", 1)[0].split("：", 1)[0]
    if code == PH.OUR_CODE or label in PH.ROLE_LABELS:
        return "ORG"
    return _LABEL_CATEGORY.get(label, "CUSTOM")


def find_leaks(db: Session, task: Task) -> tuple[list[Leak], int]:
    """(可定位的命中, 无法落到段落的命中数)。后者来自主体说明、报价汇总等附加内容。"""
    ctx = recheck_context(db, task.id)
    kw = dict(real_values=ctx.real_values, terms=ctx.terms, placeholders=ctx.placeholders, kept=ctx.kept)
    rows = engine._body_rows(db, task.id)
    texts = engine._texts_with_note(rows, task)
    redacted = {r.loc: r.redacted_text or "" for r in rows}
    if task.user_note_redacted and "NOTE" in texts:
        redacted["NOTE"] = task.user_note_redacted
    items: dict[str, list[RedactionItem]] = defaultdict(list)
    for item in db.scalars(select(RedactionItem).where(RedactionItem.task_id == task.id)):
        items[item.loc].append(item)
    # 对照表命中的默认类别：与哪个已替换的原文相同，就用它的类别；本任务没有替换项时按代号的标签推断
    known = {P.normalize(value): _code_category(code) for code, value in engine._load_full_map(db, task.id).items()}
    known.update({P.normalize(texts.get(i.loc, "")[i.char_start:i.char_end]): i.category
                  for rows_ in items.values() for i in rows_})
    leaks: list[Leak] = []
    for loc, text in redacted.items():
        if not text:
            continue
        reasons = recheck(text, **kw)
        if not reasons:
            continue
        original = texts.get(loc, "")
        segments = engine.align_segments(original, text, items[loc])
        seen = set()
        for reason in reasons:
            span = engine.to_original(segments, reason.position, reason.end if reason.end > 0 else reason.position + 1)
            if span is None or (span, reason.category) in seen:
                continue
            seen.add((span, reason.category))
            start, end = span
            surface = original[start:end]
            if reason.category == "MAP":
                replace = _USER_CATEGORY.get(known.get(P.normalize(surface), ""), "CUSTOM")
            else:
                replace = _USER_CATEGORY.get(reason.category)
            leaks.append(Leak(loc, start, end, reason.category, REASON_LABELS.get(reason.category, "未脱敏的信息"),
                              replace if len(surface.strip()) >= engine.MANUAL_MIN else None,
                              reason.category in KEEPABLE,
                              original[max(0, start - PREVIEW):start], surface, original[end:end + PREVIEW]))
    other = 0
    if not leaks:  # 段落里查不到时，再看整段外发文本（主体说明、报价汇总、跨行的上下文等），逐条列出所在区块
        text = engine.build_outbound_text(db, task)
        seen = set()
        for reason in sorted(recheck(text, **kw), key=lambda r: r.position):
            start = reason.position
            end = reason.end if reason.end > start else start + 1
            if (start, reason.category) in seen:
                continue
            seen.add((start, reason.category))
            loc, section = _section(text, start)
            # 代号原文不是用户的文字，预览中保留；只能确认可以发送，不能就地替换
            leaks.append(Leak(loc or "OUT", start, end, reason.category,
                              REASON_LABELS.get(reason.category, "未脱敏的信息"), None,
                              reason.category in KEEPABLE, text[max(0, start - PREVIEW):start], text[start:end],
                              text[end:end + PREVIEW], section))
    return leaks, other


def keep(db: Session, task: Task, *, loc: str, char_start: int, char_end: int, user_id: uuid.UUID) -> str:
    """确认这一处可以发送：沿用“保留原文”的豁免（V31Q-51），只豁免重扫，不豁免对照表与金额。返回类别。"""
    leak = next((x for x in find_leaks(db, task)[0]
                 if x.loc == loc and x.char_start == char_start and x.char_end == char_end), None)
    if leak is None or not leak.keepable:
        raise engine.RedactionInputError(422, "NOT_KEEPABLE", "这一处不能直接发送，请替换为代号")
    db.add(RedactionExclusion(task_id=task.id, loc=loc, text_enc=crypto.encrypt_text(leak.text),
                              category=leak.category[:10], created_by=user_id))
    db.flush()
    return leak.category
