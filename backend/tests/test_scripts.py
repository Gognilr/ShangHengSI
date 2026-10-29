"""部署脚本只做语法检查；升级、恢复、换密钥的行为在测试虚拟机上演练。需要整仓挂载。"""
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "deploy" / "scripts"


def test_bash_syntax():
    scripts = sorted(SCRIPTS.glob("*.sh"))
    assert scripts, f"未找到 {SCRIPTS}/*.sh；测试须挂载整个仓库"
    for path in scripts:
        assert "\r" not in path.read_text(encoding="utf-8"), f"{path.name} 含 CRLF"
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, f"{path.name}: {r.stderr}"
