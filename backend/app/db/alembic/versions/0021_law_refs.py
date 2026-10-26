"""V2-E-05 引用快照与问题来源；编号按 V2Q-94 顺延。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='0021'
down_revision='0020'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('findings',sa.Column('law_refs',JSONB(),nullable=False,server_default=sa.text("'[]'::jsonb")))
    op.add_column('findings',sa.Column('law_unverified',sa.Boolean(),nullable=False,server_default=sa.text('false')))
    op.add_column('findings',sa.Column('source',sa.String(10),nullable=False,server_default='model'))
    op.create_check_constraint('source_valid','findings',"source IN ('model','template')")


def downgrade():
    op.drop_constraint(op.f('ck_findings_source_valid'),'findings',type_='check')
    for name in ('source','law_unverified','law_refs'): op.drop_column('findings',name)
