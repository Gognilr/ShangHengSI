"""V34-B-05：标书模型核对（片段脱敏、确认、按家调用）的状态与单元格引用校验结果。"""
import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bid_projects", sa.Column("model_state", sa.String(12)))
    op.add_column("bid_projects", sa.Column("model_error", sa.String(200)))
    op.add_column("bid_projects", sa.Column("model_enc", sa.LargeBinary()))
    op.create_check_constraint("model_state_valid", "bid_projects",
                               "model_state IN ('preparing', 'awaiting', 'running', 'done', 'failed')")
    op.add_column("bid_cells", sa.Column("cite", sa.String(12)))


def downgrade():
    op.drop_column("bid_cells", "cite")
    op.drop_constraint(op.f("ck_bid_projects_model_state_valid"), "bid_projects", type_="check")
    for column in ("model_enc", "model_error", "model_state"):
        op.drop_column("bid_projects", column)
