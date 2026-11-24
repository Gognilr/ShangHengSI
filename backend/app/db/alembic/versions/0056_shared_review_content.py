"""V35-D-05 专职人员直接沉淀并撤回公司共享条文。"""
from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("content_drafts", "status", type_=sa.String(10), existing_type=sa.String(8), existing_nullable=False)
    op.drop_constraint(op.f("ck_content_drafts_status_valid"), "content_drafts", type_="check")
    op.create_check_constraint("status_valid", "content_drafts",
                               "status IN ('pending', 'approved', 'returned', 'active', 'withdrawn')")
    op.add_column("content_drafts", sa.Column("embedding", Vector(1024)))
    op.add_column("review_runs", sa.Column("use_shared_clauses", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("review_runs", "use_shared_clauses")
    op.drop_column("content_drafts", "embedding")
    op.drop_constraint(op.f("ck_content_drafts_status_valid"), "content_drafts", type_="check")
    op.execute("UPDATE content_drafts SET status = 'returned' WHERE status IN ('active', 'withdrawn')")
    op.alter_column("content_drafts", "status", type_=sa.String(8), existing_type=sa.String(10), existing_nullable=False)
    op.create_check_constraint("status_valid", "content_drafts", "status IN ('pending', 'approved', 'returned')")
