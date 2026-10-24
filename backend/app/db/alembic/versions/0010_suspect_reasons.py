"""V2-B-03：派生名称与叙述人名；V2Q-31 确认顺延为 0010。"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None
_OLD_REASONS = ["org_suffix", "capitalized", "long_digits", "before_company", "person_name"]
_OLD_SOURCES = ["term", "party", "alias", "regex", "amount", "person", "address", "user"]


def _replace(table, column, values):
    name = op.f(f"ck_{table}_{column}_valid")
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, column + " IN (" + ", ".join(f"'{v}'" for v in values) + ")")


def upgrade():
    _replace("redaction_suspects", "reason", [*_OLD_REASONS, "derived_name", "person_like"])
    _replace("redaction_items", "detected_by", [*_OLD_SOURCES, "derived"])


def downgrade():
    op.execute("DELETE FROM redaction_suspects WHERE reason IN ('derived_name', 'person_like')")
    op.execute("DELETE FROM redaction_items WHERE detected_by = 'derived'")
    _replace("redaction_suspects", "reason", _OLD_REASONS)
    _replace("redaction_items", "detected_by", _OLD_SOURCES)
