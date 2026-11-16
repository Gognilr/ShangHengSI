"""V34-B-01：标书项目、投标人、要求、线索、响应与图片哈希。"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _task(*, primary=False):
    return sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, primary_key=primary)


def _list(name):
    return sa.Column(name, JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"))


def _check(name, values):
    return sa.CheckConstraint(name + " IN (" + ", ".join(repr(v) for v in values) + ")", name=name + "_valid")


def upgrade():
    op.drop_constraint(op.f("ck_tasks_kind_valid"), "tasks", type_="check")
    op.create_check_constraint("kind_valid", "tasks", "kind IN ('review', 'assistant', 'bid')")
    op.create_table("bid_projects", _task(primary=True),
        sa.Column("opening_date", sa.Date(), nullable=False),
        sa.Column("stage", sa.String(12), nullable=False, server_default="preparing"),
        sa.Column("parameters_enc", sa.LargeBinary()), sa.Column("pricing_enc", sa.LargeBinary()),
        sa.Column("requirements_version", sa.Integer(), nullable=False, server_default="0"),
        _check("stage", ["preparing", "ready", "checking", "checked"]))
    op.create_table("bid_bidders", _id(), _task(), sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name_enc", sa.LargeBinary(), nullable=False), sa.Column("code", sa.String(8), nullable=False),
        sa.UniqueConstraint("task_id", "seq"), sa.UniqueConstraint("task_id", "code"))
    op.add_column("task_files", sa.Column("bidder_id", sa.Uuid(), sa.ForeignKey("bid_bidders.id", ondelete="CASCADE")))
    op.add_column("task_files", sa.Column("password_enc", sa.LargeBinary()))
    op.add_column("task_files", sa.Column("meta_enc", sa.LargeBinary()))
    op.create_table("bid_requirements", _id(), _task(), sa.Column("group", sa.String(32), nullable=False),
        sa.Column("text_enc", sa.LargeBinary(), nullable=False), sa.Column("redacted_text", sa.Text()), _list("locs"),
        sa.Column("method", sa.String(16), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(16), nullable=False), sa.Column("seq", sa.Integer(), nullable=False))
    op.create_table("bid_items", _id(), _task(), sa.Column("kind", sa.String(12), nullable=False), _list("bidder_ids"),
        sa.Column("requirement_id", sa.Uuid(), sa.ForeignKey("bid_requirements.id", ondelete="SET NULL")),
        sa.Column("type", sa.String(40), nullable=False), sa.Column("level", sa.String(16), nullable=False),
        sa.Column("article", sa.String(40)), sa.Column("evidence_enc", sa.LargeBinary(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("review_status", sa.String(10), nullable=False, server_default="pending"), sa.Column("note_enc", sa.LargeBinary()),
        _check("kind", ["issue", "clue", "normal_dup"]), _check("source", ["local", "model"]),
        _check("review_status", ["pending", "verified", "excluded"]))
    op.create_table("bid_cells", _id(), _task(),
        sa.Column("requirement_id", sa.Uuid(), sa.ForeignKey("bid_requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bidder_id", sa.Uuid(), sa.ForeignKey("bid_bidders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False), _list("locs"), sa.Column("reason_enc", sa.LargeBinary()),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("review_status", sa.String(10), nullable=False, server_default="pending"), sa.Column("note_enc", sa.LargeBinary()),
        sa.UniqueConstraint("requirement_id", "bidder_id"), _check("source", ["local", "model"]),
        _check("review_status", ["pending", "verified", "excluded"]))
    op.create_table("bid_images", sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("task_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page", sa.Integer()), sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False), sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False))
    # 现有角色不自动授权；新装的普通用户同样不含 use.bid。


def downgrade():
    for table in ("bid_images", "bid_cells", "bid_items", "bid_requirements"):
        op.drop_table(table)
    for column in ("meta_enc", "password_enc", "bidder_id"):
        op.drop_column("task_files", column)
    op.drop_table("bid_bidders")
    op.drop_table("bid_projects")
    op.drop_constraint(op.f("ck_tasks_kind_valid"), "tasks", type_="check")
    op.create_check_constraint("kind_valid", "tasks", "kind IN ('review', 'assistant')")
