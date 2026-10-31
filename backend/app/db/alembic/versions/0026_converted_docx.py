"""V3-C-03：加密保存旧文字格式的转换结果。"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_files", sa.Column("converted_storage_key", sa.String(64)))


def downgrade():
    op.drop_column("task_files", "converted_storage_key")
