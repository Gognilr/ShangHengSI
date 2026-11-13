"""V321-C-02：公司我方预置与本人选择分开，旧选择按原记录人读取。"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("redaction_terms", sa.Column("is_company_our_party", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("redaction_terms", sa.Column("our_party_user_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    # 管理员在“我方主体”录入过的旧记录本来就是公司预置（审计日志 op=our_party），升级后保持对所有人有效。
    op.execute("""
        UPDATE redaction_terms SET is_company_our_party = true
        WHERE kind = 'learned' AND is_our_party AND id::text IN (
            SELECT target_id FROM audit_logs
            WHERE action = 'setting_update' AND target_type = 'term' AND detail->>'op' = 'our_party')
    """)


def downgrade() -> None:
    op.drop_column("redaction_terms", "our_party_user_ids")
    op.drop_column("redaction_terms", "is_company_our_party")
