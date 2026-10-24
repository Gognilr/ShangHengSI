"""V2-A-07：补充要求来源与 300 字长度（V2Q-23）。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('company_requirements', 'text', type_=sa.String(300), existing_type=sa.String(200))
    op.add_column('company_requirements', sa.Column('sources', JSONB, nullable=False, server_default=sa.text("'[\"all\"]'::jsonb")))


def downgrade():
    op.execute('UPDATE company_requirements SET text = left(text, 200) WHERE length(text) > 200')
    op.alter_column('company_requirements', 'text', type_=sa.String(200), existing_type=sa.String(300))
    op.drop_column('company_requirements', 'sources')
