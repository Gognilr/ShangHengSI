"""备份文件格式（设计 docs/design/M7.md 3.1、3.5）：往返、篡改、截断、调换、错密码、参数被改。"""
import io
import os
import struct

import pytest

from app.backup import archive
from app.backup.archive import CHUNK_SIZE, HEADER_SIZE, BackupFormatError, EncryptingWriter, decrypt_stream

PASSWORD = b"fictive-backup-pass-0001"


def _encrypt(data: bytes, password: bytes = PASSWORD, pieces: int = 1) -> bytes:
    out = io.BytesIO()
    w = EncryptingWriter(out, password)
    step = max(1, len(data) // pieces) if data else 1
    for i in range(0, len(data), step):
        w.write(data[i:i + step])
    w.close()
    return out.getvalue()


def _decrypt(blob: bytes, password: bytes = PASSWORD) -> bytes:
    out = io.BytesIO()
    n = decrypt_stream(io.BytesIO(blob), out, password)
    assert n == len(out.getvalue())
    return out.getvalue()


def _chunks(blob: bytes) -> list[tuple[int, int]]:
    """每块（起始偏移, 总长度）。"""
    pos, out = HEADER_SIZE, []
    while pos < len(blob):
        (length,) = struct.unpack(">I", blob[pos + 12:pos + 16])
        out.append((pos, 16 + length))
        pos += 16 + length
    return out


@pytest.mark.parametrize("size", [0, 1, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, 3 * CHUNK_SIZE + 17])
def test_roundtrip(size):
    data = os.urandom(size)
    assert _decrypt(_encrypt(data)) == data
    assert _decrypt(_encrypt(data, pieces=7)) == data


def test_flipping_any_byte_fails():
    data = os.urandom(CHUNK_SIZE + 100)
    blob = _encrypt(data)
    positions = [0, 7, 9, 13, 20, 31, HEADER_SIZE, HEADER_SIZE + 5, HEADER_SIZE + 14, HEADER_SIZE + 40, len(blob) - 1]
    for _, (start, n) in enumerate(_chunks(blob)):
        positions += [start + n - 1]
    for pos in positions:
        bad = bytearray(blob)
        bad[pos] ^= 0x01
        with pytest.raises(BackupFormatError):
            _decrypt(bytes(bad))


def test_wrong_password_fails():
    blob = _encrypt(b"fictive")
    with pytest.raises(BackupFormatError, match="密码不正确"):
        _decrypt(blob, b"another-password-123")


