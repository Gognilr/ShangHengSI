"""V35-D-03 增量复审：轮次范围与基准轮，问题的本轮状态。

* review_runs.scope（full / delta，默认 full）、review_runs.base_run_id（增量轮的基准轮，删除时置空）。
* findings.carry（new / persists / unchanged / resolved，全文轮为空）、findings.prev_finding_id（上轮对应问题，删除时置空）、
  findings.carry_note（模型给的一句依据，代号形式）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_runs", sa.Column("scope", sa.String(5), server_default="full", nullable=False))
    op.add_column("review_runs", sa.Column("base_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_review_runs_base_run_id_review_runs"), "review_runs", "review_runs",
                          ["base_run_id"], ["id"], ondelete="SET NULL")
    op.add_column("findings", sa.Column("carry", sa.String(10), nullable=True))
    op.add_column("findings", sa.Column("prev_finding_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_findings_prev_finding_id_findings"), "findings", "findings",
                          ["prev_finding_id"], ["id"], ondelete="SET NULL")
    op.add_column("findings", sa.Column("carry_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("findings", "carry_note")
    op.drop_constraint(op.f("fk_findings_prev_finding_id_findings"), "findings", type_="foreignkey")
    op.drop_column("findings", "prev_finding_id")
    op.drop_column("findings", "carry")
    op.drop_constraint(op.f("fk_review_runs_base_run_id_review_runs"), "review_runs", type_="foreignkey")
    op.drop_column("review_runs", "base_run_id")
    op.drop_column("review_runs", "scope")
