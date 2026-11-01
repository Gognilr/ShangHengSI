"""生成前端自带字体 SI Sans / SI Mono 的分片与 fonts.css（V31-U1 设计第二节）。

只在更换字体来源时运行一次，产物提交进仓库；原始 TTF 不进仓库，来源与 SHA-256 见
src/assets/fonts/SOURCES.md。在一次性容器中运行（不进任何镜像）：

  docker run --rm -v "<字体源目录>:/src:ro" -v "${PWD}/frontend:/fe" python:3.12-slim \
    sh -c "pip install -q fonttools==4.59.0 brotli==1.1.0 && python /fe/scripts/build_fonts.py /src /fe"

字体源目录中需有 NotoSansSC-wght.ttf、JetBrainsMono-wght.ttf。
"""
import json
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

WEIGHTS = (400, 600)
MONO_RANGE = "U+0020-007E, U+00A0-00FF, U+2010-2027, U+2030-203A, U+2190-2199, U+2212, U+2260-2265"


def parse_range(text: str) -> list[int]:
    codes: list[int] = []
    for part in text.split(","):
        part = part.strip().upper().removeprefix("U+")
        lo, _, hi = part.partition("-")
        codes.extend(range(int(lo, 16), int(hi or lo, 16) + 1))
    return codes


def limited(path: Path) -> TTFont:
    font = TTFont(path)
    return instancer.instantiateVariableFont(font, {"wght": WEIGHTS})


def write_subset(font_path: Path, codes: list[int], out: Path) -> bool:
    font = TTFont(font_path)
    cmap = font.getBestCmap()
    keep = [c for c in codes if c in cmap]
    if not keep:
        return False
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=keep)
    subsetter.subset(font)
    font.flavor = "woff2"
    font.save(out)
    return True


def rename(font: TTFont, family: str) -> None:
    # OFL 修改版本不得使用保留名称（Noto Sans SC 的保留名称为 “Source”）
    for rec in font["name"].names:
        if rec.nameID in (1, 4, 16):
            rec.string = family
        elif rec.nameID == 6:
            rec.string = family.replace(" ", "")


def face(family: str, url: str, unicode_range: str) -> str:
    return (
        "@font-face {"
        f" font-family: \"{family}\"; font-style: normal; font-weight: {WEIGHTS[0]} {WEIGHTS[1]};"
        f" font-display: swap; src: url(\"{url}\") format(\"woff2\"); unicode-range: {unicode_range}; }}"
    )


def main(src: Path, fe: Path) -> None:
    out_dir = fe / "src/assets/fonts"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.woff2"):
        old.unlink()
    tmp = Path("/tmp/si-fonts")
    tmp.mkdir(exist_ok=True)

    sans = limited(src / "NotoSansSC-wght.ttf")
    rename(sans, "SI Sans")
    sans.save(tmp / "sans.ttf")
    mono = limited(src / "JetBrainsMono-wght.ttf")
    rename(mono, "SI Mono")
    mono.save(tmp / "mono.ttf")

    faces = []
    slices = json.loads((fe / "scripts/sans_slices.json").read_text(encoding="utf-8"))["slices"]
    for s in slices:
        name = f"si-sans-{s['name']}.woff2"
        if write_subset(tmp / "sans.ttf", parse_range(s["range"]), out_dir / name):
            faces.append(face("SI Sans", f"../assets/fonts/{name}", s["range"]))
    write_subset(tmp / "mono.ttf", parse_range(MONO_RANGE), out_dir / "si-mono.woff2")
    faces.append(face("SI Mono", "../assets/fonts/si-mono.woff2", MONO_RANGE))

    header = "/* 由 frontend/scripts/build_fonts.py 生成，不要手改。SI Sans 源自 Noto Sans SC，SI Mono 源自 JetBrains Mono，均为 SIL OFL 1.1（/licenses/）。 */\n"
    (fe / "src/styles/fonts.css").write_text(header + "\n".join(faces) + "\n", encoding="utf-8")
    total = sum(p.stat().st_size for p in out_dir.glob("*.woff2"))
    print(f"{len(faces)} faces, {total / 1048576:.2f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
