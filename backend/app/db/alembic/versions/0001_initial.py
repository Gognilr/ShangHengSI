"""初始结构：SPEC 第 18 节全部 22 张表

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() 来自 pgcrypto（SPEC 18.1）
    op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('target_type', sa.String(length=32), nullable=True),
    sa.Column('target_id', sa.String(length=64), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_table('backup_records',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('trigger', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('file_name', sa.String(length=100), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('sha256', sa.CHAR(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('running', 'success', 'failed')", name=op.f('ck_backup_records_status_valid')),
    sa.CheckConstraint("trigger IN ('schedule', 'manual')", name=op.f('ck_backup_records_trigger_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backup_records'))
    )
    op.create_table('company_requirements',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('code', sa.String(length=8), nullable=False),
    sa.Column('text', sa.String(length=200), nullable=False),
    sa.Column('doc_types', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text('\'["all"]\'::jsonb'), nullable=False),
    sa.Column('stances', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text('\'["all"]\'::jsonb'), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('sort', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_company_requirements')),
    sa.UniqueConstraint('code', name=op.f('uq_company_requirements_code'))
    )
    op.create_table('jobs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(length=12), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=8), server_default='queued', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('max_attempts', sa.Integer(), server_default=sa.text('3'), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('locked_by', sa.String(length=64), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name=op.f('ck_jobs_status_valid')),
    sa.CheckConstraint("type IN ('parse', 'review', 'backup', 'retention')", name=op.f('ck_jobs_type_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index('ix_jobs_status_run_after', 'jobs', ['status', 'run_after'], unique=False)
    op.create_table('requirement_versions',
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('version', name=op.f('pk_requirement_versions'))
    )
    op.create_table('settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_settings'))
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('display_name', sa.String(length=50), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='active', nullable=False),
    sa.Column('must_change_password', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('failed_login_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_stance', sa.String(length=10), nullable=True),
    sa.Column('last_source', sa.String(length=10), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("last_source IN ('outgoing', 'incoming')", name=op.f('ck_users_last_source_valid')),
    sa.CheckConstraint("last_stance IN ('sales', 'purchase', 'other')", name=op.f('ck_users_last_stance_valid')),
    sa.CheckConstraint("role IN ('user', 'admin')", name=op.f('ck_users_role_valid')),
    sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f('ck_users_status_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('username', name=op.f('uq_users_username'))
    )
    op.create_table('llm_calls',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('task_id', sa.Uuid(), nullable=True),
    sa.Column('task_title', sa.String(length=255), nullable=True),
    sa.Column('run_id', sa.Uuid(), nullable=True),
    sa.Column('purpose', sa.String(length=8), nullable=False),
    sa.Column('model_name', sa.String(length=100), nullable=False),
    sa.Column('request_text', sa.Text(), nullable=True),
    sa.Column('response_text', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('block_reason', sa.Text(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('tokens_in', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('tokens_out', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("purpose IN ('review', 'chat', 'test')", name=op.f('ck_llm_calls_purpose_valid')),
    sa.CheckConstraint("status IN ('sent', 'blocked', 'error')", name=op.f('ck_llm_calls_status_valid')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_llm_calls_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_calls'))
    )
    op.create_index('ix_llm_calls_created_at', 'llm_calls', [sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_llm_calls_user_id_created_at', 'llm_calls', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('redaction_terms',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('term_enc', sa.LargeBinary(), nullable=False),
    sa.Column('term_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('category', sa.String(length=10), nullable=False),
    sa.Column('is_our_party', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('hit_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_hit_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("category IN ('ORG', 'PERSON', 'CUSTOM')", name=op.f('ck_redaction_terms_category_valid')),
    sa.CheckConstraint("kind IN ('learned', 'custom')", name=op.f('ck_redaction_terms_kind_valid')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_redaction_terms_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_redaction_terms')),
    sa.UniqueConstraint('kind', 'term_hash', name=op.f('uq_redaction_terms_kind_term_hash'))
    )
    op.create_table('sessions',
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('csrf_token', sa.CHAR(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_sessions'))
    )
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('tasks',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('stance', sa.String(length=10), nullable=False),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('policy', sa.String(length=12), nullable=False),
    sa.Column('user_note', sa.String(length=500), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('latest_run_id', sa.Uuid(), nullable=True),
    sa.Column('high_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("policy IN ('ai', 'local_only')", name=op.f('ck_tasks_policy_valid')),
    sa.CheckConstraint("source IN ('outgoing', 'incoming')", name=op.f('ck_tasks_source_valid')),
    sa.CheckConstraint("stance IN ('sales', 'purchase', 'other')", name=op.f('ck_tasks_stance_valid')),
    sa.CheckConstraint("status IN ('parsing', 'parse_failed', 'awaiting_confirm', 'reviewing', 'reviewed', 'completed_local')", name=op.f('ck_tasks_status_valid')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_tasks_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tasks'))
    )
    op.create_index('ix_tasks_owner_id_created_at', 'tasks', ['owner_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('chat_messages',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.Column('content_redacted', sa.Text(), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=True),
    sa.Column('tokens_out', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f('ck_chat_messages_role_valid')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_chat_messages_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_messages'))
    )
    op.create_index('ix_chat_messages_task_id_id', 'chat_messages', ['task_id', 'id'], unique=False)
    op.create_table('local_findings',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('file_id', sa.Uuid(), nullable=True),
    sa.Column('check_code', sa.String(length=8), nullable=False),
    sa.Column('severity', sa.String(length=6), nullable=False),
    sa.Column('locs', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('detail_enc', sa.LargeBinary(), nullable=False),
    sa.Column('ai_summary', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("check_code IN ('L-01', 'L-02', 'L-03', 'L-04', 'L-05', 'L-06', 'L-07', 'L-08', 'L-INFO')", name=op.f('ck_local_findings_check_code_valid')),
    sa.CheckConstraint("severity IN ('high', 'medium', 'low')", name=op.f('ck_local_findings_severity_valid')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_local_findings_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_local_findings'))
    )
    op.create_index(op.f('ix_local_findings_task_id'), 'local_findings', ['task_id'], unique=False)
    op.create_table('parties',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('role_label', sa.String(length=32), nullable=False),
    sa.Column('country', sa.String(length=50), nullable=True),
    sa.Column('org_type', sa.String(length=20), nullable=True),
    sa.Column('is_our_party', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('confirmed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_parties_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parties')),
    sa.UniqueConstraint('task_id', 'code', name=op.f('uq_parties_task_id_code'))
    )
    op.create_table('redaction_items',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('loc', sa.String(length=64), nullable=False),
    sa.Column('char_start', sa.Integer(), nullable=False),
    sa.Column('char_end', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=10), nullable=False),
    sa.Column('placeholder', sa.String(length=64), nullable=False),
    sa.Column('detected_by', sa.String(length=10), nullable=False),
    sa.CheckConstraint("category IN ('OUR', 'ORG', 'PERSON', 'PHONE', 'EMAIL', 'URL', 'ADDR', 'ACCOUNT', 'SWIFT', 'IDNO', 'REFNO', 'AMOUNT', 'PRICE', 'QTY', 'CUSTOM')", name=op.f('ck_redaction_items_category_valid')),
    sa.CheckConstraint("detected_by IN ('term', 'party', 'alias', 'regex', 'amount', 'person', 'address', 'user')", name=op.f('ck_redaction_items_detected_by_valid')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_redaction_items_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_redaction_items'))
    )
    op.create_index('ix_redaction_items_task_id_loc', 'redaction_items', ['task_id', 'loc'], unique=False)
    op.create_table('redaction_maps',
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('map_enc', sa.LargeBinary(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_redaction_maps_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('task_id', name=op.f('pk_redaction_maps'))
    )
    op.create_table('redaction_suspects',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('loc', sa.String(length=64), nullable=False),
    sa.Column('char_start', sa.Integer(), nullable=False),
    sa.Column('char_end', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=32), nullable=False),
    sa.Column('decision', sa.String(length=10), server_default='pending', nullable=False),
    sa.Column('category', sa.String(length=10), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("category IN ('OUR', 'ORG', 'PERSON', 'PHONE', 'EMAIL', 'URL', 'ADDR', 'ACCOUNT', 'SWIFT', 'IDNO', 'REFNO', 'AMOUNT', 'PRICE', 'QTY', 'CUSTOM')", name=op.f('ck_redaction_suspects_category_valid')),
    sa.CheckConstraint("decision <> 'replace' OR category IS NOT NULL", name=op.f('ck_redaction_suspects_replace_needs_category')),
    sa.CheckConstraint("decision IN ('pending', 'replace', 'ignore')", name=op.f('ck_redaction_suspects_decision_valid')),
    sa.CheckConstraint("reason IN ('org_suffix', 'capitalized', 'long_digits', 'before_company')", name=op.f('ck_redaction_suspects_reason_valid')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_redaction_suspects_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_redaction_suspects'))
    )
    op.create_index(op.f('ix_redaction_suspects_task_id'), 'redaction_suspects', ['task_id'], unique=False)
    op.create_table('review_runs',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('run_no', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('stance', sa.String(length=10), nullable=False),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('user_note', sa.String(length=500), nullable=True),
    sa.Column('main_doc_type', sa.String(length=12), nullable=False),
    sa.Column('model_name', sa.String(length=100), nullable=True),
    sa.Column('prompt_version', sa.String(length=20), nullable=True),
    sa.Column('builtin_req_version', sa.String(length=20), nullable=True),
    sa.Column('company_req_version', sa.Integer(), nullable=True),
    sa.Column('chunk_count', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('failed_chunks', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('coverage', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('doc_summary', sa.Text(), nullable=True),
    sa.Column('sanctions_level', sa.String(length=10), nullable=True),
    sa.Column('sanctions_reasons', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('tokens_in', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('tokens_out', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("main_doc_type IN ('contract', 'quotation', 'order', 'inquiry', 'letter')", name=op.f('ck_review_runs_main_doc_type_valid')),
    sa.CheckConstraint("sanctions_level IN ('none', 'attention', 'high')", name=op.f('ck_review_runs_sanctions_level_valid')),
    sa.CheckConstraint("source IN ('outgoing', 'incoming')", name=op.f('ck_review_runs_source_valid')),
    sa.CheckConstraint("stance IN ('sales', 'purchase', 'other')", name=op.f('ck_review_runs_stance_valid')),
    sa.CheckConstraint("status IN ('queued', 'running', 'blocked', 'completed', 'partial', 'failed')", name=op.f('ck_review_runs_status_valid')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_review_runs_created_by_users')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_review_runs_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_runs')),
    sa.UniqueConstraint('task_id', 'run_no', name=op.f('uq_review_runs_task_id_run_no'))
    )
    op.create_table('task_files',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('is_main', sa.Boolean(), nullable=False),
    sa.Column('original_name', sa.String(length=255), nullable=False),
    sa.Column('ext', sa.String(length=10), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.CHAR(length=64), nullable=False),
    sa.Column('storage_key', sa.String(length=64), nullable=False),
    sa.Column('doc_type', sa.String(length=12), nullable=True),
    sa.Column('doc_type_source', sa.String(length=6), nullable=True),
    sa.Column('parser_version', sa.String(length=20), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('has_revisions', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('original_deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("doc_type IN ('contract', 'quotation', 'order', 'inquiry', 'letter')", name=op.f('ck_task_files_doc_type_valid')),
    sa.CheckConstraint("doc_type_source IN ('auto', 'user')", name=op.f('ck_task_files_doc_type_source_valid')),
    sa.CheckConstraint("ext IN ('docx', 'pdf', 'xlsx', 'jpg', 'png')", name=op.f('ck_task_files_ext_valid')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_files_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_task_files')),
    sa.UniqueConstraint('task_id', 'seq', name=op.f('uq_task_files_task_id_seq'))
    )
    op.create_table('doc_nodes',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('file_id', sa.Uuid(), nullable=False),
    sa.Column('loc', sa.String(length=64), nullable=False),
    sa.Column('ord', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('part', sa.String(length=16), nullable=False),
    sa.Column('parent_loc', sa.String(length=64), nullable=True),
    sa.Column('clause_no', sa.String(length=32), nullable=True),
    sa.Column('text_enc', sa.LargeBinary(), nullable=True),
    sa.Column('redacted_text', sa.Text(), nullable=True),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('bbox', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('sheet', sa.String(length=100), nullable=True),
    sa.Column('cell_ref', sa.String(length=16), nullable=True),
    sa.Column('ocr_conf', sa.REAL(), nullable=True),
    sa.Column('has_revision', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("kind IN ('heading', 'paragraph', 'list_item', 'table_cell', 'sheet_cell', 'ocr_block')", name=op.f('ck_doc_nodes_kind_valid')),
    sa.CheckConstraint("part IN ('body', 'header', 'footer', 'comment', 'textbox', 'hidden_sheet')", name=op.f('ck_doc_nodes_part_valid')),
    sa.ForeignKeyConstraint(['file_id'], ['task_files.id'], name=op.f('fk_doc_nodes_file_id_task_files'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_doc_nodes_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_doc_nodes')),
    sa.UniqueConstraint('task_id', 'loc', name=op.f('uq_doc_nodes_task_id_loc'))
    )
    op.create_index('ix_doc_nodes_file_id_ord', 'doc_nodes', ['file_id', 'ord'], unique=False)
    op.create_table('findings',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=12), nullable=False),
    sa.Column('severity', sa.String(length=6), nullable=False),
    sa.Column('req_ids', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('locs', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('missing', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('cite_status', sa.String(length=10), nullable=False),
    sa.Column('quote_model', sa.Text(), nullable=True),
    sa.Column('quote_snapshot_enc', sa.LargeBinary(), nullable=True),
    sa.Column('problem', sa.Text(), nullable=False),
    sa.Column('impact', sa.Text(), nullable=True),
    sa.Column('suggestion', sa.Text(), nullable=True),
    sa.Column('suggested_text', sa.Text(), nullable=True),
    sa.Column('basis', sa.Text(), nullable=True),
    sa.Column('handling_status', sa.String(length=10), server_default='pending', nullable=False),
    sa.Column('handling_note', sa.String(length=500), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("category IN ('wording', 'logic', 'legal', 'compliance', 'commercial')", name=op.f('ck_findings_category_valid')),
    sa.CheckConstraint("cite_status IN ('verified', 'unverified', 'missing')", name=op.f('ck_findings_cite_status_valid')),
    sa.CheckConstraint("handling_status IN ('pending', 'adopted', 'ignored')", name=op.f('ck_findings_handling_status_valid')),
    sa.CheckConstraint("severity IN ('high', 'medium', 'low')", name=op.f('ck_findings_severity_valid')),
    sa.ForeignKeyConstraint(['run_id'], ['review_runs.id'], name=op.f('fk_findings_run_id_review_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_findings_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_findings')),
    sa.UniqueConstraint('run_id', 'seq', name=op.f('uq_findings_run_id_seq'))
    )
    op.create_index(op.f('ix_findings_task_id'), 'findings', ['task_id'], unique=False)
    op.create_table('requirement_results',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('req_id', sa.String(length=16), nullable=False),
    sa.Column('req_source', sa.String(length=8), nullable=False),
    sa.Column('req_text', sa.Text(), nullable=False),
    sa.Column('dimension', sa.String(length=16), nullable=True),
    sa.Column('verdict', sa.String(length=8), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint("req_source IN ('builtin', 'company', 'note')", name=op.f('ck_requirement_results_req_source_valid')),
    sa.CheckConstraint("verdict IN ('issue', 'ok', 'na', 'unable')", name=op.f('ck_requirement_results_verdict_valid')),
    sa.ForeignKeyConstraint(['run_id'], ['review_runs.id'], name=op.f('fk_requirement_results_run_id_review_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requirement_results')),
    sa.UniqueConstraint('run_id', 'req_id', name=op.f('uq_requirement_results_run_id_req_id'))
    )


def downgrade() -> None:
    op.drop_table('requirement_results')
    op.drop_index(op.f('ix_findings_task_id'), table_name='findings')
    op.drop_table('findings')
    op.drop_index('ix_doc_nodes_file_id_ord', table_name='doc_nodes')
    op.drop_table('doc_nodes')
    op.drop_table('task_files')
    op.drop_table('review_runs')
    op.drop_index(op.f('ix_redaction_suspects_task_id'), table_name='redaction_suspects')
    op.drop_table('redaction_suspects')
    op.drop_table('redaction_maps')
    op.drop_index('ix_redaction_items_task_id_loc', table_name='redaction_items')
    op.drop_table('redaction_items')
    op.drop_table('parties')
    op.drop_index(op.f('ix_local_findings_task_id'), table_name='local_findings')
    op.drop_table('local_findings')
    op.drop_index('ix_chat_messages_task_id_id', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index('ix_tasks_owner_id_created_at', table_name='tasks')
    op.drop_table('tasks')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_table('redaction_terms')
    op.drop_index('ix_llm_calls_user_id_created_at', table_name='llm_calls')
    op.drop_index('ix_llm_calls_created_at', table_name='llm_calls')
    op.drop_table('llm_calls')
    op.drop_table('users')
    op.drop_table('settings')
    op.drop_table('requirement_versions')
    op.drop_index('ix_jobs_status_run_after', table_name='jobs')
    op.drop_table('jobs')
    op.drop_table('company_requirements')
    op.drop_table('backup_records')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.execute('DROP EXTENSION IF EXISTS pgcrypto')
