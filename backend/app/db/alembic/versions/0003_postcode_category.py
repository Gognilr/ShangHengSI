"""脱敏类别增加邮编 POSTCODE（SPEC 9.2、Q-139 ①，设计 R1.md 第十一节）

redaction_items、redaction_suspects 的 category CHECK 增加 POSTCODE。
降级时先删除 POSTCODE 行，再恢复原约束。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

_OLD = ['OUR', 'ORG', 'PERSON', 'PHONE', 'EMAIL', 'URL', 'ADDR', 'ACCOUNT',
        'SWIFT', 'IDNO', 'REFNO', 'AMOUNT', 'PRICE', 'QTY', 'CUSTOM']
_NEW = ['OUR', 'ORG', 'PERSON', 'PHONE', 'EMAIL', 'URL', 'ADDR', 'POSTCODE', 'ACCOUNT',
        'SWIFT', 'IDNO', 'REFNO', 'AMOUNT', 'PRICE', 'QTY', 'CUSTOM']
_TABLES = ['redaction_items', 'redaction_suspects']


def _replace_check(values: list[str]) -> None:
    sql = "category IN (" + ", ".join(f"'{v}'" for v in values) + ")"
    for table in _TABLES:
        name = f'ck_{table}_category_valid'
        op.drop_constraint(op.f(name), table, type_='check')
        op.create_check_constraint(op.f(name), table, sql)


def upgrade() -> None:
    _replace_check(_NEW)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DELETE FROM {table} WHERE category = 'POSTCODE'")
    _replace_check(_OLD)
