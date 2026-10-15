"""数据库引擎与会话。引擎在首次使用时按 DATABASE_URL 创建。"""
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def normalize_url(url: str) -> str:
    """接受 postgresql:// 写法，统一使用 psycopg 3 驱动。"""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("未配置 DATABASE_URL")
    return create_engine(normalize_url(url), pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个数据库会话。"""
    with get_sessionmaker()() as db:
        yield db
