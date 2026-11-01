"""V31-S-04：草稿状态与文件级解析错误。"""
import sqlalchemy as sa
from alembic import op

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None
_OLD = "'parsing','parse_failed','awaiting_confirm','reviewing','reviewed','completed_local','active'"


def upgrade():
    op.drop_constraint(op.f('ck_tasks_status_valid'), 'tasks', type_='check')
    op.create_check_constraint('status_valid', 'tasks', f"status IN ({_OLD},'draft')")
    op.add_column('task_files', sa.Column('parse_error', sa.Text()))


def downgrade():
    op.execute("UPDATE tasks SET status='awaiting_confirm' WHERE status='draft'")
    op.drop_column('task_files', 'parse_error')
    op.drop_constraint(op.f('ck_tasks_status_valid'), 'tasks', type_='check')
    op.create_check_constraint('status_valid', 'tasks', f"status IN ({_OLD})")
