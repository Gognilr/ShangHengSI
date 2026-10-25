"""初始迁移与 SPEC 第 18 节的一致性测试。

需要一个可用的 PostgreSQL 16 实例：设置 TEST_DATABASE_URL（指向该实例上任意可连接的库，账号需有建库权限）。
每个测试会话新建一个独立的测试库，结束后删除，不触碰任何已有数据库。
未设置 TEST_DATABASE_URL 时整个文件跳过。
"""
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import errors

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="未设置 TEST_DATABASE_URL")

BACKEND_DIR = Path(__file__).resolve().parents[1]

TYPE_ALIASES = {
    "uuid": "uuid",
    "text": "text",
    "int": "integer",
    "bigint": "bigint",
    "bool": "boolean",
    "jsonb": "jsonb",
    "bytea": "bytea",
    "inet": "inet",
    "real": "real",
    "ts": "timestamp with time zone",
}


def _url_for(dbname: str) -> str:
    """把 TEST_DATABASE_URL 中的库名换成 dbname。"""
    parts = urlsplit(ADMIN_URL)
    return urlunsplit(parts._replace(path="/" + dbname))


def _type(token: str) -> str:
    if token in TYPE_ALIASES:
        return TYPE_ALIASES[token]
    m = re.fullmatch(r"([vc])(\d+)", token)  # v50 = varchar(50)，c64 = char(64)
    assert m, token
    return ("character varying" if m.group(1) == "v" else "character") + f"({m.group(2)})"


