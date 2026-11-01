"""V31-S-05：本地审核确认操作统计。"""
import sqlalchemy as sa
from alembic import op

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('confirm_ops', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('tasks', sa.Column('awaiting_since', sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column('tasks', 'awaiting_since')
    op.drop_column('tasks', 'confirm_ops')
