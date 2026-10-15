"""SQLAlchemy 基类、约束命名规则和通用辅助（SPEC 18.1）。"""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, MetaData, Uuid, text
from sqlalchemy.orm import DeclarativeBase, mapped_column

# 约束与索引使用固定命名规则，保证迁移文件中的名称稳定
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# 时间字段一律 timestamptz，存 UTC
TIMESTAMPTZ = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_pk():
    """uuid 主键，由数据库用 pgcrypto 的 gen_random_uuid() 生成。"""
    return mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))


def enum_check(column: str, values: list[str]) -> CheckConstraint:
    """枚举字段用 varchar + CHECK 约束，不用数据库 enum 类型（可空字段为 NULL 时 CHECK 自然通过）。"""
    items = ", ".join("'" + v + "'" for v in values)
    sql = f"{column} IN ({items})"
    return CheckConstraint(sql, name=f"{column}_valid")