# 每行：字段 类型 [NN] [=默认值]。NN 表示非空；默认值不含类型转换后缀；
# =serial 表示 serial / bigserial 序列默认值。没写 = 的字段不得有数据库默认值。
SCHEMA = {
    "users": """
        id uuid NN =gen_random_uuid()
        username v50 NN
        display_name v50 NN
        password_hash text NN
        role v10 NN
        status v10 NN ='active'
        must_change_password bool NN =false
        failed_login_count int NN =0
        locked_until ts
        last_stance v10
        last_source v10
        last_login_at ts
        created_at ts NN
        updated_at ts NN""",
    "sessions": """
        token_hash c64 NN
        user_id uuid NN
        csrf_token c64 NN
        created_at ts NN
        last_seen_at ts NN
        expires_at ts NN
        ip inet""",
    "tasks": """
        id uuid NN =gen_random_uuid()
        owner_id uuid NN
        title v255 NN
        stance v10 NN
        source v10 NN
        policy v12 NN
        user_note_enc bytea
        user_note_redacted text
        status v20 NN
        error_message text
        latest_run_id uuid
        high_count int NN =0
        created_at ts NN
        updated_at ts NN
        last_completed_at ts""",
    "task_files": """
        id uuid NN =gen_random_uuid()
        task_id uuid NN
        seq int NN
        is_main bool NN
        original_name v255 NN
        ext v10 NN
        size_bytes bigint NN
        sha256 c64 NN
        storage_key v64 NN
        doc_type v12
        doc_type_source v6
        parser_version v20
        page_count int
        has_revisions bool NN =false
        warnings jsonb NN ='[]'
        original_deleted_at ts
        created_at ts NN""",
    "doc_nodes": """
        id bigint NN =serial
        task_id uuid NN
        file_id uuid NN
        loc v64 NN
        ord int NN
        kind v16 NN
        part v16 NN
        parent_loc v64
        clause_no v32
        text_enc bytea
        redacted_text text
        page int
        bbox jsonb
        sheet v100
        cell_ref v16
        ocr_conf real
        has_revision bool NN =false""",
    "parties": """
        id uuid NN =gen_random_uuid()
        task_id uuid NN
        code v32 NN
        role_label v32 NN
        country v50
        org_type v20
        is_our_party bool NN =false
        confirmed bool NN =false""",
    "redaction_maps": """
        task_id uuid NN
        map_enc bytea NN
        updated_at ts NN""",
    "redaction_items": """
        id bigint NN =serial
        task_id uuid NN
        loc v64 NN
        char_start int NN
        char_end int NN
        category v10 NN
        placeholder v64 NN
        detected_by v10 NN""",
    "redaction_suspects": """
        id bigint NN =serial
        task_id uuid NN
        loc v64 NN
        char_start int NN
        char_end int NN
        reason v32 NN
        decision v10 NN ='pending'
        category v10
        decided_by uuid
        decided_at ts""",
    "redaction_terms": """
        id uuid NN =gen_random_uuid()
        kind v8 NN
        term_enc bytea NN
        term_hash c64 NN
        category v10 NN
        is_our_party bool NN =false
        enabled bool NN =true
        hit_count int NN =0
        last_hit_at ts
        created_by uuid
        created_at ts NN""",
    "local_findings": """
        id bigint NN =serial
        task_id uuid NN
        file_id uuid
        check_code v8 NN
        severity v6 NN
        locs jsonb NN ='[]'
        detail_enc bytea NN
        ai_summary text NN
        created_at ts NN""",
    "review_runs": """
        id uuid NN =gen_random_uuid()
        task_id uuid NN
        run_no int NN
        status v10 NN
        stance v10 NN
        source v10 NN
        user_note text
        main_doc_type v12 NN
        model_name v100
        prompt_version v20
        builtin_req_version v20
        company_req_version int
        chunk_count int NN =1
        failed_chunks jsonb NN ='[]'
        coverage jsonb
        doc_summary text
        sanctions_level v10
        sanctions_reasons jsonb NN ='[]'
        tokens_in int NN =0
        tokens_out int NN =0
        error_message text
        created_by uuid NN
        queued_at ts NN
        started_at ts
        finished_at ts""",
    "requirement_results": """
        id bigint NN =serial
        run_id uuid NN
        req_id v16 NN
        req_source v8 NN
        req_text text NN
        dimension v16
        verdict v8 NN
        note text""",
    "findings": """
        id uuid NN =gen_random_uuid()
        run_id uuid NN
        task_id uuid NN
        seq int NN
        title v200 NN
        category v12 NN
        severity v6 NN
        req_ids jsonb NN ='[]'
        locs jsonb NN ='[]'
        missing bool NN =false
        cite_status v10 NN
        quote_model text
        quote_snapshot_enc bytea
        problem text NN
        impact text
        suggestion text
        suggested_text text
        basis text
        handling_status v10 NN ='pending'
        handling_note v500
        updated_by uuid
        updated_at ts""",
    "chat_messages": """
        id bigint NN =serial
        task_id uuid NN
        role v10 NN
        content_redacted text NN
        tokens_in int
        tokens_out int
        created_at ts NN""",
    "llm_calls": """
        id bigint NN =serial
        user_id uuid
        task_id uuid
        task_title v255
        run_id uuid
        purpose v8 NN
        model_name v100 NN
        request_text text
        response_text text
        status v8 NN
        block_reason text
        error_message text
        tokens_in int NN =0
        tokens_out int NN =0
        duration_ms int
        created_at ts NN""",
    "jobs": """
        id bigint NN =serial
        type v12 NN
        payload jsonb NN
        status v8 NN ='queued'
        attempts int NN =0
        max_attempts int NN =3
        run_after ts NN =now()
        locked_by v64
        heartbeat_at ts
        last_error text
        created_at ts NN
        finished_at ts""",
    "settings": """
        key v64 NN
        value jsonb NN
        updated_by uuid
        updated_at ts NN""",
    "company_requirements": """
        id uuid NN =gen_random_uuid()
        code v8 NN
        text v300 NN
        doc_types jsonb NN ='["all"]'
        stances jsonb NN ='["all"]'
        sources jsonb NN ='["all"]'
        enabled bool NN =true
        sort int NN =0
        created_by uuid
        created_at ts NN
        updated_at ts NN""",
    "requirement_versions": """
        version int NN =serial
        snapshot jsonb NN
        created_by uuid
        created_at ts NN""",
    "backup_records": """
        id bigint NN =serial
        trigger v8 NN
        status v8 NN
        file_name v100
        size_bytes bigint
        sha256 c64
        error_message text
        started_at ts NN
        finished_at ts""",
    "audit_logs": """
        id bigint NN =serial
        user_id uuid
        action v32 NN
        target_type v32
        target_id v64
        detail jsonb NN ='{}'
        ip inet
        created_at ts NN""",
}

SCHEMA["redaction_exclusions"] = """
    id uuid NN =gen_random_uuid()
    task_id uuid NN
    loc v64 NN
    text_enc bytea NN
    category v10 NN
    created_by uuid
    created_at ts NN"""

SCHEMA['ner_node_results'] = """
    id uuid NN =gen_random_uuid()
    task_id uuid NN
    loc v64 NN
    data_enc bytea NN
    updated_at ts NN"""

