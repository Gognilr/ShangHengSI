"""V2-C-01：分类并发配置、范围校验与连接池。"""
from types import SimpleNamespace

import pytest

from app.config import load_settings
from app.db import session


def test_classified_concurrency_defaults(monkeypatch):
    for name in ("WORKER_REVIEW_CONCURRENCY", "WORKER_PARSE_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert (settings.worker_review_concurrency, settings.worker_parse_concurrency) == (8, 2)


@pytest.mark.parametrize("name", ["WORKER_REVIEW_CONCURRENCY", "WORKER_PARSE_CONCURRENCY"])
@pytest.mark.parametrize("value", ["0", "33", "-1", "abc", "", "1.5"])
def test_classified_concurrency_rejects_invalid_range(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=rf"{name}.*1.*32"):
        load_settings()


@pytest.mark.parametrize("value", ["1", "32"])
def test_classified_concurrency_accepts_boundaries(monkeypatch, value):
    monkeypatch.setenv("WORKER_REVIEW_CONCURRENCY", value)
    monkeypatch.setenv("WORKER_PARSE_CONCURRENCY", value)
    settings = load_settings()
    assert settings.worker_review_concurrency == settings.worker_parse_concurrency == int(value)


def test_engine_pool_options_leave_app_defaults_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(session, "get_settings", lambda: SimpleNamespace(database_url="postgresql://test"))
    monkeypatch.setattr(session, "create_engine", lambda url, **kw: calls.append((url, kw)) or object())
    session.get_engine.cache_clear()
    try:
        app_engine = session.get_engine()
        worker_engine = session.get_engine(pool_size=12, max_overflow=12)
        assert app_engine is not worker_engine
        assert session.get_engine(pool_size=12, max_overflow=12) is worker_engine
        assert calls == [
            ("postgresql+psycopg://test", {"pool_pre_ping": True}),
            ("postgresql+psycopg://test", {"pool_pre_ping": True, "pool_size": 12, "max_overflow": 12}),
        ]
    finally:
        session.get_engine.cache_clear()
