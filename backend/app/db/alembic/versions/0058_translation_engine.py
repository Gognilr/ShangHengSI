"""翻译会话在首次翻译之前也能恢复明确选过的方式。"""
from alembic import op
import sqlalchemy as sa

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("translation_engine", sa.String(5), nullable=True))


def downgrade():
    op.drop_column("tasks", "translation_engine")
