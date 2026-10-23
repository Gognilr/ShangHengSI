"""疑似遗漏增加叙述人名原因（R4-08，设计 11.2）。"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_OLD = ["org_suffix", "capitalized", "long_digits", "before_company"]


def _replace_check(values: list[str]) -> None:
    name = op.f("ck_redaction_suspects_reason_valid")
    op.drop_constraint(name, "redaction_suspects", type_="check")
    sql = "reason IN (" + ", ".join(f"'{v}'" for v in values) + ")"
    op.create_check_constraint(name, "redaction_suspects", sql)


def upgrade() -> None:
    _replace_check([*_OLD, "person_name"])


def downgrade() -> None:
    op.execute("DELETE FROM redaction_suspects WHERE reason = 'person_name'")
    _replace_check(_OLD)
