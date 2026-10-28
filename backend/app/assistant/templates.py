"""模板参数只允许文件标识与固定选项，不能把未脱敏正文藏入参数。"""
import re
from dataclasses import dataclass
from pathlib import Path

from app.security.errors import AppError

RESOURCE = Path(__file__).resolve().parents[2] / "resources/prompts/assistant_templates.md"
TARGETS = ("中文", "英文", "日文", "韩文", "俄文", "西班牙文", "法文", "德文", "阿拉伯文")
_RULES = {
    "translate": ({"file_id", "target"}, 1, 1),
    "summary": ({"file_ids", "focus"}, 1, 3),
    "reply": ({"file_ids", "tone", "language"}, 0, 3),
    "crosscheck": ({"file_ids"}, 2, 6),
    "compare": ({"file_ids"}, 2, 6),
}


@dataclass(frozen=True)
class Prepared:
    params: dict
    message: str


def _bad():
    raise AppError(422, "VALIDATION_ERROR", "模板参数不正确")


def prepare(template: str, params: dict, files: dict[str, int], message: str = "") -> Prepared:
    if template not in _RULES or not isinstance(params, dict):
        _bad()
    allowed, minimum, maximum = _RULES[template]
    if set(params) - allowed:
        _bad()
    ids = [params.get("file_id")] if template == "translate" else params.get("file_ids")
    if not isinstance(ids, list) or any(not isinstance(fid, str) or fid not in files for fid in ids):
        _bad()
    if not minimum <= len(ids) <= maximum or len(set(ids)) != len(ids):
        _bad()
    ids = sorted(ids, key=files.__getitem__)
    result = {**params, "file_ids": ids}
    seqs = [files[fid] for fid in ids]
    listed = "、".join(str(seq) for seq in seqs)
    ranged = f"{seqs[0]}～{seqs[-1]}" if len(seqs) > 1 and seqs == list(range(seqs[0], seqs[-1] + 1)) else listed
    if template == "translate":
        if params.get("target") not in TARGETS:
            _bad()
        default = f"把文件 {listed} 翻译为{params['target']}"
    elif template == "summary":
        result["focus"] = params.get("focus", "全部")
        if result["focus"] not in ("全部", "义务与期限", "金额与付款", "风险"):
            _bad()
        default = f"概括文件 {listed} 的要点"
    elif template == "reply":
        if params.get("tone") not in ("正式", "简洁", "友好坚定") or params.get("language") not in ("中文", "英文", "同来函"):
            _bad()
        if not ids and not message.strip():
            _bad()
        default = f"根据文件 {listed} 起草回复"
    elif template == "crosscheck":
        default = f"核对文件 {ranged} 之间是否一致"
    else:
        default = f"比较文件 {ranged} 的报价"
    return Prepared(result, message.strip() or default)


def instruction(template: str) -> str:
    if template not in _RULES:
        _bad()
    text = RESOURCE.read_text(encoding="utf-8")
    return text.split(f"## {template}\n", 1)[1].split("\n## ", 1)[0].strip()


def draft_only(text: str) -> str:
    parts = re.split(r"(?m)^---[ \t]*\r?$", text, maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""
