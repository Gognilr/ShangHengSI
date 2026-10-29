"""迁移：模型与迁移一致、任务删除级联，以及会在线上升级时改动已有数据的两个迁移。

需要一个可用的 PostgreSQL 16 实例：设置 TEST_DATABASE_URL（指向该实例上任意可连接的库，账号需有建库权限）。
每个测试会话新建一个独立的测试库，结束后删除，不触碰任何已有数据库。
未设置 TEST_DATABASE_URL 时整个文件跳过。
"""
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="未设置 TEST_DATABASE_URL")


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _url_for(dbname: str) -> str:
    """把 TEST_DATABASE_URL 中的库名换成 dbname。"""
    parts = urlsplit(ADMIN_URL)
    return urlunsplit(parts._replace(path="/" + dbname))


@pytest.fixture(scope="module")
def db_url():
    """新建独立的测试库，模块结束后删除。返回该库的连接串。"""
    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = _url_for(name)
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _alembic(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="module")
def migrated(db_url):
    command.upgrade(_alembic(db_url), "head")
    with psycopg.connect(db_url, autocommit=True) as conn:
        yield conn


# ---------------------------------------------------------------- 结构


# ---------------------------------------------------------------- CHECK 约束与数据行为


def _insert_user(conn) -> str:
    return conn.execute(
        "INSERT INTO users (username, display_name, password_hash, role, created_at, updated_at) "
        "VALUES (%s, 'x', 'h', 'user', now(), now()) RETURNING id",
        (f"u_{uuid.uuid4().hex[:8]}",),
    ).fetchone()[0]


def _insert_task(conn, owner) -> str:
    has_activity = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                                "AND table_name='tasks' AND column_name='last_activity_at'").fetchone()
    extra_col, extra_value = (", last_activity_at", ", now()") if has_activity else ("", "")
    return conn.execute(
        f"INSERT INTO tasks (owner_id, title, stance, source, policy, status, created_at, updated_at{extra_col}) "
        f"VALUES (%s, 't', 'sales', 'outgoing', 'ai', 'parsing', now(), now(){extra_value}) RETURNING id",
        (owner,),
    ).fetchone()[0]


def test_deleting_task_cascades_to_all_children(migrated):
    owner = _insert_user(migrated)
    task = _insert_task(migrated, owner)
    c = migrated
    file_id = c.execute(
        "INSERT INTO task_files (task_id, seq, is_main, original_name, ext, size_bytes, sha256, storage_key, created_at) "
        "VALUES (%s, 1, true, 'a.docx', 'docx', 1, %s, 'k', now()) RETURNING id",
        (task, "0" * 64),
    ).fetchone()[0]
    c.execute(
        "INSERT INTO doc_nodes (task_id, file_id, loc, ord, kind, part) VALUES (%s, %s, 'F1-P1', 1, 'paragraph', 'body')",
        (task, file_id),
    )
    c.execute("INSERT INTO parties (task_id, code, role_label) VALUES (%s, 'A', '买方')", (task,))
    c.execute("INSERT INTO redaction_maps (task_id, map_enc, updated_at) VALUES (%s, '\\x00', now())", (task,))
    c.execute(
        "INSERT INTO redaction_items (task_id, loc, char_start, char_end, category, placeholder, detected_by) "
        "VALUES (%s, 'F1-P1', 0, 1, 'ORG', 'x', 'party')",
        (task,),
    )
    c.execute(
        "INSERT INTO redaction_suspects (task_id, loc, char_start, char_end, reason) VALUES (%s, 'F1-P1', 0, 1, 'capitalized')",
        (task,),
    )
    c.execute(
        "INSERT INTO local_findings (task_id, check_code, severity, detail_enc, ai_summary, created_at) "
        "VALUES (%s, 'L-01', 'high', '\\x00', 's', now())",
        (task,),
    )
    run_id = c.execute(
        "INSERT INTO review_runs (task_id, run_no, status, stance, source, main_doc_type, created_by, queued_at) "
        "VALUES (%s, 1, 'queued', 'sales', 'outgoing', 'contract', %s, now()) RETURNING id",
        (task, owner),
    ).fetchone()[0]
    c.execute(
        "INSERT INTO requirement_results (run_id, req_id, req_source, req_text, verdict) VALUES (%s, 'G-01', 'builtin', 't', 'ok')",
        (run_id,),
    )
    c.execute(
        "INSERT INTO findings (run_id, task_id, seq, title, category, severity, cite_status, problem) "
        "VALUES (%s, %s, 1, 't', 'legal', 'high', 'verified', 'p')",
        (run_id, task),
    )
    c.execute(
        "INSERT INTO chat_messages (task_id, role, content_redacted, created_at) VALUES (%s, 'user', 'q', now())",
        (task,),
    )
    # llm_calls 不设外键，任务删除后记录保留
    c.execute(
        "INSERT INTO llm_calls (task_id, purpose, model_name, status, created_at) VALUES (%s, 'review', 'm', 'sent', now())",
        (task,),
    )

    children = [
        "task_files", "doc_nodes", "parties", "redaction_maps", "redaction_items", "redaction_suspects",
        "local_findings", "review_runs", "findings", "chat_messages",
    ]
    for t in children:
        assert c.execute(f"SELECT count(*) FROM {t} WHERE task_id = %s", (task,)).fetchone()[0] == 1, t

    c.execute("DELETE FROM tasks WHERE id = %s", (task,))

    for t in children:
        assert c.execute(f"SELECT count(*) FROM {t} WHERE task_id = %s", (task,)).fetchone()[0] == 0, t
    assert c.execute("SELECT count(*) FROM requirement_results WHERE run_id = %s", (run_id,)).fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM llm_calls WHERE task_id = %s", (task,)).fetchone()[0] == 1


