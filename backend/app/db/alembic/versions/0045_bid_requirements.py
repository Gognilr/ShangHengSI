"""V34-B-02：要求清单的取值约束、模型补充状态，jobs 与 llm_calls 增加 bid。"""
import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

_JOBS = ["parse", "review", "backup", "retention", "law_import", "law_embed", "law_sync", "template_embed", "assist",
         "kb_import", "transcribe", "translate"]
_PURPOSES = ["review", "chat", "test", "assist"]
_REQ = {"group": ["qualification", "substantive", "veto", "pricing", "validity", "signing", "format"],
        "method": ["local", "model", "manual"], "source": ["marked", "model", "default", "manual"]}


def _in(column, values):
    return column + " IN (" + ", ".join(repr(v) for v in values) + ")"


def _replace(table, column, values):
    op.drop_constraint(op.f(f"ck_{table}_{column}_valid"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{column}_valid"), table, _in(column, values))


def upgrade():
    _replace("jobs", "type", _JOBS + ["bid"])
    _replace("llm_calls", "purpose", _PURPOSES + ["bid"])
    op.add_column("bid_projects", sa.Column("extract_state", sa.String(12)))
    op.add_column("bid_projects", sa.Column("extract_error", sa.String(200)))
    op.create_check_constraint("extract_state_valid", "bid_projects",
                               _in("extract_state", ["redacting", "awaiting", "extracting", "done", "failed"]))
    for column, values in _REQ.items():
        op.create_check_constraint(column + "_valid", "bid_requirements", _in('"group"' if column == "group" else column, values))


def downgrade():
    for column in _REQ:
        op.drop_constraint(op.f(f"ck_bid_requirements_{column}_valid"), "bid_requirements", type_="check")
    op.drop_constraint(op.f("ck_bid_projects_extract_state_valid"), "bid_projects", type_="check")
    op.drop_column("bid_projects", "extract_error")
    op.drop_column("bid_projects", "extract_state")
    op.execute("DELETE FROM llm_calls WHERE purpose='bid'")
    op.execute("DELETE FROM jobs WHERE type='bid'")
    _replace("llm_calls", "purpose", _PURPOSES)
    _replace("jobs", "type", _JOBS)
