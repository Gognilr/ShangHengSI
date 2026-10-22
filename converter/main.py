"""旧格式转换服务（SPEC 8.8，设计 docs/design/R2.md 第三节）。

在断网、只读、非 root 的 converter 容器中运行，只用 Python 标准库。
通过内存盘 /spool 与 worker 交换文件：

    in/{id}.{kind}           worker 写入的待转换文件（kind：doc / xls / rtf）
    work/{id}/               处理中（只有 converter 写）
    out/{id}.docx|.xlsx      结果
    out/{id}.err             失败，内容只有错误代码
    converter.alive          心跳，每轮循环（以及转换过程中每秒）更新 mtime

用法：
    python main.py           持续运行
    python main.py --check   心跳 30 秒内更新过返回 0，否则返回 1（容器健康检查）

日志只写 id、kind、耗时和结果代码，不写文件内容。
"""
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

SPOOL = Path(os.environ.get("CONVERT_SPOOL_DIR", "/spool"))
TMP = Path(os.environ.get("TMPDIR", "/tmp"))  # LibreOffice 的临时配置目录放在这里（tmpfs）
SOFFICE = ["soffice"]

POLL_SECONDS = 0.5
TIMEOUT_SECONDS = 60  # 单个文件的转换上限
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
STALE_SECONDS = 600  # in/ 与 out/ 中超过 10 分钟的文件（worker 已放弃）删除
ALIVE_MAX_AGE = 30  # --check 的心跳期限
ALIVE = "converter.alive"

NAME_RE = re.compile(r"\A([0-9a-f]{32})\.(doc|xls|rtf)\Z")  # \Z：不接受末尾换行
# kind → (结果扩展名, LibreOffice 导出过滤器)
FILTERS = {
    "doc": ("docx", "docx:MS Word 2007 XML"),
    "rtf": ("docx", "docx:MS Word 2007 XML"),
    "xls": ("xlsx", "xlsx:Calc MS Excel 2007 XML"),
}
ERROR_CODES = ("TIMEOUT", "FAILED", "NO_OUTPUT", "TOO_LARGE", "BAD_OUTPUT")

_stop = False


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} converter {msg}", flush=True)


def ensure_dirs(spool: Path) -> None:
    """in/、work/、out/ 设为 0770：worker（uid 10001）经同组 gid 10001 读写。"""
    for name in ("in", "work", "out"):
        d = spool / name
        d.mkdir(exist_ok=True)
        if stat.S_IMODE(d.stat().st_mode) != 0o770:
            d.chmod(0o770)


def heartbeat(spool: Path) -> None:
    (spool / ALIVE).touch(mode=0o660)


def check(spool: Path) -> int:
    try:
        age = time.time() - (spool / ALIVE).stat().st_mtime
    except OSError:
        return 1
    return 0 if age <= ALIVE_MAX_AGE else 1


