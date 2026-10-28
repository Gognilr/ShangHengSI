"""V2-D-05：公司知识库，原文及名称加密，向量只留数据库。"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = '0024'
down_revision = '0023a'
branch_labels = None
depends_on = None
_JOBS = "'parse','review','backup','retention','law_import','law_embed','law_sync','template_embed','assist'"


def _jobs(values):
    op.drop_constraint(op.f('ck_jobs_type_valid'), 'jobs', type_='check')
    op.create_check_constraint('type_valid', 'jobs', f'type IN ({values})')


def upgrade():
    op.create_table('kb_docs',
        sa.Column('id', sa.Uuid(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name_enc', sa.LargeBinary(), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('error_message', sa.Text()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('chunk_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('content_hash', sa.CHAR(64), nullable=False),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('parsing','ready','failed')", name='status_valid'),
        sa.UniqueConstraint('content_hash'))
    op.create_index('ix_kb_docs_status_enabled', 'kb_docs', ['status', 'enabled'])
    op.create_table('kb_chunks',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('doc_id', sa.Uuid(), sa.ForeignKey('kb_docs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('heading_enc', sa.LargeBinary()),
        sa.Column('text_enc', sa.LargeBinary(), nullable=False),
        sa.Column('embedding', Vector(1024)),
        sa.UniqueConstraint('doc_id', 'seq'))
    op.create_index('ix_kb_chunks_embedding', 'kb_chunks', ['embedding'], postgresql_using='hnsw',
                    postgresql_ops={'embedding': 'vector_cosine_ops'})
    _jobs(_JOBS + ",'kb_import'")


def downgrade():
    op.execute("DELETE FROM jobs WHERE type='kb_import'")
    _jobs(_JOBS)
    op.drop_table('kb_chunks')
    op.drop_table('kb_docs')
