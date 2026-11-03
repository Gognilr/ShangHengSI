"""主密钥加密（SPEC 17.3）。

用主密钥以 AES-256-GCM 加密原件文件、对照表、节点原文等敏感字段。
密文格式：版本(1 字节) + 随机数(12 字节) + 密文 + 标签(16 字节)。每次加密使用新的随机数。

主密钥文件必须恰好 32 字节的原始二进制（open_questions Q-08），不符合时拒绝启动。
主密钥、明文都不得出现在日志、异常消息中。
"""
import base64
import os
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings, read_secret_file

KEY_SIZE = 32
NONCE_SIZE = 12
TAG_SIZE = 16
FORMAT_VERSION = 1
_HEADER_SIZE = 1 + NONCE_SIZE
MIN_BLOB_SIZE = _HEADER_SIZE + TAG_SIZE


class DecryptionError(Exception):
    """密文格式错误、被篡改或密钥不对。消息不含任何密文或明文内容。"""


def load_master_key(path: str) -> bytes:
    """读取主密钥文件并校验长度。"""
    key = read_secret_file(path)
    if len(key) != KEY_SIZE:
        # 只报告长度不符，不带出文件内容
        raise RuntimeError(f"主密钥文件必须恰好 {KEY_SIZE} 字节")
    return key


@lru_cache
def get_master_key() -> bytes:
    return load_master_key(get_settings().master_key_file)


def check_master_key() -> None:
    """启动时调用：主密钥缺失或长度不对时抛出异常，阻止 app 和 worker 启动。"""
    get_master_key()


def encrypt(plaintext: bytes, *, key: bytes | None = None, aad: bytes | None = None) -> bytes:
    """加密字节串。aad 为可选的关联数据，解密时必须提供相同的值。"""
    k = key if key is not None else get_master_key()
    nonce = os.urandom(NONCE_SIZE)
    # AESGCM.encrypt 返回 密文 + 16 字节标签
    return bytes([FORMAT_VERSION]) + nonce + AESGCM(k).encrypt(nonce, plaintext, aad)


def decrypt(blob: bytes, *, key: bytes | None = None, aad: bytes | None = None) -> bytes:
    """解密 encrypt 的输出。格式不对、被篡改或密钥不对时抛出 DecryptionError。"""
    if len(blob) < MIN_BLOB_SIZE:
        raise DecryptionError("密文长度不正确")
    if blob[0] != FORMAT_VERSION:
        raise DecryptionError("不支持的密文格式版本")
    k = key if key is not None else get_master_key()
    nonce = blob[1:_HEADER_SIZE]
    try:
        return AESGCM(k).decrypt(nonce, blob[_HEADER_SIZE:], aad)
    except InvalidTag:
        raise DecryptionError("解密失败：密文被篡改或密钥不正确") from None


def encrypt_text(text: str, *, key: bytes | None = None, aad: bytes | None = None) -> bytes:
    """加密字符串（UTF-8），用于 _enc 结尾的 bytea 字段。"""
    return encrypt(text.encode("utf-8"), key=key, aad=aad)


def decrypt_text(blob: bytes, *, key: bytes | None = None, aad: bytes | None = None) -> str:
    return decrypt(blob, key=key, aad=aad).decode("utf-8")


def encrypt_to_b64(text: str, *, key: bytes | None = None) -> str:
    """加密字符串并编码为 base64 文本，用于存入 JSON 的值（如 settings 中的 model.api_key_enc）。"""
    return base64.b64encode(encrypt_text(text, key=key)).decode("ascii")


def decrypt_from_b64(value: str, *, key: bytes | None = None) -> str:
    try:
        blob = base64.b64decode(value, validate=True)
    except ValueError:
        raise DecryptionError("密文不是合法的 base64") from None
    return decrypt_text(blob, key=key)


def encrypt_to_file(path: str | Path, data: bytes, *, key: bytes | None = None) -> None:
    """加密后写入文件（原件存储）。先写临时文件、fsync，再原子替换，避免留下半个文件。"""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    blob = encrypt(data, key=key)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def decrypt_file(path: str | Path, *, key: bytes | None = None) -> bytes:
    return decrypt(Path(path).read_bytes(), key=key)


# 录音流格式独立于既有整文件格式；随机流标识同时绑定全部块，防止跨文件拼接。
STREAM_MAGIC = b"SIASR02\x00"
STREAM_CHUNK = 4 * 1024 * 1024


def encrypt_stream(path, chunks, *, key=None):
    """逐块加密并原子保存；空输入也写一个认证的末块。"""
    import struct
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    header = STREAM_MAGIC + os.urandom(16)
    aead = AESGCM(key if key is not None else get_master_key())
    try:
        with open(tmp, "wb") as out:
            os.chmod(tmp, 0o600)
            out.write(header)
            index = 0
            # 不预读下一块：显式空末块使每次只保留一块明文。
            for chunk in chunks:
                if not chunk:
                    continue
                if len(chunk) > STREAM_CHUNK:
                    raise ValueError("录音块过大")
                nonce = os.urandom(NONCE_SIZE)
                aad = header + struct.pack(">QB", index, 0)
                blob = aead.encrypt(nonce, chunk, aad)
                out.write(struct.pack(">IB", len(blob), 0) + nonce + blob)
                index += 1
            nonce = os.urandom(NONCE_SIZE)
            blob = aead.encrypt(nonce, b"", header + struct.pack(">QB", index, 1))
            out.write(struct.pack(">IB", len(blob), 1) + nonce + blob)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def decrypt_stream(path, *, key=None):
    """迭代解密；调用方须读完才可发布结果，失败须删除已写明文。"""
    import struct
    aead = AESGCM(key if key is not None else get_master_key())
    with open(path, "rb") as src:
        header = src.read(len(STREAM_MAGIC) + 16)
        if len(header) != len(STREAM_MAGIC) + 16 or not header.startswith(STREAM_MAGIC):
            raise DecryptionError("录音密文格式错误")
        index = 0
        while True:
            record = src.read(5)
            if len(record) != 5:
                raise DecryptionError("录音密文不完整")
            size, final = struct.unpack(">IB", record)
            if final not in (0, 1) or not TAG_SIZE <= size <= STREAM_CHUNK + TAG_SIZE:
                raise DecryptionError("录音密文格式错误")
            nonce, blob = src.read(NONCE_SIZE), src.read(size)
            if len(nonce) != NONCE_SIZE or len(blob) != size:
                raise DecryptionError("录音密文不完整")
            try:
                plain = aead.decrypt(nonce, blob, header + struct.pack(">QB", index, final))
            except InvalidTag:
                raise DecryptionError("录音解密失败") from None
            if final:
                if plain or src.read(1):
                    raise DecryptionError("录音密文格式错误")
                return
            yield plain
            index += 1


def is_stream(path):
    with open(path, "rb") as src:
        return src.read(len(STREAM_MAGIC)) == STREAM_MAGIC
