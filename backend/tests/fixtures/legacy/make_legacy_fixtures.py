"""生成旧格式虚构样本（设计 docs/design/R2.md 第五节）。全部内容来自现有虚构 DOCX / XLSX 样本。

分两步，在仓库根目录执行（bash 中把 ${PWD} 换成 $(pwd)）：

1. legacy：在 converter 镜像中用 LibreOffice 把 ../docx、../xlsx 的样本导出为 .doc / .rtf / .xls，
   复制出 .wps / .et（LibreOffice 不能导出金山格式，内容与 .doc / .xls 相同，Q-144 的真实样本到位后补充），
   再按 converter 的同一过滤器转回 *_doc.docx、*_rtf.docx、*_xls.xlsx：

    docker run --rm --network none -v "${PWD}/backend/tests/fixtures:/srv/fixtures" aireview-converter:dev python /srv/fixtures/legacy/make_legacy_fixtures.py legacy

2. manifest：在 backend 测试镜像中用现有解析器解析转回的文件，写 manifest.yaml：

    docker run --rm -v "${PWD}/backend:/srv/backend" aireview-backend-test python tests/fixtures/legacy/make_legacy_fixtures.py manifest

LibreOffice 每次导出的字节不同（含时间戳），所以生成的文件提交进仓库，测试不重新生成。
"""
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent

# (源样本, 导出格式)；导出过滤器见 EXPORT
SOURCES = [
    ("docx/contract_basic.docx", "doc"),
    ("docx/numbering_auto.docx", "doc"),
    ("docx/revisions.docx", "doc"),
    ("docx/parts_misc.docx", "doc"),
    ("docx/quotation_table.docx", "doc"),
    ("docx/contract_basic.docx", "rtf"),
    ("docx/numbering_auto.docx", "rtf"),
    ("xlsx/quotation.xlsx", "xls"),
    ("xlsx/hidden_sheet.xlsx", "xls"),
    ("xlsx/formula_cached.xlsx", "xls"),
]
EXPORT = {"doc": "doc:MS Word 97", "rtf": "rtf:Rich Text Format", "xls": "xls:MS Excel 97"}
# 金山格式：内容与对应的 .doc / .xls 相同，交给 converter 时 wps 按 doc、et 按 xls（设计 3.3）
KINGSOFT = [("contract_basic.doc", "wps"), ("quotation.xls", "et")]
KIND = {"doc": "doc", "wps": "doc", "rtf": "rtf", "xls": "xls", "et": "xls"}
PARSE_AS = {"doc": "docx", "rtf": "docx", "xls": "xlsx"}


def _soffice(src: Path, filt: str, ext: str, dest: Path) -> None:
    sys.path.insert(0, "/srv/converter")
    import main  # converter/main.py：与服务使用同一套 LibreOffice 命令与超时

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        timed_out, rc = main.run_soffice(src, tmp / "out", filt, tmp / "profile", main.TIMEOUT_SECONDS)
        out = tmp / "out" / f"{src.stem}.{ext}"
        if timed_out or rc != 0 or not out.is_file():
            raise SystemExit(f"转换失败：{src.name} → {ext}（timeout={timed_out} rc={rc}）")
        shutil.copyfile(out, dest)


def make_legacy() -> None:
    for rel, fmt in SOURCES:
        src = FIXTURES / rel
        legacy = HERE / f"{src.stem}.{fmt}"
        _soffice(src, EXPORT[fmt], fmt, legacy)
        back = PARSE_AS[KIND[fmt]]
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / f"src.{KIND[fmt]}"
            shutil.copyfile(legacy, staged)
            import main

            _soffice(staged, main.FILTERS[KIND[fmt]][1], back, HERE / f"{src.stem}_{fmt}.{back}")
        print(f"{legacy.name} → {src.stem}_{fmt}.{back}")
    for name, ext in KINGSOFT:
        shutil.copyfile(HERE / name, HERE / f"{Path(name).stem}.{ext}")
        print(f"{name} → {Path(name).stem}.{ext}")


def _node(n) -> dict:
    out = {"loc": n.loc, "kind": n.kind, "part": n.part, "text": n.text}
    if n.sheet is not None:
        out["sheet"] = n.sheet
    if n.clause_no is not None:
        out["clause_no"] = n.clause_no
    if n.parent_loc is not None:
        out["parent_loc"] = n.parent_loc
    if n.has_revision:
        out["has_revision"] = True
    return out


def make_manifest() -> None:
    sys.path.insert(0, str(HERE.parents[2]))  # backend/
    import yaml

    from app.parsing.docx import parse_docx
    from app.parsing.xlsx import parse_xlsx

    samples = {}
    entries = [(Path(rel).stem, fmt, rel) for rel, fmt in SOURCES]
    entries += [(Path(name).stem, ext, None) for name, ext in KINGSOFT]
    src_of = {(Path(rel).stem, fmt): rel for rel, fmt in SOURCES}
    for stem, ext, rel in entries:
        kind = KIND[ext]
        parse_as = PARSE_AS[kind]
        rel = rel or src_of[(stem, kind)]
        converted = f"{stem}_{kind}.{parse_as}"
        data = (HERE / converted).read_bytes()
        parsed = (parse_docx if parse_as == "docx" else parse_xlsx)(data, 1)
        samples[f"{stem}_{ext}"] = {
            "file": f"{stem}.{ext}",
            "source": f"../{rel}",
            "kind": kind,
            "converted": converted,
            "parse_as": parse_as,
            "page_count": parsed.page_count,
            "has_revisions": parsed.has_revisions,
            "warnings": parsed.warnings,
            "node_count": len(parsed.nodes),
            "clause_nos": [n.clause_no for n in parsed.nodes if n.clause_no],
            "nodes": [_node(n) for n in parsed.nodes],
        }
    doc = {
        "generator": "make_legacy_fixtures.py",
        "note": (
            "全部内容虚构，由 ../docx、../xlsx 的样本经 LibreOffice 导出为旧格式。"
            "kind 为交给 converter 的类型（wps 按 doc、et 按 xls）；converted 为 converter 转回的文件，"
            "nodes / warnings 为现有解析器对它的输出（loc 假定 file_seq=1）；"
            "warnings 不含 pipeline 加的“已由 … 格式转换”警告（R2-03）。"
            "LibreOffice 自己导出的 DOC / RTF 会丢失部分自动编号（design/R2.md 第〇节），以本文件为准，不代表 Word / WPS 保存的真实文件。"
        ),
        "samples": samples,
    }
    text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=1000)
    (HERE / "manifest.yaml").write_text(text, encoding="utf-8")
    print(f"manifest.yaml：{len(samples)} 个样本")


if __name__ == "__main__":
    {"legacy": make_legacy, "manifest": make_manifest}[sys.argv[1]]()
