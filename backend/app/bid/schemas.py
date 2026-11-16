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
