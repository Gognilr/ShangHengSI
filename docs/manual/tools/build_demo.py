"""把演示页 docs/manual/demo/index.html 与 img/ 合成一个可单独拷走的 HTML（图片内嵌为 base64）。
用法（仓库根目录）：python docs/manual/tools/build_demo.py  → docs/manual/dist/商衡SI-核心功能演示.html"""
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")


def inline(m: re.Match) -> str:
    data = base64.b64encode((ROOT / "demo" / m.group(1)).read_bytes()).decode()
    return f'src="data:image/png;base64,{data}"'


out = re.sub(r'src="(img/[^"]+\.png)"', inline, src)
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
target = dist / "商衡SI-核心功能演示.html"
target.write_text(out, encoding="utf-8", newline="\n")
print(target, f"{len(out) / 1048576:.1f} MiB")