PRIMARY_KEYS = {t: ["id"] for t in SCHEMA}
PRIMARY_KEYS.update(
    sessions=["token_hash"], redaction_maps=["task_id"], settings=["key"], requirement_versions=["version"]
)

# 唯一约束 / 唯一索引（不含主键）
UNIQUES = {
    ('ner_node_results', 'task_id, loc'),
    ("users", "username"),
    ("task_files", "task_id, seq"),
    ("doc_nodes", "task_id, loc"),
    ("parties", "task_id, code"),
    ("redaction_terms", "kind, term_hash"),
    ("review_runs", "task_id, run_no"),
    ("requirement_results", "run_id, req_id"),
    ("findings", "run_id, seq"),
    ("company_requirements", "code"),
}

# 普通索引
INDEXES = {
    ("sessions", "user_id"),
    ("tasks", "owner_id, created_at DESC"),
    ("doc_nodes", "file_id, ord"),
    ("redaction_items", "task_id, loc"),
    ("redaction_suspects", "task_id"),
    ("local_findings", "task_id"),
    ("findings", "task_id"),
    ("chat_messages", "task_id, id"),
    ("llm_calls", "created_at DESC"),
    ("llm_calls", "user_id, created_at DESC"),
    ("jobs", "status, run_after"),
    ("audit_logs", "created_at"),
}

# 外键：(表, 字段) -> (被引用表, 删除规则 c=CASCADE a=NO ACTION)
FOREIGN_KEYS = {
    ('ner_node_results', 'task_id'): ('tasks', 'c'),
    ("redaction_exclusions", "task_id"): ("tasks", "c"),
    ("redaction_exclusions", "created_by"): ("users", "a"),
    ("sessions", "user_id"): ("users", "a"),
    ("tasks", "owner_id"): ("users", "a"),
    ("task_files", "task_id"): ("tasks", "c"),
    ("doc_nodes", "task_id"): ("tasks", "c"),
    ("doc_nodes", "file_id"): ("task_files", "c"),
    ("parties", "task_id"): ("tasks", "c"),
    ("redaction_maps", "task_id"): ("tasks", "c"),
    ("redaction_items", "task_id"): ("tasks", "c"),
    ("redaction_suspects", "task_id"): ("tasks", "c"),
    ("redaction_terms", "created_by"): ("users", "a"),
    ("local_findings", "task_id"): ("tasks", "c"),
    ("review_runs", "task_id"): ("tasks", "c"),
    ("review_runs", "created_by"): ("users", "a"),
    ("requirement_results", "run_id"): ("review_runs", "c"),
    ("findings", "run_id"): ("review_runs", "c"),
    ("findings", "task_id"): ("tasks", "c"),
    ("chat_messages", "task_id"): ("tasks", "c"),
    ("llm_calls", "user_id"): ("users", "a"),
}

