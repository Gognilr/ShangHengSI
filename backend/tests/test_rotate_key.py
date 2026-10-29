"""V2-C-07：虚构数据的主密钥轮换、续做、抽查和安全输出。"""
from pathlib import Path

import pytest
from sqlalchemy import Column, LargeBinary, MetaData, Table, Integer, select, update

from app import cli
from app.config import get_settings
from app.db.base import Base
from app.db.models import Setting, Task, RedactionTerm, RedactionExclusion, NerNodeResult, LocalFinding, ReviewRun, Finding
from app.security import crypto
from tests.review_support import env, ready_task  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def rotation(db_engine, db_sessionmaker, make_user, ready_task, tmp_path):
    owner = make_user("rotate_owner")
    task = ready_task(owner, user_note="虚构轮换补充说明")
    with db_sessionmaker() as db:
        db.add(RedactionTerm(kind="custom", category="CUSTOM", term_hash="b"*64,
                             term_enc=crypto.encrypt_text("虚构敏感词")))
        db.add(RedactionExclusion(task_id=task.id,loc="NOTE",category="PERSON",text_enc=crypto.encrypt_text("虚构人名")))
        db.add(NerNodeResult(task_id=task.id,loc="VIRTUAL",data_enc=crypto.encrypt_text('{"virtual":true}')))
        db.add(LocalFinding(task_id=task.id,check_code="L-INFO",severity="low",detail_enc=crypto.encrypt_text("虚构本地发现"),ai_summary="虚构摘要"))
        run = ReviewRun(task_id=task.id,run_no=1,status="completed",stance="sales",source="outgoing",main_doc_type="contract",created_by=owner.id)
        db.add(run)
        db.flush()
        db.add(Finding(task_id=task.id,run_id=run.id,seq=1,title="虚构问题",category="legal",severity="low",
                       cite_status="verified",problem="虚构描述",quote_snapshot_enc=crypto.encrypt_text("虚构引用")))
        db.commit()
    old = crypto.get_master_key()
    new = b"N" * 32
    old_file, new_file = tmp_path / "old", tmp_path / "new"
    old_file.write_bytes(old)
    new_file.write_bytes(new)
    new_file.chmod(0o600)
    files = Path(get_settings().files_dir)
    before = {}
    # 给所有加密列所在的现有行留基线；额外表覆盖所有元数据列及跨批次。
    with db_engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            columns = [c for c in table.c if c.name.endswith("_enc")]
            for row in conn.execute(select(table)).mappings() if columns else []:
                pk = tuple(row[c.name] for c in table.primary_key)
                for col in columns:
                    if row[col.name] is not None:
                        before[(table.name, pk, col.name)] = crypto.decrypt(row[col.name], key=old)
    originals = {p.name: crypto.decrypt_file(p, key=old) for p in files.iterdir()}
    with db_engine.connect() as conn:
        business = {table.name: [dict(row) for row in conn.execute(select(table).order_by(*table.primary_key)).mappings()]
                    for table in Base.metadata.sorted_tables}
    return dict(engine=db_engine, sm=db_sessionmaker, task=task, old=old, new=new,
                old_file=old_file, new_file=new_file, files=files, before=before, originals=originals, business=business)


def assert_rotated(data):
    with data["engine"].connect() as conn:
        for (table_name, pk, name), expected in data["before"].items():
            table = Base.metadata.tables[table_name]
            where = [col == value for col, value in zip(table.primary_key, pk)]
            blob = conn.execute(select(table.c[name]).where(*where)).scalar_one()
            assert crypto.decrypt(blob, key=data["new"]) == expected
            with pytest.raises(crypto.DecryptionError):
                crypto.decrypt(blob, key=data["old"])
        value = conn.execute(select(Setting.value).where(Setting.key == "model.api_key_enc")).scalar_one()
        assert crypto.decrypt_from_b64(value, key=data["new"]) == "sk-test-0123456789"
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt_from_b64(value, key=data["old"])
        for table in Base.metadata.sorted_tables:
            after = [dict(row) for row in conn.execute(select(table).order_by(*table.primary_key)).mappings()]
            before = data["business"][table.name]
            assert len(after) == len(before)
            for previous, current in zip(before, after):
                for name in previous:
                    if not name.endswith("_enc") and not (table.name == "settings" and previous["key"] == "model.api_key_enc" and name == "value"):
                        assert current[name] == previous[name], (table.name, name)
    for name, expected in data["originals"].items():
        assert crypto.decrypt_file(data["files"] / name, key=data["new"]) == expected
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt_file(data["files"] / name, key=data["old"])


