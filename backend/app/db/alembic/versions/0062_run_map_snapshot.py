"""V36-R-03：review_runs 增加对照表快照（加密），旧轮次、追问按创建时的代号还原。"""
import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("review_runs", sa.Column("map_enc", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("review_runs", "map_enc")