# 枚举字段：(表, 字段) -> 合法取值（抽取代表性字段验证 CHECK 约束确实存在并生效）
ENUM_COLUMNS = {
    ("users", "role"): ["user", "admin"],
    ("users", "status"): ["active", "disabled"],
    ("users", "last_stance"): ["sales", "purchase", "other"],
    ("users", "last_source"): ["outgoing", "incoming"],
    ("review_runs", "stance"): ["sales", "purchase", "other"],
    ("review_runs", "source"): ["outgoing", "incoming"],
    ("review_runs", "main_doc_type"): ["contract", "quotation", "order", "inquiry", "letter"],
    ("redaction_suspects", "category"): [
        "OUR", "ORG", "PERSON", "PHONE", "EMAIL", "URL", "ADDR", "POSTCODE", "ACCOUNT",
        "SWIFT", "IDNO", "REFNO", "AMOUNT", "PRICE", "QTY", "CUSTOM", "IM", "PROJECT",
    ],
    ("tasks", "stance"): ["sales", "purchase", "other"],
    ("tasks", "source"): ["outgoing", "incoming"],
    ("tasks", "policy"): ["ai", "local_only"],
    ("tasks", "status"): [
        "parsing", "parse_failed", "awaiting_confirm", "reviewing", "reviewed", "completed_local",
    ],
    ("task_files", "ext"): ["docx", "pdf", "xlsx", "jpg", "png", "doc", "xls", "wps", "et", "rtf"],
    ("task_files", "doc_type"): ["contract", "quotation", "order", "inquiry", "letter"],
    ("task_files", "doc_type_source"): ["auto", "user"],
    ("doc_nodes", "kind"): ["heading", "paragraph", "list_item", "table_cell", "sheet_cell", "ocr_block"],
    ("doc_nodes", "part"): ["body", "header", "footer", "comment", "textbox", "hidden_sheet"],
    ("redaction_items", "category"): [
        "OUR", "ORG", "PERSON", "PHONE", "EMAIL", "URL", "ADDR", "POSTCODE", "ACCOUNT",
        "SWIFT", "IDNO", "REFNO", "AMOUNT", "PRICE", "QTY", "CUSTOM", "IM", "PROJECT",
    ],
    ("redaction_items", "detected_by"): [
        "term", "party", "alias", "regex", "amount", "person", "address", "user", "derived", "ner",
    ],
    ("redaction_suspects", "reason"): ["org_suffix", "capitalized", "long_digits", "before_company", "person_name", "derived_name", "person_like", "ner"],
    ("redaction_suspects", "decision"): ["pending", "replace", "ignore"],
    ("redaction_terms", "kind"): ["learned", "custom"],
    ("redaction_terms", "category"): ["ORG", "PERSON", "CUSTOM"],
    ("local_findings", "check_code"): [
        "L-01", "L-02", "L-03", "L-04", "L-05", "L-06", "L-07", "L-08", "L-09", "L-10", "L-11", "L-INFO",
    ],
    ("local_findings", "severity"): ["high", "medium", "low"],
    ("review_runs", "status"): ["queued", "running", "blocked", "completed", "partial", "failed"],
    ("review_runs", "sanctions_level"): ["none", "attention", "high"],
    ("requirement_results", "req_source"): ["builtin", "company", "note"],
    ("requirement_results", "verdict"): ["issue", "ok", "na", "unable"],
    ("findings", "category"): ["wording", "logic", "legal", "compliance", "commercial"],
    ("findings", "severity"): ["high", "medium", "low"],
    ("findings", "cite_status"): ["verified", "unverified", "missing"],
    ("findings", "handling_status"): ["pending", "adopted", "ignored"],
    ("chat_messages", "role"): ["user", "assistant"],
    ("llm_calls", "purpose"): ["review", "chat", "test"],
    ("llm_calls", "status"): ["sent", "blocked", "error"],
    ("jobs", "type"): ["parse", "review", "backup", "retention"],
    ("jobs", "status"): ["queued", "running", "done", "failed"],
    ("backup_records", "trigger"): ["schedule", "manual"],
    ("backup_records", "status"): ["running", "success", "failed"],
}


def _parse_schema(spec: str):
    cols = {}
    for line in spec.strip().splitlines():
        parts = line.split()
        name, typ, rest = parts[0], _type(parts[1]), parts[2:]
        default = next((p[1:] for p in rest if p.startswith("=")), None)
        cols[name] = (typ, "NN" in rest, default)
    return cols


# ---------------------------------------------------------------- 夹具


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


def _tables(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    ).fetchall()
    return {r[0] for r in rows} - {"alembic_version"}


# ---------------------------------------------------------------- 结构


def test_exactly_23_tables(migrated):
    assert _tables(migrated) == set(SCHEMA)
    assert len(SCHEMA) == 24  # V2Q-49：新增加密节点结果表，仍严格核对完整表集合。


def test_pgcrypto_enabled(migrated):
    row = migrated.execute("SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'").fetchone()
    assert row is not None


