from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import API_PREFIX
from app.security.errors import AppError


def test_unknown_route_uses_unified_error_format(client):
    r = client.get(f"{API_PREFIX}/no-such-path")
    assert r.status_code == 404
    assert r.json() == {"code": "NOT_FOUND", "message": "资源不存在"}


def test_app_error_unhandled_and_validation_formats(app):
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/_t/conflict")
    def conflict():
        raise AppError(409, "STATE_CONFLICT", "当前状态不允许此操作")

    @router.get("/_t/boom")
    def boom():
        raise RuntimeError("secret-original-text")

    @router.get("/_t/num")
    def num(n: int):
        return {"n": n}

    app.include_router(router)
    c = TestClient(app, raise_server_exceptions=False)

    r = c.get(f"{API_PREFIX}/_t/conflict")
    assert r.status_code == 409
    assert r.json() == {"code": "STATE_CONFLICT", "message": "当前状态不允许此操作"}

    r = c.get(f"{API_PREFIX}/_t/boom")
    assert r.status_code == 500
    assert r.json()["code"] == "INTERNAL_ERROR"
    assert "secret-original-text" not in r.text

    r = c.get(f"{API_PREFIX}/_t/num", params={"n": "abc-secret"})
    assert r.status_code == 422
    assert set(r.json()) == {"code", "message"}
    assert "abc-secret" not in r.text


def test_settings_defaults(monkeypatch):
    for name in ("WORKER_CONCURRENCY", "MAX_UPLOAD_MB", "FILES_DIR", "BACKUP_DIR"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert s.worker_concurrency == 2
    assert s.max_upload_mb == 30
    assert s.files_dir == "/data/files"
    assert s.backup_dir == "/backup"