def _remove(path: Path) -> None:
    try:
        if stat.S_ISDIR(path.lstat().st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        pass


def cleanup_stale(spool: Path, now: float | None = None) -> None:
    """删除 in/ 与 out/ 中超过 10 分钟的条目（含不合规的文件名、worker 未完成的 .part）。"""
    now = time.time() if now is None else now
    for name in ("in", "out"):
        for entry in os.scandir(spool / name):
            try:
                mtime = entry.stat(follow_symlinks=False).st_mtime
            except FileNotFoundError:
                continue
            if now - mtime > STALE_SECONDS:
                _remove(Path(entry.path))


def pick(spool: Path) -> str | None:
    """in/ 中最早的一个合规文件名；不合规的名称、符号链接和目录一律忽略。"""
    found = []
    for entry in os.scandir(spool / "in"):
        if not NAME_RE.match(entry.name):
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(st.st_mode):
            found.append((st.st_mtime, entry.name))
    return min(found)[1] if found else None


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def reap() -> None:
    """回收被杀掉的 LibreOffice 遗留的孤儿进程（本进程在容器中是 PID 1）。"""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def run_soffice(src: Path, outdir: Path, filt: str, profile: Path, timeout: float, on_tick=None) -> tuple[bool, int | None]:
    """执行一次 LibreOffice 转换，返回（是否超时, 退出码）。

    LibreOffice 在新的会话（进程组）中运行，超时或结束后杀掉整个进程组；
    等待期间每秒调用 on_tick（更新心跳，避免长转换期间心跳过期）。
    """
    cmd = SOFFICE + [
        f"-env:UserInstallation={profile.as_uri()}",
        "--headless", "--norestore", "--nolockcheck", "--nodefault",
        "--convert-to", filt, "--outdir", str(outdir), str(src),
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while True:
            try:
                proc.wait(timeout=min(1.0, max(0.05, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                if on_tick:
                    on_tick()
                if _stop or time.monotonic() >= deadline:
                    timed_out = True
                    _kill_group(proc)
                    proc.wait()
                    break
    finally:
        _kill_group(proc)  # 正常结束时也清掉同组的残留进程
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reap()
    return timed_out, (None if timed_out else proc.returncode)


def check_output(path: Path) -> str | None:
    """结果文件合规返回 None，否则返回错误代码。"""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return "NO_OUTPUT"
    if not stat.S_ISREG(st.st_mode):
        return "BAD_OUTPUT"
    if st.st_size > MAX_OUTPUT_BYTES:
        return "TOO_LARGE"
    with open(path, "rb") as f:
        if f.read(2) != b"PK":
            return "BAD_OUTPUT"
    return None


def _publish_error(spool: Path, file_id: str, code: str) -> None:
    part = spool / "out" / f"{file_id}.err.part"
    part.write_text(code, encoding="ascii")
    os.replace(part, spool / "out" / f"{file_id}.err")


def convert_one(spool: Path, name: str) -> str | None:
    """转换 in/ 中的一个文件，返回结果代码（OK 或错误代码）；文件已被 worker 撤回时返回 None。"""
    m = NAME_RE.match(name)
    file_id, kind = m.group(1), m.group(2)
    ext, filt = FILTERS[kind]
    work = spool / "work" / file_id
    profile = TMP / f"lo-{file_id}"
    _remove(work)
    work.mkdir()
    src = work / f"src.{kind}"
    started = time.monotonic()
    try:
        try:
            os.rename(spool / "in" / name, src)
        except FileNotFoundError:
            return None
        outdir = work / "out"
        try:
            timed_out, rc = run_soffice(src, outdir, filt, profile, TIMEOUT_SECONDS, on_tick=lambda: heartbeat(spool))
            if timed_out:
                code = "TIMEOUT"
            elif rc != 0:
                code = "FAILED"
            else:
                code = check_output(outdir / f"src.{ext}") or "OK"
            if code == "OK":
                # 同一文件系统内改名是原子操作，worker 不会看到写了一半的结果
                os.replace(outdir / f"src.{ext}", spool / "out" / f"{file_id}.{ext}")
            else:
                _publish_error(spool, file_id, code)
        except Exception as exc:  # 意外错误也要给 worker 一个明确结果，不必等到超时
            log(f"id={file_id} error={type(exc).__name__}")
            code = "FAILED"
            _publish_error(spool, file_id, code)
        log(f"id={file_id} kind={kind} ms={int((time.monotonic() - started) * 1000)} result={code}")
        return code
    finally:
        _remove(work)
        _remove(profile)


def recover(spool: Path) -> None:
    """启动时：上次中断的转换写 FAILED（worker 不必等到超时），清掉残留的工作目录与配置目录。"""
    for entry in os.scandir(spool / "work"):
        if re.fullmatch(r"[0-9a-f]{32}", entry.name):
            _publish_error(spool, entry.name, "FAILED")
        _remove(Path(entry.path))
    for entry in os.scandir(TMP):
        if entry.name.startswith("lo-"):
            _remove(Path(entry.path))


def run_once(spool: Path) -> str | None:
    """一轮循环：心跳 → 过期清理 → 取最早的一个文件转换。没有可转换的文件时返回 None。"""
    heartbeat(spool)
    cleanup_stale(spool)
    name = pick(spool)
    return convert_one(spool, name) if name else None


def _on_term(signum, frame) -> None:
    global _stop
    _stop = True


def serve(spool: Path) -> None:
    os.umask(0o007)  # 目录 0770、文件 0660：同组的 worker 可读、可删
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    ensure_dirs(spool)
    recover(spool)
    log("started")
    while not _stop:
        try:
            if run_once(spool) is None:
                time.sleep(POLL_SECONDS)
        except Exception as exc:
            log(f"loop error={type(exc).__name__}")
            time.sleep(POLL_SECONDS)
    log("stopped")


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        return check(SPOOL)
    if argv:
        print("用法：main.py [--check]", file=sys.stderr)
        return 2
    serve(SPOOL)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
