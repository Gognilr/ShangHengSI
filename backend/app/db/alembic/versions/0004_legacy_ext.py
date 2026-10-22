"""task_files.ext 增加旧格式 doc、xls、wps、et、rtf（SPEC 8.8、18.6，设计 R2.md 第一节）

降级时如有旧格式文件则拒绝（不自动删除用户的任务和原件；Q-146），没有时恢复原约束。

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

_OLD = ['docx', 'pdf', 'xlsx', 'jpg', 'png']
_LEGACY = ['doc', 'xls', 'wps', 'et', 'rtf']
_NAME = 'ck_task_files_ext_valid'


def _replace_check(values: list[str]) -> None:
    op.drop_constraint(op.f(_NAME), 'task_files', type_='check')
    op.create_check_constraint(op.f(_NAME), 'task_files', "ext IN (" + ", ".join(f"'{v}'" for v in values) + ")")


def upgrade() -> None:
    _replace_check(_OLD + _LEGACY)


def downgrade() -> None:
    legacy = ", ".join(f"'{v}'" for v in _LEGACY)
    count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM task_files WHERE ext IN ({legacy})")).scalar()
    if count:
        raise RuntimeError(f"有 {count} 个旧格式文件，不能降级到 0003；请先删除这些任务")
    _replace_check(_OLD)
