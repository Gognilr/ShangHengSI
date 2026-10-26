"""V2-E-06 加密范本与轮次快照，V2Q-94 顺延。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision='0022'
down_revision='0021'
branch_labels=None
depends_on=None
_JOBS="'parse','review','backup','retention','law_import','law_embed','law_sync'"
_CODES=','.join("'L-%02d'"%n for n in range(1,12))+",'L-INFO'"


def upgrade():
    op.alter_column('jobs','type',type_=sa.String(20),existing_type=sa.String(12))
    op.create_table('standard_templates',
        sa.Column('id',sa.Uuid(),primary_key=True,server_default=sa.text('gen_random_uuid()')),
        sa.Column('name_enc',sa.LargeBinary(),nullable=False),
        sa.Column('doc_types',JSONB(),nullable=False),sa.Column('stances',JSONB(),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False,server_default=sa.text('true')),
        sa.Column('content_hash',sa.CHAR(64),nullable=False),sa.Column('clause_count',sa.Integer(),nullable=False),
        sa.Column('created_by',sa.Uuid(),sa.ForeignKey('users.id')),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('standard_clauses',
        sa.Column('id',sa.Uuid(),primary_key=True,server_default=sa.text('gen_random_uuid()')),
        sa.Column('template_id',sa.Uuid(),sa.ForeignKey('standard_templates.id',ondelete='CASCADE'),nullable=False),
        sa.Column('seq',sa.Integer(),nullable=False),sa.Column('title_enc',sa.LargeBinary(),nullable=False),
        sa.Column('text_enc',sa.LargeBinary(),nullable=False),sa.Column('stance_enc',sa.LargeBinary()),
        sa.Column('acceptable_enc',sa.LargeBinary()),sa.Column('escalate_enc',sa.LargeBinary()),
        sa.Column('embedding',Vector(1024)),sa.UniqueConstraint('template_id','seq'))
    op.add_column('review_runs',sa.Column('template_id',sa.Uuid()))
    op.create_foreign_key(op.f('fk_review_runs_template_id_standard_templates'),'review_runs','standard_templates',
                          ['template_id'],['id'],ondelete='SET NULL')
    op.add_column('review_runs',sa.Column('template_name_snapshot_enc',sa.LargeBinary()))
    op.add_column('review_runs',sa.Column('template_hash',sa.CHAR(64)))
    op.drop_constraint(op.f('ck_jobs_type_valid'),'jobs',type_='check')
    op.create_check_constraint('type_valid','jobs',f"type IN ({_JOBS},'template_embed')")
    op.drop_constraint(op.f('ck_local_findings_check_code_valid'),'local_findings',type_='check')
    op.create_check_constraint('check_code_valid','local_findings',f"check_code IN ({_CODES},'L-12')")


def downgrade():
    op.execute("DELETE FROM jobs WHERE type='template_embed'")
    op.alter_column('jobs','type',type_=sa.String(12),existing_type=sa.String(20))
    op.execute("DELETE FROM local_findings WHERE check_code='L-12'")
    op.drop_constraint(op.f('ck_jobs_type_valid'),'jobs',type_='check')
    op.create_check_constraint('type_valid','jobs',f"type IN ({_JOBS})")
    op.drop_constraint(op.f('ck_local_findings_check_code_valid'),'local_findings',type_='check')
    op.create_check_constraint('check_code_valid','local_findings',f"check_code IN ({_CODES})")
    op.drop_constraint(op.f('fk_review_runs_template_id_standard_templates'),'review_runs',type_='foreignkey')
    for name in ('template_hash','template_name_snapshot_enc','template_id'):op.drop_column('review_runs',name)
    op.drop_table('standard_clauses')
    op.drop_table('standard_templates')
