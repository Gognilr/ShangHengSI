"""V2-E-01 法规库与加密目录导入。"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'laws',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('category', sa.String(60), nullable=False),
        sa.Column('office', sa.String(100)),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('published_on', sa.Date()),
        sa.Column('effective_on', sa.Date()),
        sa.Column('repealed_on', sa.Date()),
        sa.Column('replaced_by', sa.String(300)),
        sa.Column('flk_id', sa.String(32)),
        sa.Column('source', sa.String(10), nullable=False),
        sa.Column('fulltext_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('title', 'published_on', postgresql_nulls_not_distinct=True),
        sa.UniqueConstraint('flk_id'),
        sa.CheckConstraint("status IN ('not_yet', 'valid', 'amended', 'repealed')", name='status_valid'),
        sa.CheckConstraint("source IN ('builtin', 'import', 'sync')", name='source_valid'),
    )
    op.create_index('ix_laws_title', 'laws', ['title'])
    op.create_table(
        'law_imports',
        sa.Column('id', sa.Uuid(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('declared_status', sa.String(10)),
        sa.Column('file_names', JSONB(), nullable=False),
        *[sa.Column(n, sa.Integer(), nullable=False, server_default='0')
          for n in ('added', 'changed', 'unchanged', 'skipped')],
        sa.Column('diff', JSONB()),
        sa.Column('error', sa.String(200)),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('applied_by', sa.Uuid(), sa.ForeignKey('users.id')),
        sa.Column('applied_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('catalog', 'fulltext', 'sync')", name='kind_valid'),
        sa.CheckConstraint("status IN ('parsing', 'pending', 'applied', 'discarded', 'failed')", name='status_valid'),
        sa.CheckConstraint("declared_status IN ('not_yet', 'valid', 'amended', 'repealed')", name='declared_status_valid'),
    )
    op.create_table(
        'law_import_files',
        sa.Column('id', sa.Uuid(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('import_id', sa.Uuid(), sa.ForeignKey('law_imports.id', ondelete='CASCADE'), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('data_enc', sa.LargeBinary(), nullable=False),
    )
    op.drop_constraint(op.f('ck_jobs_type_valid'), 'jobs', type_='check')
    op.create_check_constraint('type_valid', 'jobs', "type IN ('parse', 'review', 'backup', 'retention', 'law_import')")


def downgrade():
    # 降级时只移除本批新增类型的队列记录，旧类型保持原样。
    op.execute("DELETE FROM jobs WHERE type = 'law_import'")
    op.drop_constraint(op.f('ck_jobs_type_valid'), 'jobs', type_='check')
    op.create_check_constraint('type_valid', 'jobs', "type IN ('parse', 'review', 'backup', 'retention')")
    op.drop_table('law_import_files')
    op.drop_table('law_imports')
    op.drop_index('ix_laws_title', table_name='laws')
    op.drop_table('laws')