# ---------------------------------------------------------------- 模型与迁移一致、回滚


def test_models_match_migration(migrated, db_url):
    """ORM 模型与迁移结果不得有差异（自动比对不应再产生任何变更）。"""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from app.db.base import Base
    from app.db.session import normalize_url

    engine = create_engine(normalize_url(db_url))
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


@pytest.mark.parametrize("legacy,original,tasks", [(False, None, False), (True, None, True),
                                                  (True, 30, True), (True, 45, True), (True, None, False)])
def test_v2c_0014_upgrade_safety_and_downgrade(legacy, original, tasks):
    """2.7 #11/12/14/16/17/18：users 判断旧库、原值保留、回填与降级。"""
    import json
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.backup.retention import run_retention
    from app.db.session import normalize_url

    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = _url_for(name)
    cfg = _alembic(url)
    try:
        command.upgrade(cfg, "0013")
        now = datetime.now(timezone.utc)
        ids = []
        with psycopg.connect(url, autocommit=True) as conn:
            owner = _insert_user(conn) if legacy else None
            if original is not None:
                conn.execute("INSERT INTO settings (key,value,updated_at) VALUES ('retention.original_days',%s,now())",
                             (json.dumps(original),))
            if tasks:
                for status, days in (("reviewed", 31), ("reviewed", 400), ("awaiting_confirm", 31)):
                    tid = _insert_task(conn, owner)
                    conn.execute("UPDATE tasks SET status=%s,last_completed_at=%s,updated_at=%s WHERE id=%s",
                                 (status, now-timedelta(days=days) if status == "reviewed" else None,
                                  now-timedelta(days=days), tid))
                    conn.execute("INSERT INTO task_files (task_id,seq,is_main,original_name,ext,size_bytes,sha256,storage_key,created_at) "
                                 "VALUES (%s,1,true,'虚构.docx','docx',1,repeat('0',64),%s,now())", (tid, uuid.uuid4().hex))
                    ids.append(tid)
            if legacy:
                conn.execute("INSERT INTO llm_calls (purpose,model_name,status,request_text,created_at) VALUES "
                             "('review','mock','sent','脱敏正文',%s)", (now-timedelta(days=200),))
                conn.execute("INSERT INTO audit_logs (action,created_at) VALUES ('task_delete',%s)",
                             (now-timedelta(days=400),))
        command.upgrade(cfg, "0014")
        with psycopg.connect(url, autocommit=True) as conn:
            values = dict(conn.execute("SELECT key,value FROM settings WHERE key LIKE 'retention.%'").fetchall())
            if not legacy:
                assert values == {}
            else:
                assert values == {"retention.original_days": original or 0, "retention.result_days": 0,
                                  "retention.outbound_days": 0, "retention.audit_days": 0,
                                  "retention.unfinished_days": 0, "retention.review_pending": True}
            if tasks:
                assert conn.execute("SELECT count(*) FROM tasks WHERE last_activity_at IS DISTINCT FROM "
                                    "coalesce(last_completed_at,updated_at,created_at)").fetchone()[0] == 0
        if legacy:
            # E-04：当前清理代码只在已完成迁移的库上运行；0014 的原断言仍在上方验证。
            command.upgrade(cfg, "head")
            engine = create_engine(normalize_url(url))
            try:
                with Session(engine) as db:
                    run_retention(db, now)
            finally:
                engine.dispose()
            with psycopg.connect(url, autocommit=True) as conn:
                assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == len(ids)
                assert conn.execute("SELECT request_text FROM llm_calls").fetchone()[0] == "脱敏正文"
                assert conn.execute("SELECT count(*) FROM audit_logs WHERE action='task_delete'").fetchone()[0] == 1
                deleted = conn.execute("SELECT count(*) FROM task_files WHERE original_deleted_at IS NOT NULL").fetchone()[0]
                assert deleted == (2 if original == 30 else 1 if original == 45 else 0)
            command.downgrade(cfg, "0014")
        command.downgrade(cfg, "0013")
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='tasks' "
                                "AND column_name='last_activity_at'").fetchone() is None
            values = dict(conn.execute("SELECT key,value FROM settings WHERE key LIKE 'retention.%'").fetchall())
            assert values == ({"retention.original_days": original or 0} if legacy else {})
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.mark.parametrize('conflict', [False, True])
def test_t42_11_builtin_laws_migration_preserves_existing_and_edits(conflict):
    from datetime import date
    expected = {
        '合同法': ('1999-03-15', '2021-01-01', '民法典'),
        '担保法': ('1995-06-30', '2021-01-01', '民法典'),
        '物权法': ('2007-03-16', '2021-01-01', '民法典'),
        '民法通则': ('2009-08-27', '2021-01-01', '民法典'),
        '民法总则': ('2017-03-15', '2021-01-01', '民法典'),
        '侵权责任法': ('2009-12-26', '2021-01-01', '民法典'),
        '中外合资经营企业法': ('2016-09-03', '2020-01-01', '外商投资法'),
        '中外合作经营企业法': ('2017-11-04', '2020-01-01', '外商投资法'),
        '外资企业法': ('2016-09-03', '2020-01-01', '外商投资法'),
        '经济合同法': (None, '1999-10-01', '民法典'),
        '涉外经济合同法': (None, '1999-10-01', '民法典'),
        '技术合同法': (None, '1999-10-01', '民法典'),
    }
    prefix = '中华人民共和国'
    name = 'aireview_test_'+uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url, cfg = _url_for(name), _alembic(_url_for(name))
    try:
        command.upgrade(cfg, '0017')
        with psycopg.connect(url, autocommit=True) as conn:
            if conflict:
                conn.execute("INSERT INTO laws(title,category,status,source,published_on,created_at,updated_at) "
                             "VALUES('中华人民共和国合同法','司法解释','valid','import','1999-03-15',now(),now())")
        command.upgrade(cfg, '0018')
        with psycopg.connect(url, autocommit=True) as conn:
            rows = conn.execute('SELECT title,published_on,repealed_on,replaced_by,category,status,source, '
                                'created_at=updated_at FROM laws ORDER BY id').fetchall()
            assert len(rows) == 12
            for title, published, repeal, replacement, category, status, source, equal in rows:
                if conflict and title == prefix+'合同法':
                    assert (category, status, source, repeal, replacement) == ('司法解释', 'valid', 'import', None, None)
                    continue
                p, r, by = expected[title.removeprefix(prefix)]
                assert (published, repeal, replacement) == (date.fromisoformat(p) if p else None,
                                                           date.fromisoformat(r), prefix+by)
                assert (category, status, source, equal) == ('法律', 'repealed', 'builtin', True)
            conn.execute("UPDATE laws SET updated_at=created_at+interval '1 second',replaced_by='示例新规定' "
                         "WHERE title='中华人民共和国担保法'")
            conn.execute("UPDATE laws SET source='import' WHERE title='中华人民共和国物权法'")
        command.downgrade(cfg, '0017')
        with psycopg.connect(url, autocommit=True) as conn:
            titles = {r[0] for r in conn.execute('SELECT title FROM laws')}
            assert titles == {prefix+'担保法', prefix+'物权法'} | ({prefix+'合同法'} if conflict else set())
        command.upgrade(cfg, '0018')
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute('SELECT count(*) FROM laws').fetchone()[0] == 12
            assert conn.execute("SELECT replaced_by FROM laws WHERE title='中华人民共和国担保法'").fetchone()[0] == '示例新规定'
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


