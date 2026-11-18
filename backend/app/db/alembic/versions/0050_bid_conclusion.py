"""标书初审签认、核验记录与本人反馈。"""
import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"


def upgrade():
    op.add_column("bid_projects", sa.Column("conclusion_enc", sa.LargeBinary()))
    op.add_column("users", sa.Column("bid_feedback_enc", sa.LargeBinary()))


def downgrade():
    op.drop_column("users", "bid_feedback_enc")
    op.drop_column("bid_projects", "conclusion_enc")
