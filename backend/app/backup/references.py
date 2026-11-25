"""原件引用按备份快照中的 task_files 行判定，不查询正在变化的现用库。"""
import os
import re
import subprocess
from pathlib import Path


class ReferenceError(Exception):
    """数据库归档中的原件引用无法读取；消息不含行内容。"""


_COPY = re.compile(rb"COPY public\.task_files \(([^)]+)\) FROM stdin;")
_NULL = rb"\N"


def file_keys(storage_key, media_key, converted_key) -> set:
    """回放副本可替代已删除的录音原件；已登记的转换件仍是必需文件。"""
    keys = {media_key or storage_key}
    if converted_key:
        keys.add(converted_key)
    return keys


def _copy_keys(lines) -> set[str]:
    for line in lines:
        matched = _COPY.fullmatch(line.rstrip(b"\r\n"))
        if matched:
            break
    else:
        raise ReferenceError("数据库导出文件缺少原件引用表")

    columns = matched[1].split(b", ")
    if b"storage_key" not in columns:
        raise ReferenceError("数据库导出文件缺少原件引用列")
    positions = {name: i for i, name in enumerate(columns)}
    keys = set()
    for line in lines:
        line = line.rstrip(b"\r\n")
        if line == rb"\.":
            return keys
        values = line.split(b"\t")
        if len(values) != len(columns):
            raise ReferenceError("数据库导出文件中的原件引用行不完整")

        def value(name):
            index = positions.get(name)
            return values[index] if index is not None else _NULL

        if value(b"original_deleted_at") != _NULL:
            continue
        media = value(b"media_storage_key")
        converted = value(b"converted_storage_key")
        raw_keys = file_keys(value(b"storage_key"), None if media == _NULL else media,
                             None if converted == _NULL else converted)
        for raw in raw_keys:
            if not re.fullmatch(rb"[0-9a-f]{32}", raw):
                raise ReferenceError("数据库导出文件中的原件引用无效")
            keys.add(raw.decode("ascii"))
    raise ReferenceError("数据库导出文件中的原件引用表不完整")


def dump_file_keys(dump: Path) -> set[str]:
    """只读取 pg_restore 输出的 COPY 行。制表符和换行在列值内由 PostgreSQL 转义。"""
    try:
        with subprocess.Popen(
            ["pg_restore", "--data-only", "--schema=public", "--table=task_files", "-f", "-", str(dump)],
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ) as process:
            keys = _copy_keys(process.stdout)
            for _ in process.stdout:
                pass
            if process.wait() != 0:
                raise ReferenceError("无法读取数据库导出文件中的原件引用")
            return keys
    except FileNotFoundError:
        raise ReferenceError("未找到 pg_restore") from None
