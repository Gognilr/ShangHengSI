"""V2-C-03：成功备份时的数据库与备份盘统计。"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("db_bytes", "dir_bytes", "dir_free_bytes"):
        op.add_column("backup_records", sa.Column(name, sa.BigInteger(), nullable=True))


def downgrade():
    for name in ("dir_free_bytes", "dir_bytes", "db_bytes"):
        op.drop_column("backup_records", name)
