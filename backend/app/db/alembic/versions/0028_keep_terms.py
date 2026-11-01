"""V31-S-03：加密术语记忆。"""
import sqlalchemy as sa
from alembic import op

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('keep_terms',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('term_hash', sa.String(64), nullable=False),
        sa.Column('term_enc', sa.LargeBinary(), nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE')),
        sa.Column('approved_by', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('hit_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_hit_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('term_hash', 'user_id'))
    op.create_index('uq_keep_terms_shared', 'keep_terms', ['term_hash'], unique=True,
                    postgresql_where=sa.text('user_id IS NULL'))


def downgrade():
    op.drop_table('keep_terms')
