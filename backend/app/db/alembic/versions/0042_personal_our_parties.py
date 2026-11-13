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


def downgrade() -> None:
    op.drop_column("redaction_terms", "our_party_user_ids")
    op.drop_column("redaction_terms", "is_company_our_party")
