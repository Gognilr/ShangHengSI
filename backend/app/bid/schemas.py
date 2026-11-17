from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProjectInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    opening_date: date
    policy: Literal["ai", "local_only"] = "local_only"

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value.strip()


class BidderInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("投标人名称不能为空")
        return value.strip()


class PasswordInput(BaseModel):
    password: str = Field(max_length=256)


Group = Literal["qualification", "substantive", "veto", "pricing", "validity", "signing", "format"]
Method = Literal["local", "model", "manual"]


class ParametersInput(BaseModel):
    """关键参数；金额以字符串传递，按 Decimal 校验，不用浮点数。"""
    ceiling: str | None = Field(None, max_length=32)
    currency: Literal["CNY", "USD", "EUR", "HKD"] = "CNY"
    validity_days: int | None = Field(None, ge=1, le=3650)
    deposit: str | None = Field(None, max_length=32)
    deposit_forms: str = Field("", max_length=100)
    consortium: bool | None = None
    tax_included: bool | None = None

    @field_validator("ceiling", "deposit")
    @classmethod
    def decimal_text(cls, value):
        from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
        if value is None or not value.strip():
            return None
        try:
            number = Decimal(value.strip().replace(",", ""))
        except InvalidOperation:
            raise ValueError("请填写数字金额") from None
        if number < 0 or not number.is_finite():
            raise ValueError("请填写数字金额")
        return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class Loc(BaseModel):
    file_id: str = Field(max_length=40)
    loc: str = Field(max_length=64)
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class RequirementInput(BaseModel):
    group: Group
    text: str = Field(min_length=1, max_length=2000)
    method: Method = "manual"
    locs: list[Loc] = Field(default_factory=list, max_length=20)

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value):
        if not value.strip():
            raise ValueError("要求文字不能为空")
        return value.strip()


class RequirementPatch(BaseModel):
    group: Group | None = None
    text: str | None = Field(None, min_length=1, max_length=2000)
    method: Method | None = None
    enabled: bool | None = None

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("要求文字不能为空")
        return value.strip() if value else value


class ReviewInput(BaseModel):
    """核对状态与备注（V34-B-06）；只传要改的一项。"""
    review_status: Literal["pending", "verified", "excluded"] | None = None
    note: str | None = Field(None, max_length=500)


class CellReviewInput(ReviewInput):
    requirement_id: str
    bidder_id: str


class SigningInput(BaseModel):
    file_id: str
    page: int | None = None
    loc: str | None = Field(None, max_length=64)
    mark: Literal["stamped", "missing", "unclear"] | None = None


class CellRef(BaseModel):
    requirement_id: str
    bidder_id: str


class SignRef(BaseModel):
    file_id: str
    page: int | None = None
    loc: str | None = Field(None, max_length=64)


class BatchReviewInput(BaseModel):
    """批量核对（V34-B-06c）：条目与单元格设同一核对状态，签章页设同一标注；一次事务，任一项不属于本项目则全部不改。"""
    items: list[str] = Field(default_factory=list, max_length=2000)
    cells: list[CellRef] = Field(default_factory=list, max_length=2000)
    review_status: Literal["pending", "verified", "excluded"] | None = None
    signing: list[SignRef] = Field(default_factory=list, max_length=500)
    mark: Literal["stamped", "missing", "unclear"] | None = None
