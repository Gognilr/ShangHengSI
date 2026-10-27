"""V2-U-01：扫描源码样式，约束品牌令牌与排版档位。"""
from pathlib import Path
import re

import pytest


SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
THEME = "styles/theme.css"
LOGO = "components/brand/SiLogo.vue"
SKELETON_ALLOWED = {"views/History.vue", "views/ReviewResult.vue"}
SIZES = {"12", "13", "14", "15", "16", "18", "24", "30", "32"}
WEIGHTS = {"400", "600", "normal", "inherit"}


def styles(source: str, suffix: str) -> str:
    """Vue 只取 style 块；CSS 注释不参与规则判断。"""
    if suffix == ".vue":
        source = "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style\s*>", source, re.S | re.I))
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


def declarations(css: str, property_name: str) -> list[str]:
    return [value.strip() for value in re.findall(
        rf"(?<![\w-]){property_name}\s*:\s*([^;{{}}]+)", css, re.I
    )]


def violations(rule: str, source: str, path: str) -> list[str]:
    css = styles(source, Path(path).suffix)
    if rule == "skeleton":
        template = re.search(r"<template\b[^>]*>(.*)</template\s*>", source, re.S | re.I)
        markup = re.sub(r"<!--.*?-->", "", template[1], flags=re.S) if template else ""
        return re.findall(r"<el-skeleton\b", markup, re.I) if path not in SKELETON_ALLOWED else []
    if rule in {"colors", "gradients", "family"} and path == THEME:
        return []
    if rule == "colors":
        return re.findall(r"#[\da-f]{3,8}\b|\b(?:rgba?|hsla?)\s*\(", css, re.I)
    if rule == "gradients":
        return re.findall(r"\b(?:linear|radial)-gradient\s*\(", css, re.I)
    if rule == "family":
        return [v for v in declarations(css, "font-family") if v.lower() != "inherit"]
    if rule == "sizes":
        if path == LOGO:
            return []
        return [v for v in declarations(css, "font-size")
                if any(n not in SIZES for n in re.findall(r"([\d.]+)px\b", v, re.I))]
    if rule == "weights":
        return [] if path == LOGO else [v for v in declarations(css, "font-weight")
                                       if v.lower() not in WEIGHTS]
    if rule == "radius":
        invalid = []
        for value in declarations(css, "border-radius"):
            tokens = re.sub(r"var\(--[\w-]+\)", "TOKEN", value).split()
            if not tokens or any(t not in {"0", "4px", "6px", "8px", "12px", "50%", "TOKEN"} for t in tokens):
                invalid.append(value)
        return invalid
    raise ValueError(rule)


def check(rule: str) -> None:
    files = sorted(p for p in SRC.rglob("*") if p.suffix in {".css", ".vue"})
    assert files, "必须整仓挂载，不能跳过源码扫描"
    errors = []
    for path in files:
        relative = path.relative_to(SRC).as_posix()
        errors.extend(f"{relative}: {v}" for v in violations(rule, path.read_text(encoding="utf-8"), relative))
    assert not errors, "\n".join(errors)


def test_colors_only_in_theme():
    check("colors")


def test_gradients_only_in_theme():
    check("gradients")


def test_font_family_only_in_theme():
    check("family")


def test_font_sizes_whitelist():
    check("sizes")


def test_font_weights_whitelist():
    check("weights")


def test_radius_whitelist():
    check("radius")


def test_no_skeleton_outside_allowed():
    check("skeleton")


@pytest.mark.parametrize("rule,bad,good", [
    ("colors", "color: #abc; background: #12345678;", "color: var(--el-color-primary);"),
    ("colors", "color: rgba(1,2,3,.5); background: hsl(0,0%,0%);", "color: inherit;"),
    ("colors", "color: rgb(1,2,3);", "color: currentColor;"),
    ("colors", "color: hsla(0,0%,0%,.5);", "color: transparent;"),
    ("gradients", "background: linear-gradient(red,blue);", "background: var(--si-brand-gradient);"),
    ("gradients", "background: radial-gradient(red,blue);", "background: transparent;"),
    ("family", "font-family: monospace;", "font-family: inherit;"),
    ("sizes", "font-size: 22px;", "font-size: 15px;"),
    ("weights", "font-weight: bold;", "font-weight: 600;"),
    ("weights", "font-weight: 500;", "font-weight: normal;"),
    ("weights", "font-weight: 700;", "font-weight: inherit;"),
    ("radius", "border-radius: 12px 16px;", "border-radius: var(--si-radius-card) 0 0 12px;"),
    ("radius", "border-radius: 10px;", "border-radius: 0 4px 6px 8px;"),
])
def test_scanner_self_check(rule, bad, good):
    assert violations(rule, f"<style scoped>.x {{ {bad} }}</style>", "Example.vue")
    assert not violations(rule, f"<style scoped>.x {{ {good} }}</style>", "Example.vue")
    assert not violations(rule, f"<template>{bad}</template><script>{bad}</script>", "Example.vue")
    assert not violations(rule, f"/* {bad} */", "example.css")
    if rule in {"colors", "gradients", "family"}:
        assert not violations(rule, bad, THEME)
    if rule in {"sizes", "weights"}:
        assert not violations(rule, f"<style>{bad}</style>", LOGO)
    if rule == "radius":
        assert not violations(rule, "a { border-radius: 50%; }", "example.css")
    skeleton = '<template><el-skeleton :rows="5" /></template>'
    assert violations("skeleton", skeleton, "views/Login.vue")
    for path in SKELETON_ALLOWED:
        assert not violations("skeleton", skeleton, path)
    assert not violations("skeleton", '<template><!-- <el-skeleton /> --></template>', "Example.vue")
