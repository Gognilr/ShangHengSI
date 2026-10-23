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


def test_restore_container_commands_do_not_read_stdin(tmp_path):
    """服务器实跑发现：docker compose run -T 会读走标准输入，第 5 步确认随之失败。"""
    text = (SCRIPTS / "restore.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "compose run" in line or "compose exec" in line:
            assert line.rstrip().endswith("</dev/null"), line
    # 假的 docker 像真实的 docker 一样读完标准输入
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "docker"
    fake.write_text("#!/usr/bin/env bash\ncat >/dev/null\n", encoding="utf-8")
    fake.chmod(0o755)
    funcs = subprocess.run(["sed", "-n", "/^worker_cli()/,/^}/p;/^app_cli()/,/^}/p", str(SCRIPTS / "restore.sh")],
                           capture_output=True, text=True, check=True).stdout
    assert "worker_cli()" in funcs and "app_cli()" in funcs
    r = _lib(f'{funcs}\nworker_cli decrypt-backup\napp_cli restore-files --verify\n'
             'confirm "将要恢复" "确认恢复"; info DONE',
             root=tmp_path, env={"PATH": f"{bindir}:{os.environ['PATH']}"}, stdin="确认恢复\n")
    assert r.returncode == 0 and r.stdout.endswith("DONE\n"), r.stderr


def test_m8_configuration():
    import yaml

    text = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    services = config["services"]
    assert set(services) == {"db", "app", "worker", "converter", "web"}
    for name, service in services.items():
        assert service["logging"] == {"driver": "json-file", "options": {"max-size": "50m", "max-file": "5"}}, name
        assert bool(service.get("ports")) == (name == "web")
    assert "./certs:/etc/caddy/certs:ro" in services["web"]["volumes"]
    # 同一标签只有 app 构建，避免 app / worker 并行导出的竞态（Q-150）。
    assert "build" not in config["x-backend"] and "build" not in services["worker"]
    assert services["app"]["build"]["context"] == "../backend"
    release_lines = []
    inside = False
    for line in text.splitlines():
        if line.strip() == "# build-begin":
            assert not inside
            inside = True
        elif line.strip() == "# build-end":
            assert inside
            inside = False
        elif not inside:
            release_lines.append(line)
    assert not inside and text.count("# build-begin") == 3
    release = yaml.safe_load("\n".join(release_lines))
    assert all("build" not in service for service in release["services"].values())
    assert "default_sni {$SITE_ADDRESS:localhost}" in (DEPLOY / "Caddyfile").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["install.sh", "upgrade.sh", "backup_now.sh"])
def test_m8_usage_and_unknown_arguments(name):
    for arg, expected in [("--help", 0), ("--unknown", 1)]:
        r = subprocess.run(["bash", str(SCRIPTS / name), arg], capture_output=True, text=True)
        assert r.returncode == expected, r.stderr
        assert ("用法" in r.stdout) if expected == 0 else ("未知参数" in r.stderr)


def test_m8_secret_output_and_stdin():
    import re

    for name in ("install.sh", "upgrade.sh", "backup_now.sh", "lib.sh"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert not re.search(r"(?:echo|printf|info)\s+[^\n]*\$\{?(?:POSTGRES_PASSWORD|APP_MASTER_KEY|BACKUP_PASSWORD|API_KEY)", text)
        for line in text.splitlines():
            if line.startswith(("compose exec ", "compose up -d", "docker run ")):
                assert line.rstrip().endswith("</dev/null"), line


def test_m8_env_set_preserves_other_lines_and_does_not_execute(tmp_path):
    marker = tmp_path / "executed"
    original = "# 保留\nPOSTGRES_PASSWORD=private\nAPP_VERSION=old\nOTHER=a b\nAPP_VERSION=duplicate\n"
    (tmp_path / ".env").write_text(original, encoding="utf-8")
    value = f"$(touch {marker})&\\literal"
    r = _lib('env_set APP_VERSION "$NEW_VERSION"', root=tmp_path, env={"NEW_VERSION": value})
    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == f"# 保留\nPOSTGRES_PASSWORD=private\nAPP_VERSION={value}\nOTHER=a b\n"
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    assert "private" not in r.stdout + r.stderr


def test_m8_release_checksum_required_and_verified(tmp_path):
    r = _lib("check_release", root=tmp_path)
    assert r.returncode == 1 and "SHA256SUMS" in r.stderr
    import hashlib
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    digest = hashlib.sha256(b"1.0.0\n").hexdigest()
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  VERSION\n", encoding="utf-8")
    r = _lib("check_release; release_version", root=tmp_path)
    assert r.returncode == 0 and r.stdout == "1.0.0", r.stderr
    (tmp_path / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    r = _lib("check_release", root=tmp_path)
    assert r.returncode == 1 and "校验失败" in r.stderr


@pytest.mark.parametrize("outcome", ["cancel", "backup_failure", "success"])
def test_upgrade_preserves_config_and_stops_before_writes(tmp_path, outcome):
    import shutil

    package = tmp_path / "package"
    target = tmp_path / "installed"
    for root in (package, target):
        (root / "scripts").mkdir(parents=True)
    (target / "secrets").mkdir()
    (target / "secrets/master_key").write_text("fictive-key")
    old_env = "APP_VERSION=1.0.0\nPOSTGRES_PASSWORD=fictive-secret\nSITE_ADDRESS=example.test\n"
    (target / ".env").write_text(old_env)
    (target / "Caddyfile").write_text("company-certificate")
    (target / "docker-compose.yml").write_text("old-compose")
    (target / "scripts/backup_now.sh").write_text("exit 1\n" if outcome == "backup_failure" else "exit 0\n")
    for name, content in {"VERSION": "1.0.1\n", "Caddyfile": "new-caddy", "docker-compose.yml": "new-compose", ".env.example": "new-example"}.items():
        (package / name).write_text(content)
    shutil.copy(SCRIPTS / "upgrade.sh", package / "scripts/upgrade.sh")
    # 真实升级脚本与 env_set；仅替代机器与 Docker 检查，避免接触实际部署。
    lib = (SCRIPTS / "lib.sh").read_text(encoding="utf-8")
    lib += '\nrequire_root() { :; }\nrequire_cmd() { :; }\ncheck_storage() { :; }\ncheck_release() { :; }\n'
    lib += '\nload_release_images() { :; }\ncompose() { :; }\nwait_healthy() { :; }\n'
    (package / "scripts/lib.sh").write_text(lib, encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "docker").chmod(0o755)
    r = subprocess.run(["bash", str(package / "scripts/upgrade.sh"), "--target", str(target)],
                       input="取消\n" if outcome == "cancel" else "已创建快照\n", capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"})
    assert "fictive-secret" not in r.stdout + r.stderr
    assert (target / "secrets/master_key").read_text() == "fictive-key"
    assert (target / "Caddyfile").read_text() == "company-certificate"
    if outcome == "success":
        assert r.returncode == 0, r.stderr
        assert (target / ".env").read_text() == old_env.replace("1.0.0", "1.0.1")
        assert (target / "Caddyfile.new").read_text() == "new-caddy"
        assert (target / "docker-compose.yml").read_text() == "new-compose"
    else:
        assert r.returncode != 0
        assert (target / ".env").read_text() == old_env
        assert (target / "docker-compose.yml").read_text() == "old-compose"
        assert not (target / "Caddyfile.new").exists()
