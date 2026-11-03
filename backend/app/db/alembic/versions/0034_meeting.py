"""V31-M：录音、逐字稿与会议纪要字段。"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None

ENUMS = [
    ('task_files', 'ext', ['docx','pdf','xlsx','jpg','png','doc','xls','wps','et','rtf'], ['mp3','m4a','wav','amr','aac','mp4']),
    ('doc_nodes', 'kind', ['heading','paragraph','list_item','table_cell','sheet_cell','ocr_block'], ['utterance']),
    ('jobs', 'type', ['parse','review','backup','retention','law_import','law_embed','law_sync','template_embed','assist','kb_import'], ['transcribe']),
    # 设计 3.1 的 chat_messages.template 在现有实现中为 assistant_messages.template。
    ('assistant_messages', 'template', ['translate','summary','reply','crosscheck','compare'], ['minutes']),
]
COLUMNS = {
    'tasks': [('participants_enc', sa.LargeBinary())],
    'task_files': [('media', JSONB()), ('media_storage_key', sa.String(64)), ('speakers_enc', sa.LargeBinary())],
    'doc_nodes': [('t_start_ms', sa.Integer()), ('t_end_ms', sa.Integer()), ('speaker', sa.SmallInteger()), ('low_conf_spans', JSONB())],
}


def checks(expand):
    for table, column, old, new in ENUMS:
        name = op.f(f'ck_{table}_{column}_valid')
        op.drop_constraint(name, table, type_='check')
        values = old + new if expand else old
        op.create_check_constraint(name, table, column + ' IN (' + ','.join(repr(v) for v in values) + ')')


def upgrade():
    for table, columns in COLUMNS.items():
        for name, kind in columns:
            op.add_column(table, sa.Column(name, kind, nullable=True))
    checks(True)


def downgrade():
    for table, column, old, new in reversed(ENUMS):
        op.execute('DELETE FROM ' + table + ' WHERE ' + column + ' IN (' + ','.join(repr(v) for v in new) + ')')
    checks(False)
    for table, columns in reversed(list(COLUMNS.items())):
        for name, kind in reversed(columns):
            op.drop_column(table, name)
