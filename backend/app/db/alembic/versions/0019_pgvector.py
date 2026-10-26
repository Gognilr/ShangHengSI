"""V2-E-04 本地向量与全文暂存。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.create_table('law_articles',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('law_id', sa.BigInteger(), sa.ForeignKey('laws.id', ondelete='CASCADE'), nullable=False),
        sa.Column('article_no', sa.String(30)),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(1024)),
        sa.UniqueConstraint('law_id', 'seq'))
    op.create_index('ix_law_articles_embedding', 'law_articles', ['embedding'],
                    postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_table('law_import_articles',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('import_id', sa.Uuid(), sa.ForeignKey('law_imports.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('published_on', sa.Date(), nullable=False),
        sa.Column('article_no', sa.String(30)),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False))
    op.create_table('task_clause_vectors',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('task_id', sa.Uuid(), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_seq', sa.Integer(), nullable=False),
        sa.Column('first_loc', sa.String(20), nullable=False),
        sa.Column('locs', JSONB(), nullable=False),
        sa.Column('embedding', Vector(1024), nullable=False),
        sa.Column('model', sa.String(60), nullable=False))
    op.create_index('ix_task_clause_vectors_task_id', 'task_clause_vectors', ['task_id'])
    op.drop_constraint(op.f('ck_jobs_type_valid'), 'jobs', type_='check')
    op.create_check_constraint('type_valid', 'jobs', "type IN ('parse', 'review', 'backup', 'retention', 'law_import', 'law_embed')")


def downgrade():
    op.execute("DELETE FROM jobs WHERE type = 'law_embed'")
    op.drop_constraint(op.f('ck_jobs_type_valid'), 'jobs', type_='check')
    op.create_check_constraint('type_valid', 'jobs', "type IN ('parse', 'review', 'backup', 'retention', 'law_import')")
    op.drop_table('task_clause_vectors')
    op.drop_table('law_import_articles')
    op.drop_table('law_articles')
    op.execute('DROP EXTENSION IF EXISTS vector')
