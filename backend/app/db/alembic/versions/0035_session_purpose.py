"""V31-U5：助手会话的用途（对话 / 会议纪要 / 翻译），侧栏三个入口各列自己的记录。"""
import sqlalchemy as sa
from alembic import op

revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('purpose', sa.String(10), nullable=False, server_default='chat'))
    op.create_check_constraint(op.f('ck_tasks_purpose_valid'), 'tasks', "purpose IN ('chat','meeting','translate')")


def downgrade():
    op.drop_constraint(op.f('ck_tasks_purpose_valid'), 'tasks', type_='check')
    op.drop_column('tasks', 'purpose')
