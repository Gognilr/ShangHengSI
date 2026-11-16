"""V34-B-03：标书本地检查的状态与覆盖说明。"""
import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bid_projects", sa.Column("check_state", sa.String(12)))
    op.add_column("bid_projects", sa.Column("check_error", sa.String(200)))
    op.add_column("bid_projects", sa.Column("check_enc", sa.LargeBinary()))
    op.create_check_constraint("check_state_valid", "bid_projects", "check_state IN ('running', 'done', 'failed')")
    op.create_index("ix_bid_images_file_id", "bid_images", ["file_id"])
    op.create_index("ix_bid_items_task_id", "bid_items", ["task_id"])


def downgrade():
    op.drop_index("ix_bid_items_task_id", "bid_items")
    op.drop_index("ix_bid_images_file_id", "bid_images")
    op.drop_constraint(op.f("ck_bid_projects_check_state_valid"), "bid_projects", type_="check")
    for column in ("check_enc", "check_error", "check_state"):
        op.drop_column("bid_projects", column)
