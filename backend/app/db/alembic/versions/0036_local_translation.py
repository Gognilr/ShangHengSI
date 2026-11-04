"""V31-U5-06：加密本地译稿与独立翻译队列。"""
import sqlalchemy as sa
from alembic import op

revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None

TYPES = ['parse', 'review', 'backup', 'retention', 'law_import', 'law_embed', 'law_sync',
         'template_embed', 'assist', 'kb_import', 'transcribe']


def upgrade():
    op.add_column('assistant_messages', sa.Column('local_result_enc', sa.LargeBinary(), nullable=True))
    op.drop_constraint(op.f('ck_jobs_type_valid'), 'jobs', type_='check')
    op.create_check_constraint(op.f('ck_jobs_type_valid'), 'jobs', 'type IN (' + ','.join(repr(v) for v in TYPES + ['translate']) + ')')


def downgrade():
    op.execute("DELETE FROM jobs WHERE type = 'translate'")
    op.execute('DELETE FROM assistant_messages WHERE local_result_enc IS NOT NULL')
    op.drop_constraint(op.f('ck_jobs_type_valid'), 'jobs', type_='check')
    op.create_check_constraint(op.f('ck_jobs_type_valid'), 'jobs', 'type IN (' + ','.join(repr(v) for v in TYPES) + ')')
    op.drop_column('assistant_messages', 'local_result_enc')
