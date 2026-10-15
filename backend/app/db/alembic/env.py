"""Alembic 环境。连接串优先取 Config 中显式设置的 sqlalchemy.url（测试用），否则取 DATABASE_URL。"""
from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.db import models  # noqa: F401  让所有表登记到 metadata
from app.db.base import Base
from app.db.session import normalize_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    if not url:
        raise RuntimeError("未配置 DATABASE_URL")
    return normalize_url(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
