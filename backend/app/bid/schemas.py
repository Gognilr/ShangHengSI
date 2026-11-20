from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    opening_date: date
    policy: Literal["ai", "local_only"] = "local_only"
    # 分类与文件夹（V34-B-01a）；修改时只改请求中给出的项，开始初审后仍可改
    category_id: int | None = None
    folder_id: str | None = Field(None, max_length=40)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value.strip()


class FolderInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    parent_id: str | None = Field(None, max_length=40)


class FolderPatch(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class PlaceInput(BaseModel):
    """拖动归档：folder_id 为空即移到“未归档”。"""
    folder_id: str | None = Field(None, max_length=40)


class BidderInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("投标人名称不能为空")
        return value.strip()


class InviteesInput(BaseModel):
    """受邀投标单位名单（V34-B-01b）：整表替换。"""
    names: list[str] = Field(max_length=200)


class PasswordInput(BaseModel):
    password: str = Field(max_length=256)


Group = Literal["qualification", "substantive", "veto", "pricing", "validity", "signing", "format"]
Method = Literal["local", "model", "manual"]


class ParametersInput(BaseModel):
    """关键参数；金额以字符串传递，按 Decimal 校验，不用浮点数。"""
    ceiling: str | None = Field(None, max_length=32)
    quote_mode: Literal["fixed", "rate", "discount"] = "fixed"
    rate_limit: str | None = Field(None, max_length=16)
    currency: Literal["CNY", "USD", "EUR", "HKD"] = "CNY"
    validity_days: int | None = Field(None, ge=1, le=3650)
    deposit: str | None = Field(None, max_length=32)
    deposit_forms: str = Field("", max_length=100)
    consortium: bool | None = None
    tax_included: bool | None = None
    agency: str = Field("", max_length=255)
    construction_max: str | None = Field(None, max_length=24)
    delivery_max: str | None = Field(None, max_length=24)
    warranty_min: str | None = Field(None, max_length=24)
    defect_min: str | None = Field(None, max_length=24)
    performance_from: str | None = Field(None, max_length=10)
    performance_until: str | None = Field(None, max_length=10)
    performance_amount: str | None = Field(None, max_length=32)
    performance_count: int | None = Field(None, ge=1, le=999)

    @field_validator("ceiling", "deposit", "performance_amount")
    @classmethod
    def decimal_text(cls, value):
        from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
        if value is None or not value.strip():
            return None
        try:
            number = Decimal(value.strip().replace(",", ""))
            if not number.is_finite() or number < 0:
                raise ValueError("请填写数字金额")
            return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        except InvalidOperation:
            raise ValueError("请填写数字金额") from None

    @field_validator("rate_limit")
    @classmethod
    def percent_limit(cls, value):
        from app.bid.pricing import rate_value
        if value is None or not value.strip():
            return None
        if (number := rate_value(value)) is None:
            raise ValueError("请填写 0 至 100 之间的百分数，如 8 或 8%")
        return format(number, "f")

    @field_validator("construction_max", "delivery_max", "warranty_min", "defect_min")
    @classmethod
    def duration(cls, value):
        from app.bid.compliance import normalize_term
        if value is None or not value.strip():
            return None
        term = normalize_term(value)
        if term is None:
            raise ValueError("请填写整数和期限单位，如 120 日历天、20 工作日或 24 个月")
        return f"{term[0]}{term[1]}"

    @field_validator("performance_from", "performance_until")
    @classmethod
    def performance_date(cls, value):
        if value is None or not value.strip():
            return None
        try:
            parsed = date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            raise ValueError("请按年-月-日填写类似业绩签订日期") from None
        return parsed

    @model_validator(mode="after")
    def performance_date_order(self):
        if self.performance_from and self.performance_until and self.performance_from > self.performance_until:
            raise ValueError("类似业绩签订起日不能晚于止日")
        return self


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


class ConclusionReviewInput(BaseModel):
    key: str = Field(min_length=1, max_length=2000)
    status: Literal["verified", "excluded", "pending"]
    reason: Literal["template", "manufacturer", "recognition", "model", "other"] | None = None
    detail: str | None = Field(None, max_length=50)
    remember: bool = False


class OpeningPriceInput(BaseModel):
    """唱标价（V34-B-06e）：小写金额为空即清除；大写与备注选填。"""
    amount: str | None = Field(None, max_length=32)
    upper: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=100)

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        return ParametersInput.decimal_text(value)

    @field_validator("upper")
    @classmethod
    def valid_upper(cls, value):
        value = "".join((value or "").split())
        if not value:
            return None
        from app.bid.compliance import upper_value
        if upper_value(value) is None:
            raise ValueError("请填写大写金额，例如 壹佰万元整")
        return value

    @field_validator("note")
    @classmethod
    def valid_note(cls, value):
        return value.strip() or None if value else None


class ApplicabilityInput(BaseModel):
    applicable: bool


class ConclusionSignInput(BaseModel):
    signed: bool


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
