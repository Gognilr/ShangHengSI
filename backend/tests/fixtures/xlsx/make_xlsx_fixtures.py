"""生成 XLSX 虚构样本和 manifest.yaml（M3-01）。

用法（在 backend 目录下）：  python tests/fixtures/xlsx/make_xlsx_fixtures.py
只用 openpyxl 和 PyYAML。openpyxl 写出的 ZIP 带当前时间，这里重新打包并固定时间戳，
因此重复生成得到逐字节相同的文件。

内容全部虚构：公司、人名、电话、邮箱均为编造，不对应任何真实对象。

生成的样本：
  quotation.xlsx         报价单：表头行（第 7 行）上方有标题行和签约方信息；含单价、金额、合计行、
                         百分比与货币格式、日期、合并单元格；第二个工作表“备注”放数字与日期的边界情形
  formula_uncached.xlsx  含没有缓存值的公式（openpyxl 写出的公式都没有缓存值）
  formula_cached.xlsx    公式带缓存值（写出后在 XML 中补入 <v>），应取缓存值、不报警告
  hidden_rows_cols.xlsx  含隐藏的行和隐藏的列
  hidden_sheet.xlsx      含隐藏工作表和 veryHidden 工作表
超过 20000 个非空单元格的文件由测试临时生成，不提交。

manifest.yaml 由本脚本根据同一份数据生成。nodes 的 loc 假定 file_seq=1（F1-S{工作表序号}-{单元格地址}），
text 是 Q-62 规定的单元格文字（手写在下面的数据里，不是由代码推算的）：
  数字去掉尾随 0 且不含科学计数法；格式含 % 时乘 100 加 %；格式含货币符号或代码时在数字前加符号或代码与一个空格；
  日期 YYYY-MM-DD，有时间部分时 YYYY-MM-DD HH:MM；布尔 TRUE / FALSE；字符串去首尾空白。
no_node_locs 是不应出现节点的单元格（未缓存的公式、空白字符串、合并区域中非左上角的格）。
"""
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from openpyxl import Workbook
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

OUT_DIR = Path(__file__).resolve().parent
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
FIXED_DT = datetime(2026, 1, 1)

USD = "[$USD] #,##0.00"
CNY = "[$¥-804] #,##0.00"
NO_NODE = object()  # 预期文字：这个单元格不出节点


@dataclass
class Cell:
    addr: str
    value: object
    fmt: str | None = None
    text: object = None  # 预期节点文字；NO_NODE 表示不出节点；None 表示与 value 的字符串形式相同


@dataclass
class Sheet:
    name: str
    cells: list[Cell]
    state: str = "visible"  # visible / hidden / veryHidden
    merges: list[str] = field(default_factory=list)
    hidden_rows: list[int] = field(default_factory=list)
    hidden_cols: list[str] = field(default_factory=list)


def _sort_key(addr: str):
    col, row = coordinate_from_string(addr)
    return row, column_index_from_string(col)


def build(path: Path, sheets: list[Sheet], cached: dict[str, str] | None = None):
    """写出 xlsx。cached: {单元格地址: 缓存值文字}，给第一个工作表中的公式单元格补缓存值。"""
    wb = Workbook()
    wb.properties.creator = "fixture"
    wb.properties.lastModifiedBy = "fixture"
    wb.properties.created = FIXED_DT
    wb.properties.modified = FIXED_DT
    wb.remove(wb.active)
    for sh in sheets:
        ws = wb.create_sheet(sh.name)
        ws.sheet_state = sh.state
        for cell in sh.cells:
            c = ws[cell.addr]
            c.value = cell.value
            if cell.fmt:
                c.number_format = cell.fmt
        for rng in sh.merges:
            ws.merge_cells(rng)
        for r in sh.hidden_rows:
            ws.row_dimensions[r].hidden = True
        for col in sh.hidden_cols:
            ws.column_dimensions[col].hidden = True
    buf = io.BytesIO()
    wb.save(buf)

    # 重新打包：固定时间戳；需要时补公式缓存值
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            data = src.read(name)
            if cached and name == "xl/worksheets/sheet1.xml":
                xml = data.decode("utf-8")
                for addr, val in cached.items():
                    pattern = re.compile(r'(<c r="%s"[^>]*><f>[^<]*</f>)<v></v>' % addr)
                    xml, n = pattern.subn(r"\1<v>%s</v>" % val, xml)
                    assert n == 1, f"未找到公式单元格 {addr}"
                data = xml.encode("utf-8")
            if name == "docProps/core.xml":
                # openpyxl 保存时会把 modified 改成当前时间，这里改回固定值
                data = re.sub(
                    rb'(<dcterms:modified[^>]*>)[^<]*', rb"\g<1>2026-01-01T00:00:00Z", data
                )
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            dst.writestr(info, data)
    path.write_bytes(out.getvalue())


