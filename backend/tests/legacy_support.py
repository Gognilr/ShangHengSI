"""旧格式转换的测试替身：用线程模拟 converter 容器读写 /spool（设计 docs/design/R2.md 3.3），不需要 LibreOffice。"""
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from app.config import get_settings

LEGACY = Path(__file__).resolve().parent / "fixtures" / "legacy"
NAME_RE = re.compile(r"\A([0-9a-f]{32})\.(doc|xls|rtf)\Z")


def manifest() -> dict:
    return yaml.safe_load((LEGACY / "manifest.yaml").read_text(encoding="utf-8"))["samples"]


def make_spool(path: Path) -> Path:
    for name in ("in", "work", "out"):
        (path / name).mkdir(parents=True, exist_ok=True)
    return path


def beat(spool: Path) -> None:
    (spool / "converter.alive").touch()


@contextmanager
def fake_converter(spool: Path, handler):
    """线程每 0.02 秒取 in/ 中的文件，handler(kind, data) 返回 (结果扩展名或 "err", 字节或错误代码)；返回 None 表示不响应。"""
    stop = threading.Event()
    seen: list[tuple[str, bytes]] = []

    def run() -> None:
        while not stop.is_set():
            beat(spool)
            for p in sorted((spool / "in").iterdir()):
                m = NAME_RE.match(p.name)
                if not m:
                    continue
                data = p.read_bytes()
                p.unlink()
                seen.append((m.group(2), data))
                reply = handler(m.group(2), data)
                if reply is None:
                    continue
                ext, body = reply
                out = spool / "out" / f"{m.group(1)}.{ext}"
                part = out.with_name(out.name + ".part")
                part.write_bytes(body if isinstance(body, bytes) else body.encode("ascii"))
                os.replace(part, out)
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        yield seen
    finally:
        stop.set()
        t.join(5)


def replay_handler():
    """按原件字节找到 manifest 中的样本，返回 converter 转回的 DOCX / XLSX。"""
    table = {(LEGACY / v["file"]).read_bytes(): v for v in manifest().values()}

    def handler(kind, data):
        v = table.get(data)
        if v is None:
            return "err", "FAILED"
        return ("docx" if v["parse_as"] == "docx" else "xlsx"), (LEGACY / v["converted"]).read_bytes()

    return handler


@pytest.fixture
def spool(tmp_path, monkeypatch):
    path = make_spool(tmp_path / "spool")
    monkeypatch.setenv("CONVERT_SPOOL_DIR", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()
