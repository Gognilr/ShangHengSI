"""V35-D-05 审核结果沉淀为待审批的范本条款或标书补充要求。"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("text_enc", sa.LargeBinary(), nullable=False),
        sa.Column("doc_type", sa.String(20)),
        sa.Column("stance", sa.String(12)),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("bid_categories.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(8), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("kind", "source_id", "content_hash"),
        sa.CheckConstraint("kind IN ('template', 'bid')", name="kind_valid"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'returned')", name="status_valid"),
    )


def downgrade() -> None:
    op.drop_table("content_drafts")
