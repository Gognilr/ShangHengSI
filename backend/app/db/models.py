"""全部 23 张表的 ORM 模型，对应 SPEC 18.3–18.24 与 V2-B 任务内排除表。

约定见 SPEC 18.1：时间 timestamptz（UTC）；枚举用 varchar + CHECK；_enc 结尾为主密钥加密后的 bytea；
任务下属表的 task_id 外键均 ON DELETE CASCADE。
时间戳的默认值由应用在写入时提供（utcnow），数据库侧只有 SPEC 明确写出的默认值。
"""
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    CHAR,
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import TIMESTAMPTZ, Base, enum_check, utcnow, uuid_pk

# ---- 枚举取值（与 SPEC 对应章节一致） ----
STANCES = ["sales", "purchase", "other"]
SOURCES = ["outgoing", "incoming"]
DOC_TYPES = ["contract", "quotation", "order", "inquiry", "letter"]
SEVERITIES = ["high", "medium", "low"]
# 9.2 代码列
REDACTION_CATEGORIES = [
    "OUR", "ORG", "PERSON", "PHONE", "EMAIL", "URL", "ADDR", "POSTCODE", "ACCOUNT",
    "SWIFT", "IDNO", "REFNO", "AMOUNT", "PRICE", "QTY", "CUSTOM", "IM", "PROJECT",
]


def _ts_now() -> Any:
    return mapped_column(TIMESTAMPTZ, nullable=False, default=utcnow)


def _ts_updated() -> Any:
    return mapped_column(TIMESTAMPTZ, nullable=False, default=utcnow, onupdate=utcnow)


def _jsonb_list() -> Any:
    return mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


def _jsonb_all() -> Any:
    return mapped_column(JSONB, nullable=False, server_default=text("'[\"all\"]'::jsonb"))


# CompanyRequirement 有名为 text 的字段，会遮住 sqlalchemy.text，类体内改用这两个常量
_TRUE = text("true")
_ZERO = text("0")


def _task_fk() -> Any:
    return mapped_column(Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)


# ---- 18.3 users ----
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        enum_check("role", ["user", "admin"]),
        enum_check("status", ["active", "disabled"]),
        enum_check("last_stance", STANCES),
        enum_check("last_source", SOURCES),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    username: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="active")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    locked_until: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    last_stance: Mapped[Optional[str]] = mapped_column(String(10))
    last_source: Mapped[Optional[str]] = mapped_column(String(10))
    last_login_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_updated()