@pytest.mark.parametrize("table", sorted(SCHEMA))
def test_columns_match_spec(migrated, table):
    rows = migrated.execute(
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
        """,
        (f"public.{table}",),
    ).fetchall()
    actual = {r[0]: (r[1], r[2], r[3]) for r in rows}
    expected = _parse_schema(SCHEMA[table])
    assert set(actual) == set(expected), f"{table} 字段不一致"
    for name, (typ, notnull, default) in expected.items():
        a_typ, a_notnull, a_default = actual[name]
        assert a_typ == typ, f"{table}.{name} 类型"
        # 主键隐含非空
        assert a_notnull == (notnull or name in PRIMARY_KEYS[table]), f"{table}.{name} 非空"
        if default is None:
            assert a_default is None, f"{table}.{name} 不应有默认值"
        elif default == "serial":
            assert a_default and a_default.startswith("nextval("), f"{table}.{name} 序列"
        else:
            assert a_default is not None, f"{table}.{name} 缺默认值"
            assert a_default.split("::")[0] == default, f"{table}.{name} 默认值"


def test_primary_keys(migrated):
    rows = migrated.execute(
        """
        SELECT c.conrelid::regclass::text, array_agg(a.attname ORDER BY k.ord)
        FROM pg_constraint c
        CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.contype = 'p' AND c.connamespace = 'public'::regnamespace
        GROUP BY c.conrelid
        """
    ).fetchall()
    actual = dict(rows)
    actual.pop("alembic_version")
    assert actual == PRIMARY_KEYS


def _index_rows(conn):
    rows = conn.execute(
        """
        SELECT i.indrelid::regclass::text, i.indisunique,
               regexp_replace(pg_get_indexdef(i.indexrelid), '^.*\\((.*)\\)$', '\\1')
        FROM pg_index i
        WHERE i.indrelid::regclass::text = ANY(%s) AND NOT i.indisprimary
        """,
        (sorted(SCHEMA),),
    ).fetchall()
    return rows


def test_unique_constraints_and_indexes(migrated):
    rows = _index_rows(migrated)
    assert {(t, c) for t, u, c in rows if u} == UNIQUES
    assert {(t, c) for t, u, c in rows if not u} == INDEXES


def test_foreign_keys_and_cascade_rules(migrated):
    rows = migrated.execute(
        """
        SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text, c.confdeltype
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
        """
    ).fetchall()
    assert {(t, col): (ref, rule) for t, col, ref, rule in rows} == FOREIGN_KEYS
    # Q-04：tasks.latest_run_id 不加外键；llm_calls.task_id / run_id 不设外键（任务删除后保留记录）
    assert ("tasks", "latest_run_id") not in {(r[0], r[1]) for r in rows}
    assert ("llm_calls", "task_id") not in {(r[0], r[1]) for r in rows}


# ---------------------------------------------------------------- CHECK 约束与数据行为


def _insert_user(conn) -> str:
    return conn.execute(
        "INSERT INTO users (username, display_name, password_hash, role, created_at, updated_at) "
        "VALUES (%s, 'x', 'h', 'user', now(), now()) RETURNING id",
        (f"u_{uuid.uuid4().hex[:8]}",),
    ).fetchone()[0]


def _insert_task(conn, owner) -> str:
    return conn.execute(
        "INSERT INTO tasks (owner_id, title, stance, source, policy, status, created_at, updated_at) "
        "VALUES (%s, 't', 'sales', 'outgoing', 'ai', 'parsing', now(), now()) RETURNING id",
        (owner,),
    ).fetchone()[0]


def test_enum_check_constraints_exist_and_reject(migrated):
    """每个枚举字段都有 CHECK 约束，且约束内容恰为 SPEC 取值（用约束定义文本核对）。"""
    rows = migrated.execute(
        """
        SELECT c.conrelid::regclass::text, pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        WHERE c.contype = 'c' AND c.connamespace = 'public'::regnamespace
        """
    ).fetchall()
    defs = {}
    for table, d in rows:
        m = re.match(r"CHECK \(\(?\(?(\w+)\)?(?:::text)? = ANY \(\(?ARRAY\[(.*)\]\)?(?:::text\[\])?\)\)\)?", d)
        if m:
            defs[(table, m.group(1))] = set(re.findall(r"'([^']+)'", m.group(2)))
    for key, values in ENUM_COLUMNS.items():
        assert key in defs, f"{key} 缺少 CHECK 约束"
        assert defs[key] == set(values), f"{key} 取值与 SPEC 不一致"
    # 约束数量 = 枚举字段数 + 1（疑似遗漏的 replace 必填 category）
    assert len(rows) == len(ENUM_COLUMNS) + 1


def test_check_rejects_invalid_values(migrated):
    owner = _insert_user(migrated)
    with pytest.raises(errors.CheckViolation):
        migrated.execute(
            "INSERT INTO tasks (owner_id, title, stance, source, policy, status, created_at, updated_at) "
            "VALUES (%s, 't', 'sales', 'outgoing', 'ai', 'bogus', now(), now())",
            (owner,),
        )
    with pytest.raises(errors.CheckViolation):
        migrated.execute(
            "INSERT INTO users (username, display_name, password_hash, role, created_at, updated_at) "
            "VALUES ('bad_role', 'x', 'h', 'root', now(), now())"
        )


def test_suspect_replace_requires_category(migrated):
    task = _insert_task(migrated, _insert_user(migrated))
    base = "INSERT INTO redaction_suspects (task_id, loc, char_start, char_end, reason, decision, category) "
    with pytest.raises(errors.CheckViolation):
        migrated.execute(base + "VALUES (%s, 'F1-P1', 0, 3, 'capitalized', 'replace', NULL)", (task,))
    migrated.execute(base + "VALUES (%s, 'F1-P1', 0, 3, 'capitalized', 'replace', 'ORG')", (task,))
    migrated.execute(base + "VALUES (%s, 'F1-P2', 0, 3, 'capitalized', 'pending', NULL)", (task,))


def test_unique_violations(migrated):
    owner = _insert_user(migrated)
    task = _insert_task(migrated, owner)
    ins = (
        "INSERT INTO parties (task_id, code, role_label) VALUES (%s, '客户_A', '买方')"
    )
    migrated.execute(ins, (task,))
    with pytest.raises(errors.UniqueViolation):
        migrated.execute(ins, (task,))


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


def test_defaults_work_on_insert(migrated):
    row = migrated.execute(
        "INSERT INTO jobs (type, payload, created_at) VALUES ('parse', '{}', now()) "
        "RETURNING id, status, attempts, max_attempts, run_after IS NOT NULL"
    ).fetchone()
    assert row[1:] == ("queued", 0, 3, True)
    v = migrated.execute(
        "INSERT INTO requirement_versions (snapshot, created_at) VALUES ('{}', now()) RETURNING version"
    ).fetchone()[0]
    assert isinstance(v, int)


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


def test_0007_check_codes_upgrade_and_downgrade():
    name = 'aireview_test_' + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, '0006')
        sql = ("INSERT INTO local_findings (task_id, check_code, severity, detail_enc, ai_summary, created_at) "
               "VALUES (%s, %s, 'high', %s, 's', now())")
        with psycopg.connect(url, autocommit=True) as conn:
            task = _insert_task(conn, _insert_user(conn))
            with pytest.raises(errors.CheckViolation):
                conn.execute(sql, (task, 'L-09', b'x'))
        command.upgrade(cfg, '0007')
        with psycopg.connect(url, autocommit=True) as conn:
            for code in ('L-01', 'L-09', 'L-10', 'L-11'):
                conn.execute(sql, (task, code, b'x'))
        command.downgrade(cfg, '0006')
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute('SELECT check_code FROM local_findings').fetchall() == [('L-01',)]
            for code in ('L-09', 'L-10', 'L-11'):
                with pytest.raises(errors.CheckViolation):
                    conn.execute(sql, (task, code, b'x'))
        command.upgrade(cfg, 'head')
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_0008_requirement_sources_upgrade_and_downgrade():
    name = 'aireview_test_' + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, '0007')
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO company_requirements (code, text, created_at, updated_at) VALUES ('CO-1', '旧要求', now(), now())")
        command.upgrade(cfg, '0008')
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute('SELECT sources FROM company_requirements').fetchone()[0] == ['all']
            conn.execute("UPDATE company_requirements SET text = %s, sources = '[\"incoming\"]'", ('字' * 300,))
        command.downgrade(cfg, '0007')
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute('SELECT text FROM company_requirements').fetchone()[0] == '字' * 200
            assert conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='company_requirements' AND column_name='sources'").fetchone()[0] == 0
        command.upgrade(cfg, 'head')
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_0011_exclusions_upgrade_downgrade_and_cascade():
    """B-05 #6：新表字段、任务级联与降级删除表，旧表数据保留。"""
    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, "0010")
        with psycopg.connect(url, autocommit=True) as conn:
            assert "redaction_exclusions" not in _tables(conn)
            owner = _insert_user(conn)
            task = _insert_task(conn, owner)
        command.upgrade(cfg, "0011")
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO redaction_exclusions (task_id, loc, text_enc, category, created_by, created_at) VALUES (%s, 'NOTE', %s, 'PERSON', %s, now())", (task, b'ciphertext', owner))
            assert conn.execute("SELECT loc, text_enc, category, created_by FROM redaction_exclusions").fetchone() == ("NOTE", b'ciphertext', "PERSON", owner)
            conn.execute("DELETE FROM tasks WHERE id=%s", (task,))
            assert conn.execute("SELECT count(*) FROM redaction_exclusions").fetchone()[0] == 0
        command.downgrade(cfg, "0010")
        with psycopg.connect(url, autocommit=True) as conn:
            assert "redaction_exclusions" not in _tables(conn)
            assert conn.execute("SELECT count(*) FROM users WHERE id=%s", (owner,)).fetchone()[0] == 1
        command.upgrade(cfg, "head")
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_0010_suspect_reasons_upgrade_and_downgrade():
    """B-03 #10；V2Q-31 顺延：新原因 / 来源可写，降级删除新行并恢复约束。"""
    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    item = "INSERT INTO redaction_items (task_id, loc, char_start, char_end, category, placeholder, detected_by) VALUES (%s, 'F1-P1', 0, 3, 'ORG', 'x', %s)"
    suspect = "INSERT INTO redaction_suspects (task_id, loc, char_start, char_end, reason) VALUES (%s, 'F1-P1', 0, 3, %s)"
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, "0009")
        with psycopg.connect(url, autocommit=True) as conn:
            task = _insert_task(conn, _insert_user(conn))
            for sql, value in [(item, "derived"), (suspect, "derived_name"), (suspect, "person_like")]:
                with pytest.raises(errors.CheckViolation):
                    conn.execute(sql, (task, value))
        command.upgrade(cfg, "0010")
        with psycopg.connect(url, autocommit=True) as conn:
            for sql, value in [(item, "derived"), (item, "party"), (suspect, "derived_name"), (suspect, "person_like"), (suspect, "person_name")]:
                conn.execute(sql, (task, value))
        command.downgrade(cfg, "0009")
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute("SELECT detected_by FROM redaction_items").fetchall() == [("party",)]
            assert conn.execute("SELECT reason FROM redaction_suspects").fetchall() == [("person_name",)]
            for sql, value in [(item, "derived"), (suspect, "derived_name"), (suspect, "person_like")]:
                with pytest.raises(errors.CheckViolation):
                    conn.execute(sql, (task, value))
        command.upgrade(cfg, "head")
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_0009_im_project_categories_upgrade_and_downgrade():
    """V2-B-01 1.5 #8；V2Q-31：保留现有 0008，类别迁移顺延 0009。"""
    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    item = ("INSERT INTO redaction_items (task_id, loc, char_start, char_end, category, placeholder, detected_by) "
            "VALUES (%s, 'F1-P1', 0, 6, %s, 'x', 'regex')")
    suspect = ("INSERT INTO redaction_suspects (task_id, loc, char_start, char_end, reason, decision, category) "
               "VALUES (%s, 'F1-P2', 0, 6, 'long_digits', 'replace', %s)")
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, "0008")
        with psycopg.connect(url, autocommit=True) as conn:
            task = _insert_task(conn, _insert_user(conn))
            for sql in (item, suspect):
                for category in ("IM", "PROJECT"):
                    with pytest.raises(errors.CheckViolation):
                        conn.execute(sql, (task, category))
        command.upgrade(cfg, "0009")
        with psycopg.connect(url, autocommit=True) as conn:
            for sql in (item, suspect):
                for category in ("IM", "PROJECT", "PHONE"):
                    conn.execute(sql, (task, category))
        command.downgrade(cfg, "0008")
        with psycopg.connect(url, autocommit=True) as conn:
            for table in ("redaction_items", "redaction_suspects"):
                assert conn.execute(f"SELECT category FROM {table}").fetchall() == [("PHONE",)]
            for sql in (item, suspect):
                for category in ("IM", "PROJECT"):
                    with pytest.raises(errors.CheckViolation):
                        conn.execute(sql, (task, category))
        command.upgrade(cfg, "head")
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_0003_postcode_category_upgrade_and_downgrade(db_url):
    """R1-11：0003 放宽 redaction_items、redaction_suspects 的类别约束；降级时删除 POSTCODE 行后恢复原约束。"""
    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    item = ("INSERT INTO redaction_items (task_id, loc, char_start, char_end, category, placeholder, detected_by) "
            "VALUES (%s, 'F1-P1', 0, 6, %s, '[邮编]', 'regex')")
    suspect = ("INSERT INTO redaction_suspects (task_id, loc, char_start, char_end, reason, decision, category) "
               "VALUES (%s, 'F1-P2', 0, 6, 'long_digits', 'replace', %s)")
    counts = ("SELECT (SELECT count(*) FROM redaction_items WHERE task_id = %s), "
              "(SELECT count(*) FROM redaction_suspects WHERE task_id = %s)")
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, "0002")
        with psycopg.connect(url, autocommit=True) as conn:
            task = _insert_task(conn, _insert_user(conn))
            with pytest.raises(errors.CheckViolation):
                conn.execute(item, (task, "POSTCODE"))
        command.upgrade(cfg, "0003")
        with psycopg.connect(url, autocommit=True) as conn:
            for category in ("POSTCODE", "PHONE"):
                conn.execute(item, (task, category))
                conn.execute(suspect, (task, category))
            assert conn.execute(counts, (task, task)).fetchone() == (2, 2)
        command.downgrade(cfg, "0002")
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute(counts, (task, task)).fetchone() == (1, 1)  # 只删 POSTCODE 行
            with pytest.raises(errors.CheckViolation):
                conn.execute(suspect, (task, "POSTCODE"))
        command.upgrade(cfg, "head")
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')



