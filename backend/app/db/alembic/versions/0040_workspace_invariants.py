"""V32-A-01：材料版本、文件序号高水位、审核轮次文件范围、讨论稿与锚点列（只加列，不转换数据）。"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("material_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tasks", sa.Column("last_file_seq", sa.Integer(), nullable=False, server_default="0"))
    op.execute("UPDATE tasks SET last_file_seq = COALESCE((SELECT MAX(seq) FROM task_files WHERE task_files.task_id = tasks.id), 0)")
    op.add_column("review_runs", sa.Column("material_version", sa.Integer(), nullable=True))
    op.add_column("review_runs", sa.Column("file_ids", postgresql.JSONB(), nullable=True))
    op.add_column("assistant_messages", sa.Column("material_version", sa.Integer(), nullable=True))
    op.add_column("assistant_messages", sa.Column("finding_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_assistant_messages_finding_id_findings", "assistant_messages", "findings",
                          ["finding_id"], ["id"], ondelete="SET NULL")
    op.add_column("findings", sa.Column("discussed_text", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("discussed_message_id", sa.BigInteger(), nullable=True))
    op.add_column("findings", sa.Column("discussed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ("discussed_at", "discussed_message_id", "discussed_text"):
        op.drop_column("findings", column)
    op.drop_constraint("fk_assistant_messages_finding_id_findings", "assistant_messages", type_="foreignkey")
    op.drop_column("assistant_messages", "finding_id")
    op.drop_column("assistant_messages", "material_version")
    op.drop_column("review_runs", "file_ids")
    op.drop_column("review_runs", "material_version")
    op.drop_column("tasks", "last_file_seq")
    op.drop_column("tasks", "material_version")
