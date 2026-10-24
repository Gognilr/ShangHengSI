"""本地校验的公共纯数据结构（设计 M6 第二节）。调用方只传入正文节点。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckNode:
    loc: str
    text: str
    file_seq: int
    clause_no: str | None


@dataclass(frozen=True)
class CheckFile:
    seq: int
    is_main: bool
    ext: str
    doc_type: str | None
    warnings: list[str]


@dataclass
class CheckResult:
    code: str
    severity: str
    locs: list[str]
    file_seq: int | None
    detail: str
    ai_summary: str


SEVERITY = {"L-01": "high", "L-02": "high", "L-03": "medium", "L-04": "high", "L-05": "high",
            "L-06": "medium", "L-07": "medium", "L-08": "medium", "L-INFO": "low",
            "L-09": "high", "L-10": "medium", "L-11": "medium"}
