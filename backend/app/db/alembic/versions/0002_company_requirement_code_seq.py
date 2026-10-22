"""公司补充要求编号序列（CO-n，删除后不复用，SPEC 18.21、Q-107）

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE SEQUENCE company_requirement_code_seq START 1')


def downgrade() -> None:
    op.execute('DROP SEQUENCE company_requirement_code_seq')
