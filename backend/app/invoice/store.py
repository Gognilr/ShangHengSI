"""内存盘 invoice_mem（设计第五节）：原件与识别结果用主密钥加密后放在这里，不写持久卷，不进备份。

目录结构：{root}/{批次 id}/manifest.bin（文件名、扩展名与收票时的未识别）、f{序号}.bin（原件）、result.bin（识别结果）。
每个文件的关联数据带批次 id 与文件标识，不能把一批的密文挪到另一批解开。
"""
import json
import os
import shutil
import time
from pathlib import Path

from app.config import get_settings
from app.security import crypto
from app.security.errors import AppError

RESERVE_BYTES = 64 * 1024 * 1024  # 余量不足时不收新批（结果与并发的另一批要用）
MSG_BUSY = "服务器正忙，请稍后再试"


def root() -> Path:
    return Path(get_settings().invoice_mem_dir)


def batch_dir(bid: str) -> Path:
    return root() / bid


def _aad(bid: str, tag: str) -> bytes:
    return f"invoice:{bid}:{tag}".encode()


def _write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def save(bid: str, files: list[tuple[str, str, bytes]], skipped: list[dict]) -> None:
    """files：[(文件名, 扩展名, 内容)]。余量不足时抛出 503，不留半批。"""
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    need = sum(len(d) for _, _, d in files) + 1024 * 1024
    if shutil.disk_usage(base).free - need < RESERVE_BYTES:
        raise AppError(503, "INVOICE_BUSY", MSG_BUSY)
    d = batch_dir(bid)
    d.mkdir(mode=0o700)
    try:
        for i, (_, _, data) in enumerate(files):
            _write(d / f"f{i}.bin", crypto.encrypt(data, aad=_aad(bid, f"f{i}")))
        manifest = {"files": [{"name": n, "ext": e} for n, e, _ in files], "skipped": skipped}
        _write(d / "manifest.bin", crypto.encrypt(json.dumps(manifest, ensure_ascii=False).encode(), aad=_aad(bid, "m")))
    except OSError:
        clear(bid)
        raise AppError(503, "INVOICE_BUSY", MSG_BUSY) from None


def load_manifest(bid: str) -> dict | None:
    try:
        blob = (batch_dir(bid) / "manifest.bin").read_bytes()
    except OSError:
        return None
    return json.loads(crypto.decrypt(blob, aad=_aad(bid, "m")))


def load_file(bid: str, i: int) -> bytes | None:
    try:
        blob = (batch_dir(bid) / f"f{i}.bin").read_bytes()
    except OSError:
        return None
    return crypto.decrypt(blob, aad=_aad(bid, f"f{i}"))


def save_result(bid: str, result: dict) -> bool:
    """批次已被清除（目录不在）时不写，返回 False。"""
    d = batch_dir(bid)
    if not d.is_dir():
        return False
    blob = crypto.encrypt(json.dumps(result, ensure_ascii=False).encode(), aad=_aad(bid, "r"))
    tmp = d / "result.tmp"
    try:
        tmp.unlink(missing_ok=True)
        _write(tmp, blob)
        tmp.replace(d / "result.bin")
    except OSError:
        return False
    return True


def load_result(bid: str) -> dict | None:
    try:
        blob = (batch_dir(bid) / "result.bin").read_bytes()
    except OSError:
        return None
    return json.loads(crypto.decrypt(blob, aad=_aad(bid, "r")))


def drop_originals(bid: str) -> None:
    """识别完成后原件不再需要，先删掉，只留结果。"""
    for p in batch_dir(bid).glob("f*.bin"):
        p.unlink(missing_ok=True)


def clear(bid: str) -> None:
    shutil.rmtree(batch_dir(bid), ignore_errors=True)


def list_dirs() -> list[tuple[str, float]]:
    """[(批次 id, 距最后修改的秒数)]。"""
    base = root()
    if not base.is_dir():
        return []
    now = time.time()
    out = []
    for p in base.iterdir():
        try:
            out.append((p.name, now - p.stat().st_mtime))
        except OSError:
            continue
    return out
