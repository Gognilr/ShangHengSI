"""备份文件格式：分块流式加密与解密（SPEC 16.2 第 3 步、16.3，设计 docs/design/M7.md 3.1）。

文件 = 文件头（明文 32 字节）+ 若干块：
  文件头：magic b"AIRVBAK\\x01"（末字节为格式版本）、scrypt log2(n)、r、p、保留字节、明文块大小（4 字节大端）、盐（16 字节）
  块：随机数（12 字节）+ 密文长度（4 字节大端）+ AES-256-GCM 密文（含 16 字节标签）
  每块的关联数据 = 文件头 + 块序号（8 字节大端）+ 末块标志（1 字节）；最后一块一定带末块标志（明文可以为空）。
调换、删除、截断任何一块，或在末块后追加数据，都会解密失败。

密钥由备份密码经 scrypt 派生。解密时只接受本版本固定的参数，防止文件头被改成极大的 n 耗尽内存。
本模块不碰数据库，异常消息只说明错误类别，不带任何内容、密码或密钥。M8 的 restore.sh 经 `python -m app.cli
decrypt-backup` 调用 decrypt_stream，不另写解密实现。
"""
import os
import struct
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.config import read_secret_file

MAGIC = b"AIRVBAK\x01"
SCRYPT_LOG_N = 15
SCRYPT_R = 8
SCRYPT_P = 1
CHUNK_SIZE = 1024 * 1024  # 明文每块 1 MiB
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
_HEADER = struct.Struct(">8sBBBBI16s")  # 32 字节
HEADER_SIZE = _HEADER.size
_CHUNK_HEAD = struct.Struct(">12sI")
MIN_PASSWORD_CHARS = 12  # open_questions Q-100


class BackupFormatError(Exception):
    """备份文件格式不对、被篡改、被截断或密码错误。消息不含任何内容。"""


def derive_key(password: bytes, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** SCRYPT_LOG_N, r=SCRYPT_R, p=SCRYPT_P).derive(password)


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + struct.pack(">QB", index, 1 if final else 0)


def load_backup_password(path: str) -> bytes:
    """读取备份密码文件：去掉末尾换行后至少 12 个字符（Q-100）。不符合时抛出不含内容的 RuntimeError。"""
    raw = read_secret_file(path)
    password = raw.rstrip(b"\r\n")
    try:
        chars = len(password.decode("utf-8"))
    except UnicodeDecodeError:
        raise RuntimeError("备份密码文件无效：不是 UTF-8 文本") from None
    if chars < MIN_PASSWORD_CHARS:
        raise RuntimeError(f"备份密码文件无效：密码至少 {MIN_PASSWORD_CHARS} 个字符")
    return password


class EncryptingWriter:
    """流式加密写入：write() 缓冲到满一块就加密写出，close() 写出末块。不关闭 out。"""

    def __init__(self, out: BinaryIO, password: bytes):
        if not password:
            raise ValueError("备份密码为空")
        salt = os.urandom(SALT_SIZE)
        self._header = _HEADER.pack(MAGIC, SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P, 0, CHUNK_SIZE, salt)
        self._aead = AESGCM(derive_key(password, salt))
        self._out = out
        self._buf = bytearray()
        self._index = 0
        self._closed = False
        out.write(self._header)

    def _emit(self, data: bytes, final: bool) -> None:
        nonce = os.urandom(NONCE_SIZE)
        ct = self._aead.encrypt(nonce, data, _aad(self._header, self._index, final))
        self._out.write(_CHUNK_HEAD.pack(nonce, len(ct)))
        self._out.write(ct)
        self._index += 1

    def write(self, data: bytes) -> int:
        if self._closed:
            raise ValueError("写入已关闭的备份文件")
        self._buf += data
        # 至少留一字节在缓冲区之外再出块：保证末块由 close() 写出时带末块标志
        while len(self._buf) > CHUNK_SIZE:
            self._emit(bytes(self._buf[:CHUNK_SIZE]), False)
            del self._buf[:CHUNK_SIZE]
        return len(data)

    def close(self) -> None:
        if self._closed:
            return
        self._emit(bytes(self._buf), True)
        self._buf.clear()
        self._closed = True

    # tarfile 的流式模式只需要 write；flush 留给调用方对底层文件做
    def flush(self) -> None:
        pass


def _read_exact(src: BinaryIO, n: int) -> bytes:
    data = b""
    while len(data) < n:
        part = src.read(n - len(data))
        if not part:
            break
        data += part
    return data


def decrypt_stream(src: BinaryIO, dst: BinaryIO, password: bytes) -> int:
    """解密 EncryptingWriter 的输出写入 dst，返回明文字节数。任何格式问题抛出 BackupFormatError。

    明文可能已部分写入 dst 后才发现错误（如截断），调用方须在出错时丢弃 dst。
    """
    header = _read_exact(src, HEADER_SIZE)
    if len(header) != HEADER_SIZE:
        raise BackupFormatError("不是本系统的备份文件")
    magic, log_n, r, p, reserved, chunk_size, salt = _HEADER.unpack(header)
    if magic != MAGIC:
        raise BackupFormatError("不是本系统的备份文件，或格式版本不支持")
    if (log_n, r, p, reserved, chunk_size) != (SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P, 0, CHUNK_SIZE):
        raise BackupFormatError("备份文件头参数不正确")
    aead = AESGCM(derive_key(password, salt))
    total = 0
    index = 0
    while True:
        head = _read_exact(src, _CHUNK_HEAD.size)
        if not head:
            raise BackupFormatError("备份文件不完整（缺少末块）")
        if len(head) != _CHUNK_HEAD.size:
            raise BackupFormatError("备份文件不完整")
        nonce, length = _CHUNK_HEAD.unpack(head)
        if length < TAG_SIZE or length > CHUNK_SIZE + TAG_SIZE:
            raise BackupFormatError("备份文件块长度不正确")
        ct = _read_exact(src, length)
        if len(ct) != length:
            raise BackupFormatError("备份文件不完整")
        plain = None
        final = False
        for flag in (False, True):
            try:
                plain = aead.decrypt(nonce, ct, _aad(header, index, flag))
                final = flag
                break
            except InvalidTag:
                continue
        if plain is None:
            raise BackupFormatError("解密失败：备份密码不正确，或文件已被篡改")
        dst.write(plain)
        total += len(plain)
        index += 1
        if final:
            break
    if src.read(1):
        raise BackupFormatError("备份文件末块之后还有多余数据")
    return total
