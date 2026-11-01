"""V31-S-03a：确认替换的类别与主写法关联。"""
import sqlalchemy as sa
from alembic import op

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None

CATEGORIES = "'ORG','PERSON','CUSTOM','PROJECT','ADDR','REFNO','ACCOUNT','IDNO','IM','NAME','TERM'"


def upgrade():
    op.drop_constraint(op.f('ck_redaction_terms_category_valid'), 'redaction_terms', type_='check')
    op.create_check_constraint(op.f('ck_redaction_terms_category_valid'), 'redaction_terms', f'category IN ({CATEGORIES})')
    op.add_column('redaction_terms', sa.Column('alias_of', sa.CHAR(64)))


def downgrade():
    op.drop_column('redaction_terms', 'alias_of')
    op.execute("UPDATE redaction_terms SET category='CUSTOM' WHERE category NOT IN ('ORG','PERSON','CUSTOM')")
    op.drop_constraint(op.f('ck_redaction_terms_category_valid'), 'redaction_terms', type_='check')
    op.create_check_constraint(op.f('ck_redaction_terms_category_valid'), 'redaction_terms', "category IN ('ORG','PERSON','CUSTOM')")
