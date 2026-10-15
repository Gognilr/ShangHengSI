"""主密钥加密测试（SPEC 17.3）。测试用的密钥都是临时生成的随机值。"""
import os
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.config import get_settings
from app.security import crypto
from app.security.crypto import (
    FORMAT_VERSION,
    KEY_SIZE,
    NONCE_SIZE,
    TAG_SIZE,
    DecryptionError,
    decrypt,
    decrypt_file,
    decrypt_from_b64,
    decrypt_text,
    encrypt,
    encrypt_text,
    encrypt_to_b64,
    encrypt_to_file,
    load_master_key,
)

KEY = os.urandom(KEY_SIZE)


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    """把 APP_MASTER_KEY_FILE 指向一个临时密钥文件，并清掉配置和密钥缓存。"""

    def _make(content: bytes | None):
        path = tmp_path / "master_key"
        if content is not None:
            path.write_bytes(content)
        monkeypatch.setenv("APP_MASTER_KEY_FILE", str(path))
        get_settings.cache_clear()
        crypto.get_master_key.cache_clear()
        return path

    yield _make
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


# ---------------------------------------------------------------- 密文格式与往返


def test_format_and_roundtrip():
    plaintext = "甲方：虚构贸易有限公司，合同总价 USD 1,234,567.00".encode("utf-8")
    blob = encrypt(plaintext, key=KEY)
    assert blob[0] == FORMAT_VERSION
    assert len(blob) == 1 + NONCE_SIZE + len(plaintext) + TAG_SIZE
    assert plaintext not in blob
    assert decrypt(blob, key=KEY) == plaintext


@pytest.mark.parametrize("plaintext", [b"", b"x", os.urandom(1), os.urandom(100_000)])
def test_roundtrip_various_sizes(plaintext):
    assert decrypt(encrypt(plaintext, key=KEY), key=KEY) == plaintext


def test_text_and_b64_roundtrip():
    text = "客户_A：虚构公司 Example Trading LLC"
    assert decrypt_text(encrypt_text(text, key=KEY), key=KEY) == text
    b64 = encrypt_to_b64("sk-test-not-a-real-key", key=KEY)
    assert isinstance(b64, str)
    assert decrypt_from_b64(b64, key=KEY) == "sk-test-not-a-real-key"


def test_invalid_b64_rejected():
    with pytest.raises(DecryptionError):
        decrypt_from_b64("这不是base64", key=KEY)


def test_nonce_is_fresh_every_time():
    blobs = [encrypt(b"same plaintext", key=KEY) for _ in range(1000)]
    nonces = {b[1 : 1 + NONCE_SIZE] for b in blobs}
    assert len(nonces) == 1000
    assert len(set(blobs)) == 1000


# ---------------------------------------------------------------- 篡改与错误输入


def test_tampering_any_byte_fails():
    blob = encrypt("虚构原文".encode("utf-8"), key=KEY)
    for i in range(len(blob)):
        tampered = bytearray(blob)
        tampered[i] ^= 0x01
        with pytest.raises(DecryptionError):
            decrypt(bytes(tampered), key=KEY)


def test_truncated_or_extended_fails():
    blob = encrypt(b"some data", key=KEY)
    for bad in (b"", blob[:1], blob[: 1 + NONCE_SIZE + TAG_SIZE - 1], blob[:-1], blob + b"\x00"):
        with pytest.raises(DecryptionError):
            decrypt(bad, key=KEY)


def test_unknown_version_rejected():
    blob = bytearray(encrypt(b"data", key=KEY))
    blob[0] = 2
    with pytest.raises(DecryptionError, match="版本"):
        decrypt(bytes(blob), key=KEY)


def test_wrong_key_fails():
    blob = encrypt(b"data", key=KEY)
    with pytest.raises(DecryptionError):
        decrypt(blob, key=os.urandom(KEY_SIZE))


def test_aad_must_match():
    blob = encrypt(b"data", key=KEY, aad=b"doc_nodes:1")
    assert decrypt(blob, key=KEY, aad=b"doc_nodes:1") == b"data"
    for bad_aad in (None, b"doc_nodes:2"):
        with pytest.raises(DecryptionError):
            decrypt(blob, key=KEY, aad=bad_aad)


def test_error_messages_do_not_leak_content():
    secret = "虚构客户名称有限公司".encode("utf-8")
    blob = bytearray(encrypt(secret, key=KEY))
    blob[-1] ^= 0xFF
    with pytest.raises(DecryptionError) as exc:
        decrypt(bytes(blob), key=KEY)
    assert "虚构" not in str(exc.value)
    assert exc.value.__cause__ is None


# ---------------------------------------------------------------- 文件加密


def test_file_roundtrip_is_atomic_and_private(tmp_path):
    target = tmp_path / "3f2b.bin"
    data = os.urandom(50_000)
    encrypt_to_file(target, data, key=KEY)
    assert decrypt_file(target, key=KEY) == data
    assert data not in target.read_bytes()
    assert not (tmp_path / "3f2b.bin.tmp").exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


# ---------------------------------------------------------------- 主密钥文件


def test_master_key_loaded_from_settings(key_file):
    key_file(KEY)
    assert crypto.get_master_key() == KEY
    blob = encrypt(b"via master key")
    assert decrypt(blob, key=KEY) == b"via master key"


@pytest.mark.parametrize("size", [0, 16, KEY_SIZE - 1, KEY_SIZE + 1, 64])
def test_master_key_wrong_size_rejected(key_file, size):
    content = os.urandom(size)
    path = key_file(content)
    with pytest.raises(RuntimeError, match="32 字节") as exc:
        load_master_key(str(path))
    assert content.hex() not in str(exc.value) or size == 0


def test_master_key_missing_rejected(key_file):
    key_file(None)
    with pytest.raises(RuntimeError, match="不存在"):
        crypto.check_master_key()


def test_master_key_path_not_configured(monkeypatch):
    monkeypatch.delenv("APP_MASTER_KEY_FILE", raising=False)
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="未配置"):
            crypto.check_master_key()
    finally:
        get_settings.cache_clear()
        crypto.get_master_key.cache_clear()


def test_app_refuses_to_start_with_bad_key(key_file):
    from app.main import create_app

    key_file(os.urandom(KEY_SIZE - 1))
    with pytest.raises(RuntimeError, match="32 字节"):
        with TestClient(create_app()):
            pass


def test_app_starts_with_valid_key(key_file):
    from app.main import create_app

    key_file(KEY)
    with TestClient(create_app()) as c:
        assert c.get("/api/v1/no-such-path").status_code == 404


def test_worker_refuses_to_start_with_bad_key(key_file):
    from app import worker

    key_file(b"too short")
    with pytest.raises(RuntimeError, match="32 字节"):
        worker.main()


# ---------------------------------------------------------------- gen-key 命令


def test_gen_key_creates_private_32_byte_file(tmp_path):
    out = tmp_path / "master_key"
    assert cli.main(["gen-key", "--out", str(out)]) == 0
    key = out.read_bytes()
    assert len(key) == KEY_SIZE
    assert load_master_key(str(out)) == key
    if sys.platform != "win32":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_gen_key_never_overwrites(tmp_path, capsys):
    out = tmp_path / "master_key"
    out.write_bytes(KEY)
    assert cli.main(["gen-key", "--out", str(out)]) == 1
    assert out.read_bytes() == KEY
    assert "拒绝覆盖" in capsys.readouterr().err


def test_gen_key_produces_different_keys(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    cli.main(["gen-key", "--out", str(a)])
    cli.main(["gen-key", "--out", str(b)])
    assert a.read_bytes() != b.read_bytes()
