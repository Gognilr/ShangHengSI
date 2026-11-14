"""V321-E-06：jobs.progress，解析阶段上报（文件 id、阶段、页数，不含文件名与正文）。"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("progress", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "progress")
