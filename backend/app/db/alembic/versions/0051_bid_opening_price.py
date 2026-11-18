"""唱标价（V34-B-06e）：投标人加加密字段存小写、大写与备注；B-09 存在项目结论里的唱标价一并迁过来。"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bid_bidders", sa.Column("opening_price_enc", sa.LargeBinary()))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT task_id, conclusion_enc FROM bid_projects WHERE conclusion_enc IS NOT NULL")).all()
    if not rows:
        return
    from app.security import crypto
    for task_id, blob in rows:
        state = json.loads(crypto.decrypt_text(bytes(blob)))
        totals = state.pop("manual_totals", None)
        if totals is None:
            continue
        for bid, amount in totals.items():
            value = json.dumps({"amount": amount, "upper": None, "note": None}, ensure_ascii=False)
            conn.execute(sa.text("UPDATE bid_bidders SET opening_price_enc = :enc WHERE id = :id AND task_id = :task"),
                         {"enc": crypto.encrypt_text(value), "id": bid, "task": task_id})
        conn.execute(sa.text("UPDATE bid_projects SET conclusion_enc = :enc WHERE task_id = :task"),
                     {"enc": crypto.encrypt_text(json.dumps(state, ensure_ascii=False, sort_keys=True)), "task": task_id})


def downgrade():
    op.drop_column("bid_bidders", "opening_price_enc")
