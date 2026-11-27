"""V36-I-01：jobs 的类型增加 invoice（发票管家识别任务，不新增表）。"""
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None

_JOBS = ["parse", "review", "backup", "retention", "law_import", "law_embed", "law_sync", "template_embed", "assist",
         "kb_import", "transcribe", "translate", "bid"]


def _replace(values):
    op.drop_constraint(op.f("ck_jobs_type_valid"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_type_valid"), "jobs", "type IN (" + ", ".join(repr(v) for v in values) + ")")


def upgrade():
    _replace(_JOBS + ["invoice"])


def downgrade():
    op.execute("DELETE FROM jobs WHERE type = 'invoice'")
    _replace(_JOBS)
