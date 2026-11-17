"""V34-B-01a：标书项目的个人文件夹（最多三级）与全公司统一的分类。"""
import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

DEFAULTS = ["工程类", "采购类", "服务类", "其他"]


def upgrade():
    categories = op.create_table(
        "bid_categories", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(20), nullable=False, unique=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.bulk_insert(categories, [{"name": n, "seq": i + 1} for i, n in enumerate(DEFAULTS)])
    op.create_table(
        "bid_folders", sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("bid_folders.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Index("ix_bid_folders_owner_id", "owner_id"))
    op.add_column("bid_projects", sa.Column("folder_id", sa.Uuid(), sa.ForeignKey("bid_folders.id", ondelete="SET NULL")))
    op.add_column("bid_projects", sa.Column("category_id", sa.Integer(), sa.ForeignKey("bid_categories.id")))
    # 已有项目归入“其他”；内置业务管理员角色增加“标书分类”维护权限
    op.execute("UPDATE bid_projects SET category_id = (SELECT id FROM bid_categories WHERE name = '其他')")
    op.execute("UPDATE roles SET permissions = permissions || '[\"content.bid_categories\"]'::jsonb "
               "WHERE builtin = 'biz_admin' AND NOT permissions ? 'content.bid_categories'")


def downgrade():
    op.execute("UPDATE roles SET permissions = permissions - 'content.bid_categories'")
    op.drop_column("bid_projects", "category_id")
    op.drop_column("bid_projects", "folder_id")
    op.drop_table("bid_folders")
    op.drop_table("bid_categories")
