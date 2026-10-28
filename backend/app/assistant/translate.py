"""翻译纯逻辑：不拆节点、按位置对齐、在本地按目标书写系统选择已存在的写法。"""
import re
import unicodedata

from app.redaction.placeholders import NEUTRAL
from app.security.errors import AppError


def require_quota(segment_count: int, remaining: dict[str, int | None]) -> None:
    limits = [value for value in remaining.values() if value is not None]
    if limits and segment_count > min(limits):
        raise AppError(409, "ASSIST_QUOTA", f"翻译需要调用 {segment_count} 次，今日剩余 {min(limits)} 次")


def segments(nodes: list[tuple[str, str]], limit: int = 4000) -> list[list[tuple[str, str]]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("段长必须为正整数")
    result, part, count = [], [], 0
    for loc, text in nodes:
        size = len(f"⟦{loc}⟧ {text}")
        if part and count + 1 + size > limit:
            result.append(part)
            part, count = [], 0
        count += size + (1 if part else 0)
        part.append((loc, text))
    if part:
        result.append(part)
    return result


def align(text: str, nodes: list[tuple[str, str]]) -> tuple[dict[str, str], bool]:
    if not nodes:
        return {}, bool(text.strip())
    matches = list(re.finditer(r"(?m)^⟦([^⟧\r\n]+)⟧[ \t]*", text))
    expected = {loc for loc, _ in nodes}
    actual = [match[1] for match in matches]
    if len(actual) != len(expected) or set(actual) != expected or (matches and text[:matches[0].start()].strip()):
        return {nodes[0][0]: text}, True
    return {match[1]: text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
            for i, match in enumerate(matches)}, False


def _script(value: str, target: str) -> bool:
    names = [unicodedata.name(char, "") for char in value if char.isalpha()]
    if not names:
        return False
    han = lambda name: name.startswith(("CJK", "IDEOGRAPHIC"))
    if target == "中文":
        return all(han(name) for name in names)
    if target in ("英文", "西班牙文", "法文", "德文"):
        return all(name.startswith("LATIN") for name in names)
    if target == "日文":
        return any(name.startswith(("HIRAGANA", "KATAKANA")) for name in names) and all(
            han(name) or name.startswith(("HIRAGANA", "KATAKANA")) for name in names)
    if target == "韩文":
        return any(name.startswith("HANGUL") for name in names) and all(han(name) or name.startswith("HANGUL") for name in names)
    prefix = {"俄文": "CYRILLIC", "阿拉伯文": "ARABIC"}.get(target)
    return bool(prefix) and all(name.startswith(prefix) for name in names)


def target_mapping(mapping: dict[str, str], target: str) -> dict[str, str]:
    result = {}
    for code, primary in mapping.items():
        if not NEUTRAL.fullmatch(code):
            continue
        alternatives = [primary] + [value for key, value in mapping.items() if key.startswith(code + "#")]
        result[code] = next((value for value in alternatives if _script(value, target)), primary)
    return result


def task_segments(db, task, params):
    from sqlalchemy import select
    from app.assistant.prompt import selected_files
    from app.admin.settings_store import get_setting
    from app.db.models import DocNode
    files = selected_files(db, task, params)
    if len(files) != 1:
        raise AppError(422, "VALIDATION_ERROR", "翻译只能选择一个文件")
    rows = db.scalars(select(DocNode).where(DocNode.file_id == files[0].id, DocNode.part == "body").order_by(DocNode.ord))
    nodes = [(row.loc, row.redacted_text or "") for row in rows]
    if not nodes:
        raise AppError(422, "VALIDATION_ERROR", "所选文件没有可翻译的正文")
    return segments(nodes, get_setting(db, "assistant.translate_segment_chars"))
