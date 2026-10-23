"""补充说明原文加密、代号快照（R4-03，Q-154）。旧开发数据按确认方案丢弃。"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("user_note_enc", sa.LargeBinary(), nullable=True))
    op.add_column("tasks", sa.Column("user_note_redacted", sa.Text(), nullable=True))
    op.drop_column("tasks", "user_note")
    op.execute("UPDATE review_runs SET user_note = NULL")
    op.alter_column("review_runs", "user_note", existing_type=sa.String(500), type_=sa.Text())
    op.execute("UPDATE requirement_results SET req_text = '（本次补充说明，升级时已清除）' WHERE req_source = 'note'")


def downgrade() -> None:
    op.add_column("tasks", sa.Column("user_note", sa.String(500), nullable=True))
    op.alter_column("review_runs", "user_note", existing_type=sa.Text(), type_=sa.String(500),
                    postgresql_using="left(user_note, 500)")
    op.drop_column("tasks", "user_note_redacted")
    op.drop_column("tasks", "user_note_enc")
