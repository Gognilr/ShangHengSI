"""converter 测试（设计 docs/design/R2.md 3.3、第四节）。只用标准库 unittest，在 converter 镜像中运行：

    docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
      -v "${PWD}/converter/tests:/srv/converter/tests:ro" \
      -v "${PWD}/backend/tests/fixtures/legacy:/srv/fixtures:ro" \
      aireview-converter:dev python -m unittest discover -s /srv/converter/tests

LibreOffice 的行为（超时、失败、输出异常）用假的 soffice 脚本模拟；真实转换用 /srv/fixtures 中的虚构样本。
ContainerTests 断言运行条件与 compose 中的加固一致（断网、只读、非 root、无 capability）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402

FIXTURES = Path(os.environ.get("LEGACY_FIXTURES_DIR", "/srv/fixtures"))

# 假 soffice：按参数找到 --outdir 和 --convert-to，按 MODE 行事
FAKE = r'''
import os, sys, time, subprocess
args = sys.argv[1:]
outdir = args[args.index("--outdir") + 1]
ext = args[args.index("--convert-to") + 1].split(":")[0]
src = args[-1]
os.makedirs(outdir, exist_ok=True)
out = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + "." + ext)
mode = MODE
if mode == "ok":
    open(out, "wb").write(b"PK\x03\x04fake")
elif mode == "fail":
    sys.exit(1)
elif mode == "fail_with_output":
    open(out, "wb").write(b"PK\x03\x04fake")
    sys.exit(81)
elif mode == "noout":
    sys.exit(0)
elif mode == "notzip":
    open(out, "wb").write(b"hello, not a zip")
elif mode == "big":
    open(out, "wb").write(b"PK" + b"0" * 2048)
elif mode == "symlink":
    os.symlink("/etc/passwd", out)
elif mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(PIDFILE, "w").write(str(child.pid))
    time.sleep(60)
'''


def _alive(pid: int) -> bool:
    """进程存在且不是僵尸。"""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


class SpoolCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="convtest-"))
        self.spool = self.tmp / "spool"
        self.spool.mkdir()
        main.ensure_dirs(self.spool)
        self.lo_tmp = self.tmp / "lo"
        self.lo_tmp.mkdir()
        self._saved = (main.SOFFICE, main.TIMEOUT_SECONDS, main.MAX_OUTPUT_BYTES, main.TMP)
        main.TMP = self.lo_tmp

    def tearDown(self):
        main.SOFFICE, main.TIMEOUT_SECONDS, main.MAX_OUTPUT_BYTES, main.TMP = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake(self, mode: str) -> None:
        script = self.tmp / f"fake_{mode}.py"
        pidfile = self.tmp / "child.pid"
        script.write_text(f"MODE = {mode!r}\nPIDFILE = {str(pidfile)!r}\n" + FAKE, encoding="utf-8")
        main.SOFFICE = [sys.executable, str(script)]

    def put(self, kind: str, data: bytes = b"\xd0\xcf\x11\xe0fictional", name: str | None = None) -> str:
        file_id = uuid.uuid4().hex
        path = self.spool / "in" / (name or f"{file_id}.{kind}")
        path.write_bytes(data)
        return file_id

    def out_names(self) -> set[str]:
        return {p.name for p in (self.spool / "out").iterdir()}

    def assert_clean(self, file_id: str) -> None:
        self.assertFalse((self.spool / "work" / file_id).exists())
        self.assertFalse((self.lo_tmp / f"lo-{file_id}").exists())
        self.assertEqual(list((self.spool / "in").iterdir()), [])


class ProtocolTests(SpoolCase):
    def test_success_doc_rtf_xls(self):
        self.fake("ok")
        for kind, ext in (("doc", "docx"), ("rtf", "docx"), ("xls", "xlsx")):
            file_id = self.put(kind)
            self.assertEqual(main.run_once(self.spool), "OK")
            self.assertIn(f"{file_id}.{ext}", self.out_names())
            self.assertEqual((self.spool / "out" / f"{file_id}.{ext}").read_bytes()[:2], b"PK")
            self.assert_clean(file_id)
        self.assertFalse(any(n.endswith(".part") or n.endswith(".err") for n in self.out_names()))

    def test_oldest_first_one_at_a_time(self):
        self.fake("ok")
        first = self.put("doc")
        os.utime(self.spool / "in" / f"{first}.doc", (time.time() - 5, time.time() - 5))
        second = self.put("xls")
        self.assertEqual(main.run_once(self.spool), "OK")
        self.assertEqual(self.out_names(), {f"{first}.docx"})
        self.assertTrue((self.spool / "in" / f"{second}.xls").exists())
        self.assertEqual(main.run_once(self.spool), "OK")
        self.assertEqual(self.out_names(), {f"{first}.docx", f"{second}.xlsx"})
        self.assertIsNone(main.run_once(self.spool))

    def _assert_error(self, code: str) -> None:
        file_id = self.put("doc")
        self.assertEqual(main.run_once(self.spool), code)
        self.assertEqual(self.out_names(), {f"{file_id}.err"})
        self.assertEqual((self.spool / "out" / f"{file_id}.err").read_text(encoding="ascii"), code)
        self.assert_clean(file_id)

    def test_failed(self):
        self.fake("fail")
        self._assert_error("FAILED")

    def test_nonzero_exit_with_output_is_failed(self):
        self.fake("fail_with_output")
        self._assert_error("FAILED")

    def test_no_output(self):
        self.fake("noout")
        self._assert_error("NO_OUTPUT")

    def test_not_zip(self):
        self.fake("notzip")
        self._assert_error("BAD_OUTPUT")

    def test_symlink_output(self):
        self.fake("symlink")
        self._assert_error("BAD_OUTPUT")

    def test_too_large(self):
        self.fake("big")
        main.MAX_OUTPUT_BYTES = 1024
        self._assert_error("TOO_LARGE")

    def test_output_at_limit_is_ok(self):
        self.fake("big")
        main.MAX_OUTPUT_BYTES = 2050
        file_id = self.put("doc")
        self.assertEqual(main.run_once(self.spool), "OK")
        self.assertEqual(self.out_names(), {f"{file_id}.docx"})

    def test_timeout_kills_process_group(self):
        self.fake("hang")
        main.TIMEOUT_SECONDS = 2
        started = time.monotonic()
        self._assert_error("TIMEOUT")
        self.assertLess(time.monotonic() - started, 10)
        child = int((self.tmp / "child.pid").read_text())
        self.assertFalse(_alive(child), "LibreOffice 的子进程应随进程组一起被杀掉")

    def test_illegal_names_ignored_then_removed_when_stale(self):
        self.fake("ok")
        file_id = uuid.uuid4().hex
        names = [
            f"{file_id.upper()}.doc",  # 大写
            f"{file_id}.docx",  # 不在 kind 范围
            f"{file_id}.wps",  # wps 须由 worker 按 doc 交来
            f"{file_id}.doc.part",  # worker 尚未写完
            f"{file_id[:31]}.doc",  # 长度不对
            "x.doc",
            f"{file_id}.DOC",
            f"{file_id}.doc ",
            f"{file_id}.doc\n",  # 末尾换行
        ]
        for n in names:
            self.put("doc", name=n)
        os.symlink("/etc/passwd", self.spool / "in" / f"{uuid.uuid4().hex}.doc")  # 符号链接
        (self.spool / "in" / f"{uuid.uuid4().hex}.xls").mkdir()  # 目录
        before = sorted(p.name for p in (self.spool / "in").iterdir())
        self.assertIsNone(main.run_once(self.spool))
        self.assertEqual(sorted(p.name for p in (self.spool / "in").iterdir()), before)
        self.assertEqual(self.out_names(), set())
        old = time.time() - main.STALE_SECONDS - 5
        for p in (self.spool / "in").iterdir():
            os.utime(p, (old, old), follow_symlinks=False)
        self.assertIsNone(main.run_once(self.spool))
        self.assertEqual(list((self.spool / "in").iterdir()), [])
        self.assertTrue(Path("/etc/passwd").exists())

    def test_stale_cleanup(self):
        self.fake("ok")
        old = time.time() - main.STALE_SECONDS - 5
        stale_in = self.put("doc")
        os.utime(self.spool / "in" / f"{stale_in}.doc", (old, old))
        for name in ("a" * 32 + ".docx", "b" * 32 + ".err", "c" * 32 + ".xlsx.part"):
            p = self.spool / "out" / name
            p.write_bytes(b"PK")
            os.utime(p, (old, old))
        fresh = self.spool / "out" / ("d" * 32 + ".docx")
        fresh.write_bytes(b"PK")
        self.assertIsNone(main.run_once(self.spool), "过期的待转换文件直接删除，不再转换")
        self.assertEqual(self.out_names(), {fresh.name})
        self.assertEqual(list((self.spool / "in").iterdir()), [])

    def test_input_withdrawn(self):
        self.fake("ok")
        file_id = uuid.uuid4().hex
        self.assertIsNone(main.convert_one(self.spool, f"{file_id}.doc"))
        self.assertFalse((self.spool / "work" / file_id).exists())
        self.assertEqual(self.out_names(), set())

    def test_recover_interrupted_work(self):
        file_id = uuid.uuid4().hex
        (self.spool / "work" / file_id / "out").mkdir(parents=True)
        (self.spool / "work" / "junk").mkdir()
        (self.lo_tmp / f"lo-{file_id}").mkdir()
        main.recover(self.spool)
        self.assertEqual(list((self.spool / "work").iterdir()), [])
        self.assertEqual(list(self.lo_tmp.iterdir()), [])
        self.assertEqual((self.spool / "out" / f"{file_id}.err").read_text(encoding="ascii"), "FAILED")

    def test_dir_modes(self):
        for name in ("in", "work", "out"):
            (self.spool / name).chmod(0o700)
        main.ensure_dirs(self.spool)
        for name in ("in", "work", "out"):
            self.assertEqual((self.spool / name).stat().st_mode & 0o777, 0o770)


class HeartbeatTests(SpoolCase):
    def test_each_loop_updates_heartbeat(self):
        alive = self.spool / main.ALIVE
        self.assertFalse(alive.exists())
        main.run_once(self.spool)
        self.assertLess(time.time() - alive.stat().st_mtime, 5)
        old = time.time() - 100
        os.utime(alive, (old, old))
        main.run_once(self.spool)
        self.assertLess(time.time() - alive.stat().st_mtime, 5)

    def test_heartbeat_during_conversion(self):
        self.fake("hang")
        ticks = []
        src = self.tmp / "src.doc"
        src.write_bytes(b"x")
        timed_out, rc = main.run_soffice(src, self.tmp / "o", "docx:MS Word 2007 XML", self.lo_tmp / "p", 3.2, on_tick=lambda: ticks.append(1))
        self.assertTrue(timed_out)
        self.assertIsNone(rc)
        self.assertGreaterEqual(len(ticks), 2)

    def test_check(self):
        self.assertEqual(main.check(self.spool), 1, "没有心跳文件")
        main.heartbeat(self.spool)
        self.assertEqual(main.check(self.spool), 0)
        old = time.time() - main.ALIVE_MAX_AGE - 1
        os.utime(self.spool / main.ALIVE, (old, old))
        self.assertEqual(main.check(self.spool), 1, "心跳过期")

    def test_check_cli(self):
        env = dict(os.environ, CONVERT_SPOOL_DIR=str(self.spool))
        cmd = [sys.executable, str(Path(main.__file__)), "--check"]
        self.assertEqual(subprocess.run(cmd, env=env).returncode, 1)
        main.heartbeat(self.spool)
        self.assertEqual(subprocess.run(cmd, env=env).returncode, 0)
        old = time.time() - main.ALIVE_MAX_AGE - 1
        os.utime(self.spool / main.ALIVE, (old, old))
        self.assertEqual(subprocess.run(cmd, env=env).returncode, 1)
        self.assertEqual(subprocess.run([sys.executable, str(Path(main.__file__)), "--bogus"], env=env, stderr=subprocess.DEVNULL).returncode, 2)


@unittest.skipUnless(FIXTURES.is_dir(), "未挂载 /srv/fixtures")
class RealConversionTests(SpoolCase):
    """真实 LibreOffice 转换虚构样本：wps 按 doc、et 按 xls 交给 converter（设计 3.3）。"""

    KIND = {".doc": "doc", ".wps": "doc", ".rtf": "rtf", ".xls": "xls", ".et": "xls"}

    def test_fixtures(self):
        samples = sorted(p for p in FIXTURES.iterdir() if p.suffix in self.KIND)
        self.assertGreaterEqual({p.suffix for p in samples}, set(self.KIND))
        for p in samples:
            with self.subTest(sample=p.name):
                kind = self.KIND[p.suffix]
                file_id = self.put(kind, p.read_bytes())
                started = time.monotonic()
                self.assertEqual(main.run_once(self.spool), "OK")
                self.assertLess(time.monotonic() - started, 30)
                ext = main.FILTERS[kind][0]
                out = self.spool / "out" / f"{file_id}.{ext}"
                with zipfile.ZipFile(out) as z:
                    names = set(z.namelist())
                self.assertIn("word/document.xml" if ext == "docx" else "xl/workbook.xml", names)
                self.assertFalse(any("vbaProject" in n for n in names))
                out.unlink()
                self.assert_clean(file_id)

    def test_garbage_input_does_not_hang(self):
        # 不是合法 OLE 的内容：LibreOffice 可能按纯文本导入，也可能失败；只要求在时限内给出明确结果
        file_id = self.put("doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + os.urandom(4096))
        code = main.run_once(self.spool)
        self.assertIn(code, ("OK",) + main.ERROR_CODES)
        self.assert_clean(file_id)


class ContainerTests(unittest.TestCase):
    """运行条件：只有 lo、根文件系统只读、uid 10002 / gid 10001、无 capability、no-new-privileges。"""

    def test_only_loopback(self):
        self.assertEqual(sorted(os.listdir("/sys/class/net")), ["lo"])

    def test_user(self):
        self.assertEqual((os.getuid(), os.getgid()), (10002, 10001))

    def test_root_fs_read_only(self):
        for d in ("/", "/srv/converter", "/usr/lib/libreoffice", "/etc"):
            with self.subTest(dir=d), self.assertRaises(OSError):
                with tempfile.NamedTemporaryFile(dir=d):
                    pass

    def test_no_capabilities_no_new_privs(self):
        status = dict(line.split(":\t", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":\t" in line)
        self.assertEqual(int(status["CapEff"], 16), 0)
        self.assertEqual(int(status["CapPrm"], 16), 0)
        self.assertEqual(status["NoNewPrivs"].strip(), "1")

    def test_libreoffice_version_pinned(self):
        out = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", "libreoffice-writer-nogui", "libreoffice-calc-nogui"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertEqual(out, "4:25.2.3-2+deb13u8" * 2)

    def test_no_java_no_uno(self):
        for pkg in ("python3-uno", "default-jre-headless", "libreoffice-java-common"):
            r = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", pkg], capture_output=True, text=True)
            self.assertFalse(r.stdout.startswith("ii"), pkg)  # 未知（返回非 0）或 un（未安装）


if __name__ == "__main__":
    unittest.main()