def test_0004_legacy_ext_upgrade_and_downgrade(db_url):
    """R2-02：0004 放宽 task_files.ext；有旧格式文件时拒绝降级，删除后可降级并恢复原约束。"""
    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    insert = ("INSERT INTO task_files (task_id, seq, is_main, original_name, ext, size_bytes, sha256, storage_key, created_at) "
              "VALUES (%s, %s, true, 'x', %s, 1, repeat('0', 64), %s, now())")
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, "0003")
        with psycopg.connect(url, autocommit=True) as conn:
            task = _insert_task(conn, _insert_user(conn))
            with pytest.raises(errors.CheckViolation):
                conn.execute(insert, (task, 1, "doc", uuid.uuid4().hex))
        command.upgrade(cfg, "0004")
        with psycopg.connect(url, autocommit=True) as conn:
            for seq, ext in enumerate(["docx", "doc", "xls", "wps", "et", "rtf"], start=1):
                conn.execute(insert, (task, seq, ext, uuid.uuid4().hex))
            for ext in ("docm", "xlsm", "dot", "DOC"):
                with pytest.raises(errors.CheckViolation):
                    conn.execute(insert, (task, 99, ext, uuid.uuid4().hex))
        with pytest.raises(RuntimeError, match="5 个旧格式文件"):
            command.downgrade(cfg, "0003")
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute("SELECT count(*) FROM task_files").fetchone()[0] == 6  # 拒绝降级时不删除任何数据
            conn.execute("DELETE FROM task_files WHERE ext <> 'docx'")
        command.downgrade(cfg, "0003")
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute("SELECT count(*) FROM task_files").fetchone()[0] == 1
            with pytest.raises(errors.CheckViolation):
                conn.execute(insert, (task, 2, "rtf", uuid.uuid4().hex))
        command.upgrade(cfg, "head")
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_downgrade_leaves_empty_database(db_url):
    """独立库：升级后回滚，应无任何业务表和 pgcrypto 扩展。"""
    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        with psycopg.connect(url, autocommit=True) as conn:
            assert _tables(conn) == set()
            assert conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'").fetchone() is None
            sequences = conn.execute(
                "SELECT count(*) FROM pg_class WHERE relkind = 'S' AND relnamespace = 'public'::regnamespace"
            ).fetchone()[0]
            assert sequences == 0
        command.upgrade(cfg, "head")  # 回滚后可再次升级
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_0012_ner_upgrade_and_downgrade():
    """B-06 #6：来源/原因的真实数据库约束，降级只删除ner行。"""
    name = 'aireview_test_' + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    item = "INSERT INTO redaction_items (task_id,loc,char_start,char_end,category,placeholder,detected_by) VALUES (%s,'F1-P1',0,3,'PERSON','人员_1',%s)"
    suspect = "INSERT INTO redaction_suspects (task_id,loc,char_start,char_end,reason) VALUES (%s,'NOTE',0,3,%s)"
    try:
        url = _url_for(name)
        cfg = _alembic(url)
        command.upgrade(cfg, '0011')
        with psycopg.connect(url, autocommit=True) as conn:
            task = _insert_task(conn, _insert_user(conn))
            for sql in (item, suspect):
                with pytest.raises(errors.CheckViolation):
                    conn.execute(sql, (task, 'ner'))
        command.upgrade(cfg, '0012')
        with psycopg.connect(url, autocommit=True) as conn:
            for sql, value in ((item, 'ner'), (item, 'person'), (suspect, 'ner'), (suspect, 'person_name')):
                conn.execute(sql, (task, value))
        command.downgrade(cfg, '0011')
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute('SELECT detected_by FROM redaction_items').fetchall() == [('person',)]
            assert conn.execute('SELECT reason FROM redaction_suspects').fetchall() == [('person_name',)]
            for sql in (item, suspect):
                with pytest.raises(errors.CheckViolation):
                    conn.execute(sql, (task, 'ner'))
        command.upgrade(cfg, 'head')
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
