from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


Group = Literal["qualification", "substantive", "veto", "pricing", "validity", "signing", "general", "format"]
Method = Literal["local", "model", "manual"]


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
    # ★ / 否决条款的比对参数（工期类、类似业绩）；只传要改的项，空字符串即清空
    param: dict[str, str | int | None] | None = None

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("要求文字不能为空")
        return value.strip() if value else value


class DismissInput(BaseModel):
    """初审结论中的“不是问题”（V35-E-01），可撤销。"""
    key: str = Field(min_length=1, max_length=2000)
    dismissed: bool


class OpeningPriceInput(BaseModel):
    """唱标价（V34-B-06e）：小写金额为空即清除；大写与备注选填。"""
    amount: str | None = Field(None, max_length=32)
    upper: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=100)

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
        if value is None or not value.strip():
            return None
        try:
            number = Decimal(value.strip().replace(",", ""))
        except InvalidOperation:
            raise ValueError("请填写数字金额") from None
        if not number.is_finite() or number < 0:
            raise ValueError("请填写数字金额")
        return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

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


class SigningInput(BaseModel):
    file_id: str
    page: int | None = None
    loc: str | None = Field(None, max_length=64)
    mark: Literal["stamped", "missing", "unclear"] | None = None


class SignRef(BaseModel):
    file_id: str
    page: int | None = None
    loc: str | None = Field(None, max_length=64)


class SigningBatchInput(BaseModel):
    """签章页批量标注（本家全部已盖章）；一次事务，任一页不属于本项目则全部不改。"""
    signing: list[SignRef] = Field(min_length=1, max_length=500)
    mark: Literal["stamped", "missing", "unclear"] | None = None
