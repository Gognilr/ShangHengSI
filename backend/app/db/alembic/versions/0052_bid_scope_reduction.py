"""V35-E-01 定位收缩：撤下关键参数、Y/N 核实与评委签认、反馈记忆、二次核验与第五十一条默认行。

数据迁移：
* 旧的“已排除”（条目、单元格与结论事项）并入项目结论的 dismissed（“不是问题”）；“不适用”的要求改为停用；
  核实、签认、二次核验与备注不再保留。
* 关键参数中工期、交货期、质保期、缺陷责任期与类似业绩的取值，写到对应 ★ / 否决条款的参数上；
  其余关键参数删除，只留报价方式。
* 第五十一条默认行及其判断删除。
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

_GROUPS = ["qualification", "substantive", "veto", "pricing", "validity", "signing", "general", "format"]
_OLD_GROUPS = [g for g in _GROUPS if g != "general"]
_TERMS = ("construction_max", "delivery_max", "warranty_min", "defect_min")
_PERFORMANCE = ("performance_count", "performance_amount", "performance_from", "performance_until")


def _group_check(values):
    op.drop_constraint(op.f("ck_bid_requirements_group_valid"), "bid_requirements", type_="check")
    op.create_check_constraint(op.f("ck_bid_requirements_group_valid"), "bid_requirements",
                               '"group" IN (' + ", ".join(repr(v) for v in values) + ")")


def _migrate_data(conn):
    from app.bid.compliance import requirement_kind
    from app.security import crypto

    def load(blob):
        return json.loads(crypto.decrypt_text(bytes(blob))) if blob is not None else {}

    def save(value):
        return crypto.encrypt_text(json.dumps(value, ensure_ascii=False, sort_keys=True))

    projects = conn.execute(sa.text("SELECT task_id, parameters_enc, conclusion_enc FROM bid_projects")).all()
    for task_id, params_blob, conclusion_blob in projects:
        conclusion = load(conclusion_blob)
        dismissed = {key for key, review in conclusion.get("reviews", {}).items() if review.get("status") == "excluded"}
        dismissed |= {f"item:{i}" for (i,) in conn.execute(sa.text(
            "SELECT id FROM bid_items WHERE task_id = :t AND review_status = 'excluded'"), {"t": task_id})}
        dismissed |= {f"cell:{r}:{b}" for r, b in conn.execute(sa.text(
            "SELECT requirement_id, bidder_id FROM bid_cells WHERE task_id = :t AND review_status = 'excluded'"), {"t": task_id})}
        for rid in conclusion.get("not_applicable", []):
            conn.execute(sa.text("UPDATE bid_requirements SET enabled = false WHERE id = :id AND task_id = :t"), {"id": rid, "t": task_id})
        conn.execute(sa.text("UPDATE bid_projects SET conclusion_enc = :enc WHERE task_id = :t"),
                     {"enc": save({"dismissed": sorted(dismissed)}) if dismissed else None, "t": task_id})

        if params_blob is None:
            continue
        state = load(params_blob)
        values = state.pop("values", None) or {}
        state.pop("sources", None)
        state["quote_mode"] = values.get("quote_mode") or "fixed"
        conn.execute(sa.text("UPDATE bid_projects SET parameters_enc = :enc WHERE task_id = :t"), {"enc": save(state), "t": task_id})
        rows = conn.execute(sa.text("SELECT id, text_enc FROM bid_requirements WHERE task_id = :t AND \"group\" IN ('veto', 'substantive')"),
                            {"t": task_id}).all()
        for rid, text_blob in rows:
            kind = requirement_kind(crypto.decrypt_text(bytes(text_blob)))
            if kind in _TERMS and values.get(kind):
                param = {kind: values[kind]}
            elif kind == "performance" and values.get("performance_count"):
                param = {key: values.get(key) for key in _PERFORMANCE}
            else:
                continue
            conn.execute(sa.text("UPDATE bid_requirements SET param_enc = :enc, method = 'local' WHERE id = :id"),
                         {"enc": crypto.encrypt_text(json.dumps(param, ensure_ascii=False)), "id": rid})

    defaults = "SELECT id FROM bid_requirements WHERE source = 'default'"
    conn.execute(sa.text(f"DELETE FROM bid_items WHERE requirement_id IN ({defaults})"))
    conn.execute(sa.text("DELETE FROM bid_requirements WHERE source = 'default'"))


def upgrade():
    op.add_column("bid_requirements", sa.Column("param_enc", sa.LargeBinary()))
    _group_check(_GROUPS)
    _migrate_data(op.get_bind())
    for table in ("bid_items", "bid_cells"):
        op.drop_constraint(op.f(f"ck_{table}_review_status_valid"), table, type_="check")
        op.drop_column(table, "review_status")
        op.drop_column(table, "note_enc")
    op.drop_column("users", "bid_feedback_enc")


def downgrade():
    op.add_column("users", sa.Column("bid_feedback_enc", sa.LargeBinary()))
    for table in ("bid_items", "bid_cells"):
        op.add_column(table, sa.Column("review_status", sa.String(10), nullable=False, server_default="pending"))
        op.add_column(table, sa.Column("note_enc", sa.LargeBinary()))
        op.create_check_constraint(op.f(f"ck_{table}_review_status_valid"), table,
                                   "review_status IN ('pending', 'verified', 'excluded')")
    op.execute("UPDATE bid_requirements SET \"group\" = 'substantive' WHERE \"group\" = 'general'")
    _group_check(_OLD_GROUPS)
    op.drop_column("bid_requirements", "param_enc")
