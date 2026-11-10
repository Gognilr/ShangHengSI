"""问题处理备注改为主密钥加密存储（V31Q-114）。已有备注在迁移中加密，原明文列删除。"""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("handling_note_enc", sa.LargeBinary(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, handling_note FROM findings WHERE handling_note IS NOT NULL")).all()
    if rows:
        from app.security import crypto
        for fid, note in rows:
            conn.execute(sa.text("UPDATE findings SET handling_note_enc = :enc WHERE id = :id"),
                         {"enc": crypto.encrypt_text(note), "id": fid})
    op.drop_column("findings", "handling_note")


def downgrade() -> None:
    op.add_column("findings", sa.Column("handling_note", sa.String(500), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, handling_note_enc FROM findings WHERE handling_note_enc IS NOT NULL")).all()
    if rows:
        from app.security import crypto
        for fid, blob in rows:
            conn.execute(sa.text("UPDATE findings SET handling_note = :note WHERE id = :id"),
                         {"note": crypto.decrypt_text(bytes(blob))[:500], "id": fid})
    op.drop_column("findings", "handling_note_enc")
