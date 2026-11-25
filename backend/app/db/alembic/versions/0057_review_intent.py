"""文件审核入口创建的会话在首轮前也按文件审核恢复。"""
from alembic import op
import sqlalchemy as sa

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("review_intent", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("tasks", "review_intent")
