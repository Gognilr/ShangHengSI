"""十六进制颜色只写在 frontend/src/styles/theme.css（CLAUDE.md）。"""
from pathlib import Path
import re

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
THEME = "styles/theme.css"


def _styles(source: str, suffix: str) -> str:
    if suffix == ".vue":
        source = "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style\s*>", source, re.S | re.I))
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


def test_colors_only_in_theme():
    files = sorted(p for p in SRC.rglob("*") if p.suffix in {".css", ".vue"})
    assert files, "需要整仓挂载"
    errors = []
    for path in files:
        relative = path.relative_to(SRC).as_posix()
        if relative == THEME:
            continue
        css = _styles(path.read_text(encoding="utf-8"), path.suffix)
        errors += [f"{relative}: {m}" for m in re.findall(r"#[\da-f]{3,8}\b|\b(?:rgba?|hsla?)\s*\(", css, re.I)]
    assert not errors, "\n".join(errors)
