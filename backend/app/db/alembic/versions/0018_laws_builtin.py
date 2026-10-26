"""V2-E-02：将原12部废止法律清单迁入法规库；不覆盖同键已有数据。"""
from datetime import date

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None

# 迁移数据固定在此，不依赖已删除的运行时 YAML 或未来版本的 ORM。
_SEEDS = [
    ('合同法', '1999-03-15', '2021-01-01', '民法典'),
    ('担保法', '1995-06-30', '2021-01-01', '民法典'),
    ('物权法', '2007-03-16', '2021-01-01', '民法典'),
    ('民法通则', '2009-08-27', '2021-01-01', '民法典'),
    ('民法总则', '2017-03-15', '2021-01-01', '民法典'),
    ('侵权责任法', '2009-12-26', '2021-01-01', '民法典'),
    ('中外合资经营企业法', '2016-09-03', '2020-01-01', '外商投资法'),
    ('中外合作经营企业法', '2017-11-04', '2020-01-01', '外商投资法'),
    ('外资企业法', '2016-09-03', '2020-01-01', '外商投资法'),
    ('经济合同法', None, '1999-10-01', '民法典'),
    ('涉外经济合同法', None, '1999-10-01', '民法典'),
    ('技术合同法', None, '1999-10-01', '民法典'),
]
_PREFIX = '中华人民共和国'


def _table():
    return sa.table('laws', sa.column('title', sa.String), sa.column('category', sa.String),
                    sa.column('status', sa.String), sa.column('source', sa.String),
                    sa.column('published_on', sa.Date), sa.column('repealed_on', sa.Date),
                    sa.column('replaced_by', sa.String), sa.column('created_at', sa.DateTime(timezone=True)),
                    sa.column('updated_at', sa.DateTime(timezone=True)))


def upgrade():
    laws = _table()
    for title, published, repealed, replacement in _SEEDS:
        op.execute(insert(laws).values(title=_PREFIX+title, category='法律', status='repealed', source='builtin',
                   published_on=date.fromisoformat(published) if published else None,
                   repealed_on=date.fromisoformat(repealed), replaced_by=_PREFIX+replacement,
                   created_at=sa.func.now(), updated_at=sa.func.now()).on_conflict_do_nothing())


def downgrade():
    laws = _table()
    keys = [sa.and_(laws.c.title == _PREFIX+title,
                    laws.c.published_on == (date.fromisoformat(published) if published else None))
            for title, published, _, _ in _SEEDS]
    op.execute(laws.delete().where(sa.or_(*keys), laws.c.source == 'builtin', laws.c.updated_at == laws.c.created_at))
