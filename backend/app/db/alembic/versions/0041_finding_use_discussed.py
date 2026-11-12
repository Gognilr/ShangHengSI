"""V32-B-03：问题采纳时所选的建议来源（原审核建议 / 讨论修改稿），只加列。"""
import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("use_discussed", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("findings", "use_discussed")
