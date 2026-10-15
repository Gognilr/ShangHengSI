"""测试公共夹具。测试样本只用虚构内容，模型调用一律用模拟服务。

需要数据库的测试使用 db_sessionmaker / api 等夹具：读取 TEST_DATABASE_URL（open_questions Q-26），
每个测试会话新建一个独立的临时库并迁移到最新版本，结束后删除；每个测试开始前清空全部表。
未设置 TEST_DATABASE_URL 时，这些测试自动跳过。
"""
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import create_app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
BACKEND_DIR = Path(__file__).resolve().parents[1]
# 测试用户的默认密码（虚构，满足强度规则）
DEFAULT_PASSWORD = "Test1234pass"


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    # raise_server_exceptions=False：让未处理异常走统一的 500 处理器，便于断言错误格式
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- 数据库


@pytest.fixture(scope="session")
def pg_url():
    """会话级临时库（已迁移到 head）的连接串。"""
    if not TEST_DATABASE_URL:
        pytest.skip("未设置 TEST_DATABASE_URL")
    from alembic import command
    from alembic.config import Config

    name = "aireview_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = urlunsplit(urlsplit(TEST_DATABASE_URL)._replace(path="/" + name))
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    yield url
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def db_engine(pg_url):
    from app.db.base import Base
    from app.db.session import normalize_url

    engine = create_engine(normalize_url(pg_url))
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    engine.dispose()


@pytest.fixture
def db_sessionmaker(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def db_app(db_sessionmaker):
    """接入测试库的 app：get_db 改为使用测试库。"""
    from app.db.session import get_db

    application = create_app()

    def _get_db():
        with db_sessionmaker() as s:
            yield s

    application.dependency_overrides[get_db] = _get_db
    return application


@pytest.fixture
def new_client(db_app):
    """返回一个工厂，每次调用得到一个独立的浏览器（各自的 Cookie）。Cookie 带 Secure，必须用 https。"""

    def _make() -> TestClient:
        return TestClient(db_app, base_url="https://testserver")

    return _make


@pytest.fixture
def api(new_client):
    return new_client()


@pytest.fixture
def make_user(db_sessionmaker):
    """创建用户：make_user("alice", role="admin", must_change_password=True, status="disabled")。"""
    from app.db.base import utcnow
    from app.db.models import User
    from app.security.passwords import hash_password

    def _make(
        username: str,
        *,
        password: str = DEFAULT_PASSWORD,
        role: str = "user",
        status: str = "active",
        must_change_password: bool = False,
    ) -> User:
        now = utcnow()
        user = User(
            username=username,
            display_name=username,
            password_hash=hash_password(password),
            role=role,
            status=status,
            must_change_password=must_change_password,
            created_at=now,
            updated_at=now,
        )
        with db_sessionmaker() as s:
            s.add(user)
            s.commit()
        return user

    return _make


@pytest.fixture
def login(new_client):
    """登录并返回（已带会话 Cookie 的客户端，csrf_token）。"""

    def _login(username: str, password: str = DEFAULT_PASSWORD) -> tuple[TestClient, str]:
        c = new_client()
        r = c.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return c, r.json()["csrf_token"]

    return _login
