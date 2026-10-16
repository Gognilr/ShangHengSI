"""模型输出结构（SPEC 11.3，不联网版）。

只校验结构；取值（类别、严重程度、结论、制裁等级）在 validate.py 中映射和兜底。
结构中未定义的字段一律丢弃（11.4 第 9 条）。
"""
from pydantic import BaseModel, ConfigDict, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _as_list(value):
    """模型偶尔把单个编号写成字符串，这里统一成列表；null 视为空列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return value


class RequirementResultOut(_Model):
    req_id: str
    verdict: str
    note: str | None = None


class FindingOut(_Model):
    title: str
    category: str = ""
    severity: str = ""
    req_ids: list[str] = []
    locs: list[str] = []
    quote: str | None = None
    missing: bool = False
    problem: str
    impact: str | None = None
    suggestion: str | None = None
    suggested_text: str | None = None
    basis: str | None = None

    @field_validator("req_ids", "locs", mode="before")
    @classmethod
    def _lists(cls, value):
        return _as_list(value)


class SanctionsOut(_Model):
    level: str = ""
    reasons: list[str] = []

    @field_validator("reasons", mode="before")
    @classmethod
    def _lists(cls, value):
        return _as_list(value)


class ModelOutput(_Model):
    doc_summary: str = ""
    requirement_results: list[RequirementResultOut]
    findings: list[FindingOut]
    sanctions: SanctionsOut | None = None

    @field_validator("findings", mode="before")
    @classmethod
    def _none_findings(cls, value):
        return [] if value is None else value