def expected_nodes(sheets: list[Sheet]) -> tuple[list[dict], list[str]]:
    nodes, no_node = [], []
    for idx, sh in enumerate(sheets, start=1):
        part = "body" if sh.state == "visible" else "hidden_sheet"
        # 合并区域中非左上角的格没有内容，不需要列出
        for cell in sorted(sh.cells, key=lambda c: _sort_key(c.addr)):
            loc = f"F1-S{idx}-{cell.addr}"
            if cell.text is NO_NODE:
                no_node.append(loc)
                continue
            text = cell.text if cell.text is not None else str(cell.value)
            nodes.append({"loc": loc, "sheet": sh.name, "text": text, "part": part})
    return nodes, no_node


def entry(file: str, description: str, sheets: list[Sheet], warnings: list[str], must_replace: list[dict], must_keep: list[str]) -> dict:
    nodes, no_node = expected_nodes(sheets)
    return {
        "file": file,
        "description": description,
        "page_count": None,
        "sheets": [{"index": i, "name": s.name, "state": s.state} for i, s in enumerate(sheets, start=1)],
        "warnings": warnings,
        "nodes": nodes,
        "no_node_locs": no_node,
        "must_replace": must_replace,
        "must_keep": must_keep,
    }


# ---------------------------------------------------------------- 各样本

WARN_FORMULA = "含未计算的公式，相关金额无法核验"
WARN_HIDDEN_ROWCOL = "含隐藏的行或列，已按正常内容审核"
WARN_HIDDEN_SHEET = "隐藏工作表未参与审核"


