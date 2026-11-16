"""V34-B-06：签章页速览的人工标注。"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bid_projects", sa.Column("signing", postgresql.JSONB()))


def downgrade():
    op.drop_column("bid_projects", "signing")
