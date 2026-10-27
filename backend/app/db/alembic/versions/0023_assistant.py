"""V2-D-01：会话任务与仅存代号的消息。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None
_OLD = "'parsing','parse_failed','awaiting_confirm','reviewing','reviewed','completed_local'"


def upgrade():
    op.add_column("tasks", sa.Column("kind", sa.String(10), nullable=False, server_default="review"))
    op.create_check_constraint("kind_valid", "tasks", "kind IN ('review','assistant')")
    op.drop_constraint(op.f("ck_tasks_status_valid"), "tasks", type_="check")
    op.create_check_constraint("status_valid", "tasks", f"status IN ({_OLD},'active')")
    op.add_column("task_files", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.create_table("assistant_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("template", sa.String(20)),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_redacted", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error_code", sa.String(40)),
        sa.Column("extras", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user','assistant')", name="role_valid"),
        sa.CheckConstraint("template IN ('translate','summary','reply','crosscheck','compare')", name="template_valid"),
        sa.CheckConstraint("status IN ('queued','streaming','done','failed','stopped')", name="status_valid"),
        sa.CheckConstraint("role <> 'user' OR status = 'done'", name="user_done"))
    op.create_index("ix_assistant_messages_task_id_id", "assistant_messages", ["task_id", "id"])
    op.create_index("ix_assistant_messages_one_pending", "assistant_messages", ["task_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued','streaming')"))


def downgrade():
    # 会话没有审核语义，回退时删除会话，不伪装为旧版审核任务。
    op.execute("DELETE FROM jobs WHERE payload->>'task_id' IN (SELECT id::text FROM tasks WHERE kind='assistant')")
    op.execute("UPDATE llm_calls SET task_title=NULL WHERE task_id IN (SELECT id FROM tasks WHERE kind='assistant')")
    op.execute("DELETE FROM tasks WHERE kind='assistant'")
    op.drop_table("assistant_messages")
    op.drop_column("task_files", "confirmed_at")
    op.drop_constraint(op.f("ck_tasks_status_valid"), "tasks", type_="check")
    op.create_check_constraint("status_valid", "tasks", f"status IN ({_OLD})")
    op.drop_constraint(op.f("ck_tasks_kind_valid"), "tasks", type_="check")
    op.drop_column("tasks", "kind")