def quotation() -> tuple[list[Sheet], dict]:
    s1 = Sheet(
        "报价",
        [
            Cell("A1", "报价单 Quotation"),
            Cell("A2", "卖方：星河虚构进出口有限公司"),
            Cell("A3", "买方：Fictional Metals Trading FZE"),
            Cell("A4", "联系人：周示例，电话：13800138000，邮箱：zhou.example@example.com"),
            Cell("A5", "报价日期"),
            Cell("B5", datetime(2026, 3, 10), "yyyy-mm-dd", "2026-03-10"),
            Cell("A6", "有效期至"),
            Cell("B6", datetime(2026, 4, 30), "yyyy-mm-dd", "2026-04-30"),
            Cell("A7", "序号"),
            Cell("B7", "品名"),
            Cell("C7", "规格"),
            Cell("D7", "数量(MT)"),
            Cell("E7", "单价(USD/MT)"),
            Cell("F7", "金额(USD)"),
            Cell("G7", "预付款比例"),
            Cell("A8", 1, None, "1"),
            Cell("B8", "煅后石油焦"),
            Cell("C8", "低硫 0-3mm"),
            Cell("D8", 320, None, "320"),
            Cell("E8", 1250.0, USD, "USD 1250"),
            Cell("F8", 400000.0, USD, "USD 400000"),
            Cell("G8", 0.3, "0%", "30%"),
            Cell("A9", 2, None, "2"),
            Cell("B9", "针状焦"),
            Cell("C9", "超低硫 1-5mm"),
            Cell("D9", 100, None, "100"),
            Cell("E9", 2100.0, USD, "USD 2100"),
            Cell("F9", 210000.0, USD, "USD 210000"),
            Cell("G9", 0.3, "0%", "30%"),
            Cell("A10", "合计 Total"),
            Cell("F10", 610000.0, USD, "USD 610000"),
            Cell("A11", "折合人民币（汇率 7.1）"),
            Cell("F11", 4331000.0, CNY, "¥ 4331000"),
        ],
        merges=["A1:G1", "A10:E10", "A11:E11"],
    )
    s2 = Sheet(
        "备注",
        [
            Cell("A1", "备注"),
            Cell("A2", "佣金率"),
            Cell("B2", 0.0125, "0.00%", "1.25%"),
            Cell("A3", "最小价格变动"),
            Cell("B3", 0.0000001, "General", "0.0000001"),
            Cell("A4", "授信额度（USD）"),
            Cell("B4", 15000000000.0, "General", "15000000000"),
            Cell("A5", "含税"),
            Cell("B5", True, None, "TRUE"),
            Cell("A6", "更新时间"),
            Cell("B6", datetime(2026, 3, 10, 14, 30), "yyyy-mm-dd hh:mm", "2026-03-10 14:30"),
            Cell("A7", "汇率"),
            Cell("B7", 7.1, None, "7.1"),
            Cell("A8", "运费（USD）"),
            Cell("B8", 12.50, None, "12.5"),
            Cell("A9", "说明"),
            Cell("B9", "  付款以实际到港为准  ", None, "付款以实际到港为准"),
            Cell("B10", "   ", None, NO_NODE),
        ],
    )
    sheets = [s1, s2]
    e = entry(
        "quotation.xlsx",
        "报价单。表头行在第 7 行，上方是标题行、签约方和联系人信息；单价与金额列为货币格式（[$USD]）、预付款比例为百分比格式、折合人民币为 [$¥-804] 格式；"
        "A1:G1、A10:E10、A11:E11 为合并单元格（值在左上角）；F10 为合计行（普通数值，不是公式）。第二个工作表“备注”放数字、日期、布尔值、空白字符串等边界情形。",
        sheets,
        [],
        [
            {"text": "星河虚构进出口有限公司", "category": "ORG"},
            {"text": "Fictional Metals Trading FZE", "category": "ORG"},
            {"text": "周示例", "category": "PERSON"},
            {"text": "13800138000", "category": "PHONE"},
            {"text": "zhou.example@example.com", "category": "EMAIL"},
            {"text": "USD 1250", "category": "PRICE"},
            {"text": "USD 2100", "category": "PRICE"},
            {"text": "USD 400000", "category": "AMOUNT"},
            {"text": "USD 210000", "category": "AMOUNT"},
            {"text": "USD 610000", "category": "AMOUNT"},
            {"text": "¥ 4331000", "category": "AMOUNT"},
        ],
        ["煅后石油焦", "针状焦", "低硫 0-3mm", "超低硫 1-5mm", "320", "100", "30%", "2026-03-10", "2026-04-30", "7.1", "1.25%"],
    )
    return sheets, e


def formula_sheet() -> Sheet:
    return Sheet(
        "汇总",
        [
            Cell("A1", "项目"),
            Cell("B1", "金额(USD)"),
            Cell("A2", "货款"),
            Cell("B2", 400000.0, USD, "USD 400000"),
            Cell("A3", "运费"),
            Cell("B3", 12000.0, USD, "USD 12000"),
            Cell("A4", "合计"),
            Cell("B4", "=SUM(B2:B3)", USD, NO_NODE),
            Cell("A5", "折合人民币"),
            Cell("B5", "=B4*7.1", CNY, NO_NODE),
        ],
    )


def formula_uncached() -> dict:
    return entry(
        "formula_uncached.xlsx",
        "B4、B5 是没有缓存值的公式：不出节点，整个文件加一条警告（只加一次）；其余单元格正常。",
        [formula_sheet()],
        [WARN_FORMULA],
        [{"text": "USD 400000", "category": "AMOUNT"}, {"text": "USD 12000", "category": "AMOUNT"}],
        ["货款", "运费"],
    )


