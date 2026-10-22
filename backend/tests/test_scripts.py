"""部署脚本（设计 docs/design/M8.md 第二节、3.6）：语法检查、通用约定与 lib.sh 公共函数。

需要看到仓库根目录下的 deploy/：测试时把仓库根目录挂到 /srv（与 test_forbidden_words 相同，open_questions Q-116）。
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SCRIPTS = DEPLOY / "scripts"


def _scripts() -> list[Path]:
    found = sorted(SCRIPTS.glob("*.sh"))
    assert found, f"未找到 {SCRIPTS}/*.sh；测试须挂载整个仓库"
    return found


def test_bash_syntax():
    for path in _scripts():
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, f"{path.name}: {r.stderr}"


def test_conventions():
    for path in _scripts():
        text = path.read_text(encoding="utf-8")
        assert "\r" not in text, f"{path.name} 含 CRLF"
        assert "set -x" not in text and "set -o xtrace" not in text, path.name
        if path.name != "lib.sh":
            lines = text.splitlines()
            assert lines[0] == "#!/usr/bin/env bash", path.name
            assert "set -Eeuo pipefail" in lines[:12], path.name
            assert 'ROOT="$(cd "$(dirname "$0")/.." && pwd)"' in text, path.name
            assert 'source "$ROOT/scripts/lib.sh"' in text, path.name


def _lib(script: str, *, root: Path, env: dict | None = None, stdin: str = "") -> subprocess.CompletedProcess:
    """在 bash 中 source lib.sh 后执行一段命令。"""
    full_env = {"PATH": os.environ["PATH"], "ROOT": str(root), **(env or {})}
    code = f'set -Eeuo pipefail\nsource "{SCRIPTS / "lib.sh"}"\n{script}\n'
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, env=full_env, input=stdin)


def test_env_get_reads_without_executing(tmp_path):
    marker = tmp_path / "executed"
    (tmp_path / ".env").write_text(
        "# 注释\nPOSTGRES_PASSWORD=first\nBACKUP_HOST_DIR=\"/data/apps/aireview/backup\"\r\n"
        f"EVIL=$(touch {marker})\nPOSTGRES_PASSWORD=second\nQUOTED='a b'\nEMPTY=\n",
        encoding="utf-8")
    r = _lib('printf "[%s]" "$(env_get POSTGRES_PASSWORD)" "$(env_get BACKUP_HOST_DIR)" "$(env_get QUOTED)" '
             '"$(env_get EMPTY)" "$(env_get MISSING)" "$(env_get EVIL)"', root=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"[second][/data/apps/aireview/backup][a b][][][$(touch {marker})]"
    assert not marker.exists()
    # 没有 .env 时返回空
    r = _lib('printf "[%s]" "$(env_get X)"', root=tmp_path / "none")
    assert r.stdout == "[]"


def test_die_info_and_confirm(tmp_path):
    r = _lib('info "你好"; die "出错了"; info "不会执行"', root=tmp_path)
    assert r.returncode == 1 and r.stdout == "你好\n" and r.stderr == "错误：出错了\n"
    r = _lib('confirm "将要恢复" "确认恢复"; info DONE', root=tmp_path, stdin="确认恢复\n")
    assert r.returncode == 0 and r.stdout.endswith("DONE\n")
    for answer in ("确认\n", "yes\n", ""):
        r = _lib('confirm "将要恢复" "确认恢复"; info DONE', root=tmp_path, stdin=answer)
        assert r.returncode == 1 and "未确认，已取消" in r.stderr and "DONE" not in r.stdout
    r = _lib('confirm "将要恢复" "确认恢复"; info DONE', root=tmp_path, env={"ASSUME_YES": "1"})
    assert r.returncode == 0 and "已跳过确认" in r.stdout


def test_require_cmd_and_resolve_path(tmp_path):
    r = _lib("require_cmd bash no-such-command-xyz", root=tmp_path)
    assert r.returncode == 1 and "缺少命令 no-such-command-xyz" in r.stderr
    r = _lib('printf "%s|%s|%s" "$(resolve_path ./backup)" "$(resolve_path backup)" "$(resolve_path /data/b)"',
             root=tmp_path)
    assert r.stdout == f"{tmp_path}/backup|{tmp_path}/backup|/data/b"


def test_require_on_data_disk():
    r = _lib('require_on_data_disk /usr/bin', root=ROOT)
    assert r.returncode == 1 and "位于根分区上" in r.stderr
    # 不存在的路径按最近的已存在上级目录判断
    r = _lib('require_on_data_disk /no/such/dir', root=ROOT)
    assert r.returncode == 1 and "位于根分区上" in r.stderr
    target = subprocess.run(["findmnt", "-n", "-o", "TARGET", "--target", str(ROOT)],
                            capture_output=True, text=True).stdout.strip()
    if target == "/":
        pytest.skip("仓库目录不是单独挂载的文件系统")
    r = _lib(f'require_on_data_disk "{ROOT}/no-such-subdir"; info ok', root=ROOT)
    assert r.returncode == 0 and r.stdout == "ok\n", r.stderr


def test_containerd_root(tmp_path):
    cfg = tmp_path / "config.toml"
    cases = {
        "version = 2\nroot = \"/data/containerd\"\nstate = \"/run/containerd\"\n": "/data/containerd",
        "root = '/data/ctr'  # 注释\n": "/data/ctr",
        "version = 2\n[plugins]\n  root = \"/not/top/level\"\n": "/var/lib/containerd",
        "": "/var/lib/containerd",
    }
    for content, expected in cases.items():
        cfg.write_text(content, encoding="utf-8")
        r = _lib('containerd_root', root=tmp_path, env={"CONTAINERD_CONFIG": str(cfg)})
        assert r.stdout == expected, content
    r = _lib('containerd_root', root=tmp_path, env={"CONTAINERD_CONFIG": str(tmp_path / "missing.toml")})
    assert r.stdout == "/var/lib/containerd"


def test_restore_usage_and_arguments():
    script = str(SCRIPTS / "restore.sh")
    r = subprocess.run(["bash", script, "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "用法：sudo ./scripts/restore.sh" in r.stdout
    r = subprocess.run(["bash", script, "--bad"], capture_output=True, text=True)
    assert r.returncode == 1 and "未知参数 --bad" in r.stderr
    r = subprocess.run(["bash", script], capture_output=True, text=True)
    assert r.returncode == 1 and "用法" in r.stderr
    r = subprocess.run(["bash", script, "a.bak", "b.bak"], capture_output=True, text=True)
    assert r.returncode == 1 and "只能指定一个备份文件" in r.stderr