def test_v2c07_rotate_all_and_verify(rotation, capsys, caplog):
    from app.security import rotate
    d = rotation
    rotate.rotate_key(d["engine"], d["files"], d["old"], d["new"])
    assert_rotated(d)
    rotate.verify_key(d["engine"], d["files"], d["new"])
    with pytest.raises(rotate.RotationError):
        rotate.verify_key(d["engine"], d["files"], d["old"])
    # 新密钥可解的密文完全跳过，重复执行不再生成新 nonce。
    saved = {p.name: p.read_bytes() for p in d["files"].iterdir()}
    rotate.rotate_key(d["engine"], d["files"], d["old"], d["new"])
    assert saved == {p.name: p.read_bytes() for p in d["files"].iterdir()}
    captured = capsys.readouterr()
    output = captured.out + captured.err + caplog.text
    assert "虚构轮换补充说明" not in output and "sk-test-0123456789" not in output
    assert repr(d["old"]) not in output and repr(d["new"]) not in output


def test_v2c07_resume_committed_batches(rotation, monkeypatch):
    from app.security import rotate
    d = rotation
    calls = []
    def stop_after_second_batch():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("虚构中断")
    monkeypatch.setattr(rotate, "_after_batch", stop_after_second_batch)
    with pytest.raises(RuntimeError, match="虚构中断"):
        rotate.rotate_key(d["engine"], d["files"], d["old"], d["new"], batch_size=1)
    monkeypatch.setattr(rotate, "_after_batch", lambda: None)
    rotate.rotate_key(d["engine"], d["files"], d["old"], d["new"], batch_size=1)
    assert_rotated(d)


def test_v2c07_metadata_includes_future_columns(rotation):
    from app.security import rotate
    expected = {"doc_nodes.text_enc", "redaction_maps.map_enc", "redaction_terms.term_enc",
                "local_findings.detail_enc", "findings.quote_snapshot_enc", "tasks.user_note_enc",
                "redaction_exclusions.text_enc"}
    expected |= {'standard_templates.name_enc','review_runs.template_name_snapshot_enc','law_import_files.data_enc'}
    expected |= {'standard_clauses.'+name+'_enc' for name in ('title','text','stance','acceptable','escalate')}
    assert expected <= {f"{col.table.name}.{col.name}" for col in rotate.encrypted_columns()}
    # 使用真正的数据库扩展表验证发现、批量处理和抽查，而非固定列名单。
    metadata = MetaData()
    future = Table("rotation_future", metadata, Column("id", Integer, primary_key=True),
                   Column("extra_enc", LargeBinary))
    d = rotation
    future.create(d["engine"])
    Base.metadata._add_table(future.name, None, future)
    try:
        with d["engine"].begin() as conn:
            conn.execute(future.insert(), [{"id": i, "extra_enc": crypto.encrypt(b"fictional-future", key=d["old"])} for i in range(1, 503)])
        assert future.c.extra_enc in rotate.encrypted_columns()
        rotate.rotate_key(d["engine"], d["files"], d["old"], d["new"])
        with d["engine"].connect() as conn:
            for blob in conn.execute(select(future.c.extra_enc)).scalars():
                assert crypto.decrypt(blob, key=d["new"]) == b"fictional-future"
        # 每列最多 20 条，序号 21 的损坏不在抽查样本；全量轮换仍须发现。
        with d["engine"].begin() as conn:
            conn.execute(update(future).where(future.c.id == 21).values(extra_enc=b"outside-sample"))
        rotate.verify_key(d["engine"], d["files"], d["new"])
        with pytest.raises(rotate.RotationError, match="rotation_future.*21"):
            rotate.rotate_key(d["engine"], d["files"], d["old"], d["new"])
    finally:
        Base.metadata.remove(future)
        future.drop(d["engine"])


# V2Q-75（C-09 审核修订）：只处理按存储规则命名的原件，其他条目不读不改；不合格原件在改库前报错。