def formula_cached() -> tuple[Sheet, dict]:
    sh = formula_sheet()
    sh.cells[7].text = "USD 412000"  # B4
    sh.cells[9].text = "¥ 2925200"  # B5
    e = entry(
        "formula_cached.xlsx",
        "与 formula_uncached.xlsx 相同的表，但 B4、B5 的公式带缓存值（412000、2925200）：取缓存值出节点，不报警告。",
        [sh],
        [],
        [
            {"text": "USD 400000", "category": "AMOUNT"},
            {"text": "USD 12000", "category": "AMOUNT"},
            {"text": "USD 412000", "category": "AMOUNT"},
            {"text": "¥ 2925200", "category": "AMOUNT"},
        ],
        ["货款", "运费"],
    )
    return sh, e


def hidden_rows_cols() -> tuple[Sheet, dict]:
    sh = Sheet(
        "清单",
        [
            Cell("A1", "项目"),
            Cell("B1", "数量"),
            Cell("C1", "内部备注"),
            Cell("A2", "A 料"),
            Cell("B2", 10, None, "10"),
            Cell("C2", "底价 USD 900", None),
            Cell("A3", "B 料"),
            Cell("B3", 20, None, "20"),
            Cell("A4", "内部折扣"),
            Cell("B4", 5, None, "5"),
            Cell("A5", "C 料"),
            Cell("B5", 30, None, "30"),
        ],
        hidden_rows=[4],
        hidden_cols=["C"],
    )
    e = entry(
        "hidden_rows_cols.xlsx",
        "第 4 行和 C 列被隐藏：节点照常出（part=body），整个文件加一条警告。",
        [sh],
        [WARN_HIDDEN_ROWCOL],
        [{"text": "USD 900", "category": "PRICE"}],
        ["A 料", "B 料", "C 料", "内部折扣"],
    )
    return sh, e


def hidden_sheet() -> tuple[list[Sheet], dict]:
    sheets = [
        Sheet("报价", [Cell("A1", "品名"), Cell("B1", "单价"), Cell("A2", "煅后石油焦"), Cell("B2", 1250.0, USD, "USD 1250")]),
        Sheet(
            "内部成本",
            [Cell("A1", "成本价"), Cell("B1", 900, USD, "USD 900"), Cell("A2", "利润率"), Cell("B2", 0.28, "0%", "28%")],
            state="hidden",
        ),
        Sheet("配置", [Cell("A1", "审批人"), Cell("B1", "吴虚构")], state="veryHidden"),
    ]
    e = entry(
        "hidden_sheet.xlsx",
        "第 2 个工作表（hidden）和第 3 个工作表（veryHidden）的节点 part=hidden_sheet，整个文件加一条警告。",
        sheets,
        [WARN_HIDDEN_SHEET],
        [{"text": "吴虚构", "category": "PERSON"}, {"text": "USD 1250", "category": "PRICE"}, {"text": "USD 900", "category": "PRICE"}],
        ["煅后石油焦", "28%"],
    )
    return sheets, e


def main():
    samples = {}

    sheets, e = quotation()
    build(OUT_DIR / "quotation.xlsx", sheets)
    samples["quotation"] = e

    build(OUT_DIR / "formula_uncached.xlsx", [formula_sheet()])
    samples["formula_uncached"] = formula_uncached()

    sh, e = formula_cached()
    build(OUT_DIR / "formula_cached.xlsx", [sh], cached={"B4": "412000", "B5": "2925200"})
    samples["formula_cached"] = e

    sh, e = hidden_rows_cols()
    build(OUT_DIR / "hidden_rows_cols.xlsx", [sh])
    samples["hidden_rows_cols"] = e

    sheets, e = hidden_sheet()
    build(OUT_DIR / "hidden_sheet.xlsx", sheets)
    samples["hidden_sheet"] = e

    manifest = {
        "generator": "make_xlsx_fixtures.py",
        "note": "全部内容虚构。nodes 的 loc 假定 file_seq=1；text 为 Q-62 规定的单元格文字；no_node_locs 为不应出节点的单元格。节点按行优先排序。",
        "samples": samples,
    }
    (OUT_DIR / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8"
    )
    print("已生成", ", ".join(s["file"] for s in samples.values()))


if __name__ == "__main__":
    main()