# ---- 18.4 sessions ----
class Session(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    csrf_token: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = _ts_now()
    last_seen_at: Mapped[datetime] = _ts_now()
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(INET)


# ---- 18.5 tasks ----
class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        enum_check("stance", STANCES),
        enum_check("source", SOURCES),
        enum_check("policy", ["ai", "local_only"]),
        enum_check(
            "status",
            ["parsing", "parse_failed", "awaiting_confirm", "reviewing", "reviewed", "completed_local"],
        ),
        Index("ix_tasks_owner_id_created_at", "owner_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    stance: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    policy: Mapped[str] = mapped_column(String(12), nullable=False)
    user_note_enc: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    user_note_redacted: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    # Q-04：latest_run_id 与 review_runs.task_id 构成循环外键，这里不加外键
    latest_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    high_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_updated()
    last_completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)


# ---- 18.6 task_files ----
class TaskFile(Base):
    __tablename__ = "task_files"
    __table_args__ = (
        UniqueConstraint("task_id", "seq"),
        enum_check("ext", ["docx", "pdf", "xlsx", "jpg", "png", "doc", "xls", "wps", "et", "rtf"]),
        enum_check("doc_type", DOC_TYPES),
        enum_check("doc_type_source", ["auto", "user"]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = _task_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    is_main: Mapped[bool] = mapped_column(Boolean, nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ext: Mapped[str] = mapped_column(String(10), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_type: Mapped[Optional[str]] = mapped_column(String(12))
    doc_type_source: Mapped[Optional[str]] = mapped_column(String(6))
    parser_version: Mapped[Optional[str]] = mapped_column(String(20))
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    has_revisions: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    warnings: Mapped[Any] = _jsonb_list()
    original_deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = _ts_now()


# ---- 18.7 doc_nodes ----
class DocNode(Base):
    __tablename__ = "doc_nodes"
    __table_args__ = (
        UniqueConstraint("task_id", "loc"),
        enum_check(
            "kind",
            ["heading", "paragraph", "list_item", "table_cell", "sheet_cell", "ocr_block"],
        ),
        enum_check("part", ["body", "header", "footer", "comment", "textbox", "hidden_sheet"]),
        Index("ix_doc_nodes_file_id_ord", "file_id", "ord"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[uuid.UUID] = _task_fk()
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("task_files.id", ondelete="CASCADE"), nullable=False
    )
    loc: Mapped[str] = mapped_column(String(64), nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    part: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_loc: Mapped[Optional[str]] = mapped_column(String(64))
    clause_no: Mapped[Optional[str]] = mapped_column(String(32))
    text_enc: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    redacted_text: Mapped[Optional[str]] = mapped_column(Text)
    page: Mapped[Optional[int]] = mapped_column(Integer)
    bbox: Mapped[Optional[Any]] = mapped_column(JSONB)
    sheet: Mapped[Optional[str]] = mapped_column(String(100))
    cell_ref: Mapped[Optional[str]] = mapped_column(String(16))
    ocr_conf: Mapped[Optional[float]] = mapped_column(REAL)
    has_revision: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


# ---- 18.8 parties ----
class Party(Base):
    __tablename__ = "parties"
    __table_args__ = (UniqueConstraint("task_id", "code"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = _task_fk()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    role_label: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(50))
    org_type: Mapped[Optional[str]] = mapped_column(String(20))
    is_our_party: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


# ---- 18.9 redaction_maps ----
class RedactionMap(Base):
    __tablename__ = "redaction_maps"

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    map_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = _ts_updated()


# ---- 18.10 redaction_items ----
class RedactionItem(Base):
    __tablename__ = "redaction_items"
    __table_args__ = (
        enum_check("category", REDACTION_CATEGORIES),
        enum_check(
            "detected_by",
            ["term", "party", "alias", "regex", "amount", "person", "address", "user", "derived", "ner"],
        ),
        Index("ix_redaction_items_task_id_loc", "task_id", "loc"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[uuid.UUID] = _task_fk()
    loc: Mapped[str] = mapped_column(String(64), nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    placeholder: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_by: Mapped[str] = mapped_column(String(10), nullable=False)


class RedactionExclusion(Base):
    """V2-B-05：任务内撤销的人名原文加密；loc 只记录来源（V2Q-35）。"""
    __tablename__ = "redaction_exclusions"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = _task_fk()
    loc: Mapped[str] = mapped_column(String(64), nullable=False)
    text_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts_now()


# ---- 18.11 redaction_suspects ----
class RedactionSuspect(Base):
    __tablename__ = "redaction_suspects"
    __table_args__ = (
        enum_check("reason", ["org_suffix", "capitalized", "long_digits", "before_company", "person_name", "derived_name", "person_like", "ner"]),
        enum_check("decision", ["pending", "replace", "ignore"]),
        enum_check("category", REDACTION_CATEGORIES),
        # decision=replace 时 category 必填
        CheckConstraint("decision <> 'replace' OR category IS NOT NULL", name="replace_needs_category"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    loc: Mapped[str] = mapped_column(String(64), nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    category: Mapped[Optional[str]] = mapped_column(String(10))
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    decided_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)


# ---- 18.12 redaction_terms ----
class RedactionTerm(Base):
    __tablename__ = "redaction_terms"
    __table_args__ = (
        UniqueConstraint("kind", "term_hash"),
        enum_check("kind", ["learned", "custom"]),
        enum_check("category", ["ORG", "PERSON", "CUSTOM"]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    term_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    term_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    is_our_party: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_hit_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts_now()


# ---- 18.13 local_findings ----
class LocalFinding(Base):
    __tablename__ = "local_findings"
    __table_args__ = (
        enum_check(
            "check_code",
            ["L-01", "L-02", "L-03", "L-04", "L-05", "L-06", "L-07", "L-08", "L-09", "L-10", "L-11", "L-INFO"],
        ),
        enum_check("severity", SEVERITIES),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    check_code: Mapped[str] = mapped_column(String(8), nullable=False)
    severity: Mapped[str] = mapped_column(String(6), nullable=False)
    locs: Mapped[Any] = _jsonb_list()
    detail_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ai_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts_now()


# ---- 18.14 review_runs ----
class ReviewRun(Base):
    __tablename__ = "review_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "run_no"),
        enum_check("status", ["queued", "running", "blocked", "completed", "partial", "failed"]),
        enum_check("stance", STANCES),
        enum_check("source", SOURCES),
        enum_check("main_doc_type", DOC_TYPES),
        enum_check("sanctions_level", ["none", "attention", "high"]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = _task_fk()
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    stance: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    user_note: Mapped[Optional[str]] = mapped_column(Text)  # 本轮补充说明的代号快照（R4）
    main_doc_type: Mapped[str] = mapped_column(String(12), nullable=False)
    model_name: Mapped[Optional[str]] = mapped_column(String(100))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(20))
    builtin_req_version: Mapped[Optional[str]] = mapped_column(String(20))
    company_req_version: Mapped[Optional[int]] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    failed_chunks: Mapped[Any] = _jsonb_list()
    coverage: Mapped[Optional[Any]] = mapped_column(JSONB)
    doc_summary: Mapped[Optional[str]] = mapped_column(Text)
    sanctions_level: Mapped[Optional[str]] = mapped_column(String(10))
    sanctions_reasons: Mapped[Any] = _jsonb_list()
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)


# ---- 18.15 requirement_results ----
class RequirementResult(Base):
    __tablename__ = "requirement_results"
    __table_args__ = (
        UniqueConstraint("run_id", "req_id"),
        enum_check("req_source", ["builtin", "company", "note"]),
        enum_check("verdict", ["issue", "ok", "na", "unable"]),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    req_id: Mapped[str] = mapped_column(String(16), nullable=False)
    req_source: Mapped[str] = mapped_column(String(8), nullable=False)
    req_text: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[Optional[str]] = mapped_column(String(16))
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)


# ---- 18.16 findings ----
class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        enum_check("category", ["wording", "logic", "legal", "compliance", "commercial"]),
        enum_check("severity", SEVERITIES),
        enum_check("cite_status", ["verified", "unverified", "missing"]),
        enum_check("handling_status", ["pending", "adopted", "ignored"]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    # 冗余字段，用于权限校验
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(12), nullable=False)
    severity: Mapped[str] = mapped_column(String(6), nullable=False)
    req_ids: Mapped[Any] = _jsonb_list()
    locs: Mapped[Any] = _jsonb_list()
    missing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    cite_status: Mapped[str] = mapped_column(String(10), nullable=False)
    quote_model: Mapped[Optional[str]] = mapped_column(Text)
    quote_snapshot_enc: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[Optional[str]] = mapped_column(Text)
    suggestion: Mapped[Optional[str]] = mapped_column(Text)
    suggested_text: Mapped[Optional[str]] = mapped_column(Text)
    basis: Mapped[Optional[str]] = mapped_column(Text)
    handling_status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    handling_note: Mapped[Optional[str]] = mapped_column(String(500))
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)


# ---- 18.17 chat_messages ----
class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        enum_check("role", ["user", "assistant"]),
        Index("ix_chat_messages_task_id_id", "task_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[uuid.UUID] = _task_fk()
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = _ts_now()


# ---- 18.18 llm_calls ----
class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        enum_check("purpose", ["review", "chat", "test"]),
        enum_check("status", ["sent", "blocked", "error"]),
        Index("ix_llm_calls_created_at", text("created_at DESC")),
        Index("ix_llm_calls_user_id_created_at", "user_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("users.id"))
    # 不设外键：任务删除后保留记录
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    task_title: Mapped[Optional[str]] = mapped_column(String(255))
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    request_text: Mapped[Optional[str]] = mapped_column(Text)
    response_text: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    block_reason: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = _ts_now()


# ---- 18.19 jobs ----
class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        enum_check("type", ["parse", "review", "backup", "retention"]),
        enum_check("status", ["queued", "running", "done", "failed"]),
        Index("ix_jobs_status_run_after", "status", "run_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(12), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    run_after: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=text("now()"))
    locked_by: Mapped[Optional[str]] = mapped_column(String(64))
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts_now()
    finished_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)


# ---- 18.20 settings ----
class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = _ts_updated()


# ---- 18.21 company_requirements ----
# CO 编号序列（Q-107，迁移 0002）：删除后编号不复用。不挂在 metadata 上，由迁移创建。
company_requirement_code_seq = Sequence("company_requirement_code_seq")


class CompanyRequirement(Base):
    __tablename__ = "company_requirements"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    doc_types: Mapped[Any] = _jsonb_all()
    stances: Mapped[Any] = _jsonb_all()
    sources: Mapped[Any] = _jsonb_all()
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=_TRUE)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=_ZERO)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_updated()


# ---- 18.22 requirement_versions ----
class RequirementVersion(Base):
    __tablename__ = "requirement_versions"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[datetime] = _ts_now()


# ---- 18.23 backup_records ----
class BackupRecord(Base):
    __tablename__ = "backup_records"
    __table_args__ = (
        enum_check("trigger", ["schedule", "manual"]),
        enum_check("status", ["running", "success", "failed"]),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    file_name: Mapped[Optional[str]] = mapped_column(String(100))
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    sha256: Mapped[Optional[str]] = mapped_column(CHAR(64))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)


# ---- 18.24 audit_logs ----
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(32))
    target_id: Mapped[Optional[str]] = mapped_column(String(64))
    detail: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    ip: Mapped[Optional[str]] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=utcnow, index=True)
