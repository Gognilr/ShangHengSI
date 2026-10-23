"""M2-12 的测试公共部分：隔离的主密钥与原件目录、已配置的模拟模型、已解析好的待确认任务。

被 test_runs_api.py 和 test_review_job.py 共用；模型调用一律经 httpx.MockTransport，
没有显式安装模拟服务的测试一旦真的发请求就会失败（不会联网）。
"""
import os
from pathlib import Path

import httpx
import pytest

from app.admin import settings_store
from app.config import get_settings
from app.db.models import Task, TaskFile, User
from app.llm import gateway
from app.security import crypto
from app.tasks import pipeline, storage
from tests.mock_llm import mock_transport

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"
REAL_OUR_NAME = "星河虚构进出口有限公司"
REAL_BUYER_NAME = "Fictional Metals Trading FZE"


def sample(name: str) -> bytes:
    return (FIXTURES / f"{name}.docx").read_bytes()


def _no_call(request: httpx.Request) -> httpx.Response:
    raise AssertionError("测试中出现了未预期的模型调用")


@pytest.fixture
def env(tmp_path, monkeypatch, db_sessionmaker):
    """主密钥、原件目录、已配置的模型设置；重试间隔改为 0；默认不允许任何模型调用。"""
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    monkeypatch.setenv("FILES_DIR", str(tmp_path / "files"))
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    monkeypatch.setattr(gateway, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(_no_call))
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.base_url", "https://llm.example.test/v1", None)
        settings_store.set_setting(db, "model.name", "mock-model", None)
        settings_store.set_setting(db, "model.api_key_enc", crypto.encrypt_to_b64("sk-test-0123456789"), None)
        db.commit()
    yield
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


@pytest.fixture
def llm(monkeypatch, env):
    """use(modes) 安装模拟模型服务并返回收到的请求列表；也可传入自己的 httpx 处理函数。"""

    def use(modes="normal", handler=None) -> list[httpx.Request]:
        requests: list[httpx.Request] = []
        if handler is None:
            monkeypatch.setattr(gateway, "TRANSPORT", mock_transport(modes, requests))
        else:
            def wrapped(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                return handler(request)

            monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(wrapped))
        return requests

    return use


@pytest.fixture
def ready_task(db_sessionmaker, env):
    """工厂：为 owner 建一个已解析、待确认（awaiting_confirm）的任务，默认是 contract_basic 样本。"""

    def _make(owner: User, files: list[tuple[str, bytes]] | None = None, *, policy: str = "ai",
              stance: str = "sales", source: str = "outgoing", user_note: str | None = None) -> Task:
        files = files or [("销售合同.docx", sample("contract_basic"))]
        with db_sessionmaker() as db:
            task = Task(owner_id=owner.id, title=files[0][0], stance=stance, source=source, policy=policy,
                        user_note_enc=crypto.encrypt_text(user_note) if user_note else None, status="parsing")
            db.add(task)
            db.flush()
            for seq, (name, data) in enumerate(files, start=1):
                key, sha = storage.save_original(data)
                db.add(TaskFile(task_id=task.id, seq=seq, is_main=seq == 1, original_name=name,
                                ext=name.rsplit(".", 1)[-1], size_bytes=len(data), sha256=sha, storage_key=key))
            db.commit()
            pipeline.parse_task(db, task.id)
            db.refresh(task)
            return task

    return _make
