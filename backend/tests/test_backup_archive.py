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


def test_header_and_chunk_layout():
    blob = _encrypt(os.urandom(2 * CHUNK_SIZE + 5))
    assert blob[:8] == b"AIRVBAK\x01"
    assert blob[8:12] == bytes([15, 8, 1, 0])
    assert struct.unpack(">I", blob[12:16])[0] == CHUNK_SIZE
    sizes = [n for _, n in _chunks(blob)]
    assert sizes == [16 + CHUNK_SIZE + 16, 16 + CHUNK_SIZE + 16, 16 + 5 + 16]


def test_same_content_encrypts_differently():
    data = b"same fictive content" * 100
    a, b = _encrypt(data), _encrypt(data)
    assert a != b and a[16:32] != b[16:32]  # 盐不同


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


def test_swapping_chunks_fails():
    blob = _encrypt(os.urandom(3 * CHUNK_SIZE))
    (s0, n0), (s1, n1), *_ = _chunks(blob)
    swapped = blob[:s0] + blob[s1:s1 + n1] + blob[s0:s0 + n0] + blob[s1 + n1:]
    with pytest.raises(BackupFormatError):
        _decrypt(swapped)


def test_truncation_and_dropping_final_chunk_fail():
    blob = _encrypt(os.urandom(2 * CHUNK_SIZE + 10))
    chunks = _chunks(blob)
    last_start = chunks[-1][0]
    for cut in (HEADER_SIZE - 1, HEADER_SIZE, last_start, last_start + 10, len(blob) - 1):
        with pytest.raises(BackupFormatError):
            _decrypt(blob[:cut])
    # 去掉中间一块
    s, n = chunks[1]
    with pytest.raises(BackupFormatError):
        _decrypt(blob[:s] + blob[s + n:])


def test_data_after_final_chunk_fails():
    blob = _encrypt(b"fictive")
    with pytest.raises(BackupFormatError, match="多余"):
        _decrypt(blob + b"\x00")
    with pytest.raises(BackupFormatError):
        _decrypt(blob + _encrypt(b"more")[HEADER_SIZE:])


def test_wrong_password_fails():
    blob = _encrypt(b"fictive")
    with pytest.raises(BackupFormatError, match="密码不正确"):
        _decrypt(blob, b"another-password-123")


@pytest.mark.parametrize("offset,value", [(8, 20), (9, 16), (10, 2), (11, 1)])
def test_changed_scrypt_parameters_rejected_before_kdf(offset, value, monkeypatch):
    blob = bytearray(_encrypt(b"fictive"))
    blob[offset] = value
    called = []
    monkeypatch.setattr(archive, "derive_key", lambda *a: called.append(1) or b"\x00" * 32)
    with pytest.raises(BackupFormatError, match="参数"):
        _decrypt(bytes(blob))
    assert not called


def test_changed_chunk_size_and_oversized_chunk_rejected():
    blob = bytearray(_encrypt(b"fictive"))
    blob[12:16] = struct.pack(">I", 2 * CHUNK_SIZE)
    with pytest.raises(BackupFormatError, match="参数"):
        _decrypt(bytes(blob))
    blob = bytearray(_encrypt(b"fictive"))
    blob[HEADER_SIZE + 12:HEADER_SIZE + 16] = struct.pack(">I", CHUNK_SIZE + 17)
    with pytest.raises(BackupFormatError, match="块长度"):
        _decrypt(bytes(blob))


def test_not_a_backup_file():
    for blob in (b"", b"PK\x03\x04" + b"\x00" * 40, b"AIRVBAK\x02" + b"\x00" * 40):
        with pytest.raises(BackupFormatError):
            _decrypt(blob)


def test_error_messages_contain_no_content():
    secret = b"Fictive Secret Trading Co. USD 123,456.00"
    blob = bytearray(_encrypt(secret))
    blob[-1] ^= 1
    with pytest.raises(BackupFormatError) as exc:
        _decrypt(bytes(blob))
    assert "Fictive" not in str(exc.value) and PASSWORD.decode() not in str(exc.value)


def test_writer_rejects_empty_password_and_write_after_close():
    with pytest.raises(ValueError):
        EncryptingWriter(io.BytesIO(), b"")
    w = EncryptingWriter(io.BytesIO(), PASSWORD)
    w.close()
    w.close()  # 重复关闭无副作用
    with pytest.raises(ValueError):
        w.write(b"x")


def test_load_backup_password(tmp_path):
    p = tmp_path / "backup_password"
    p.write_bytes(b"correct-horse-battery\r\n")
    assert archive.load_backup_password(str(p)) == b"correct-horse-battery"
    p.write_bytes("十二个字的中文备份密码共十二".encode() + b"\n")
    assert archive.load_backup_password(str(p)).decode() == "十二个字的中文备份密码共十二"
    for bad in (b"", b"\n", b"short-pass1\n", b"\xff\xfe" * 10):
        p.write_bytes(bad)
        with pytest.raises(RuntimeError, match="备份密码文件无效") as exc:
            archive.load_backup_password(str(p))
        assert "short-pass1" not in str(exc.value)
    with pytest.raises(RuntimeError):
        archive.load_backup_password(str(tmp_path / "missing"))
    with pytest.raises(RuntimeError):
        archive.load_backup_password("")
