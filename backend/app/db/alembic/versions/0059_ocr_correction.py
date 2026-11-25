"""保留 PDF 扫描文字校订前的识别稿密文。"""
from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("doc_nodes", sa.Column("ocr_original_enc", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("doc_nodes", "ocr_original_enc")
