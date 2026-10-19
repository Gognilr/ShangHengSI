"""外发文本构建（SPEC 9.7、17.5）。

只包含 part=body 的节点（调用方保证），按文档顺序每个节点一行，以位置编号开头，有 clause_no 时编号放在文字前；
正文前加【主体说明】。文件中的 <document>、</document> 字样换成全角，防止提前闭合提示词中的 document 标签。
报价明细表（9.7 后半）整表（表头行至最后一个数据行）换成一行汇总，放在表头行第一个单元格的位置（M4）。
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.parsing.model import with_clause_no
from app.redaction.placeholders import OTHER, OUR_CODE
from app.redaction.quotes import QuoteTable, summary_text

_DOCUMENT_TAG = re.compile(r"<\s*/?\s*document\b[^<>]*>", re.IGNORECASE)
_LINE_BREAKS = re.compile(r"\s*[\r\n]+\s*")


@dataclass
class PartyInfo:
    code: str
    role_label: str
    country: str | None
    org_type: str | None
    is_our_party: bool


def escape_document_tags(text: str) -> str:
    """把 <document>、</document>（含大小写、空格变体）中的尖括号换成全角。"""
    return _DOCUMENT_TAG.sub(lambda m: m.group().replace("<", "＜").replace(">", "＞"), text)


def _letter_order(code: str) -> tuple[int, str]:
    suffix = code.rsplit("_", 1)[-1]
    return len(suffix), suffix


def party_description(parties: list[PartyInfo]) -> str:
    """【主体说明】我方=卖方；客户_A：买方，阿联酋，贸易商；收货人_B：土耳其，货代"""
    parts = []
    for p in sorted(parties, key=lambda p: (not p.is_our_party, _letter_order(p.code))):
        if p.is_our_party:
            parts.append(OUR_CODE if p.role_label == OTHER else f"{OUR_CODE}={p.role_label}")
            continue
        fields = []
        code_role = p.code.rsplit("_", 1)[0]
        if p.role_label != OTHER and p.role_label != code_role:
            fields.append(p.role_label)
        fields.append(p.country or "国家未知")
        fields.append(p.org_type or "类型未知")
        parts.append(f"{p.code}：{'，'.join(fields)}")
    return "【主体说明】" + ("；".join(parts) if parts else "无")


def node_line(loc: str, clause_no: str | None, redacted_text: str) -> str:
    text = _LINE_BREAKS.sub(" ", redacted_text.strip())
    return f"[{loc}] {escape_document_tags(with_clause_no(text, clause_no))}"


def build(parties: list[PartyInfo], nodes: list[tuple[str, str | None, str]],
          quote_tables: Sequence[QuoteTable] = ()) -> str:
    """nodes：[(loc, clause_no, redacted_text)]，已按文档顺序排列。

    quote_tables 中各表 row_locs 的节点不再单独成行，first_loc 处换成汇总行“[{表格位置}] 【报价明细表：…】”。
    """
    summary_at = {t.first_loc: t for t in quote_tables}
    skipped = {loc for t in quote_tables for loc in t.row_locs}
    lines = [party_description(parties)]
    for loc, clause_no, text in nodes:
        if loc in summary_at:
            t = summary_at[loc]
            lines.append(f"[{t.loc}] {escape_document_tags(summary_text(t))}")
        elif loc not in skipped:
            lines.append(node_line(loc, clause_no, text))
    return "\n".join(lines)
