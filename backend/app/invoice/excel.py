"""Excel 发票清单（设计第四节）：openpyxl 在内存中生成，不落盘。"""
import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.invoice.model import Invoice, Unrecognized

HEADERS = ("序号", "票种", "发票代码", "发票号码", "开票日期", "购买方名称", "购买方税号", "销售方名称", "销售方税号",
           "项目名称", "金额", "税额", "价税合计", "税率", "备注", "文件名", "核对提示")
WIDTHS = (6, 22, 14, 24, 12, 28, 22, 28, 22, 30, 14, 12, 14, 10, 30, 28, 36)
TEXT_COLS = {3, 4, 7, 9}  # 代码、号码、税号写成文本：不丢前导零、不变科学计数
MONEY_COLS = {11, 12, 13}
MONEY_FMT = "#,##0.00"
HINT_FILL = PatternFill("solid", fgColor="FFF3D6")  # 浅琥珀底：有核对提示的行（Excel 单元格颜色，不属于界面令牌）


def build(rows: list[Invoice], unrecognized: list[Unrecognized]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "发票清单"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for i, inv in enumerate(rows, start=1):
        ws.append([i, inv.kind, inv.code, inv.number, inv.date, inv.buyer_name, inv.buyer_tax, inv.seller_name,
                   inv.seller_tax, inv.item_text, inv.amount, inv.tax, inv.total, "、".join(inv.rates), inv.remark,
                   "、".join(inv.files), "；".join(inv.hints)])
        r = ws.max_row
        for c in TEXT_COLS:
            ws.cell(r, c).number_format = "@"
        for c in MONEY_COLS:
            ws.cell(r, c).number_format = MONEY_FMT
        if inv.hints:
            ws.cell(r, len(HEADERS)).fill = HINT_FILL
    total = [v for v in (sum((x.amount for x in rows if x.amount is not None), Decimal("0.00")),
                         sum((x.tax for x in rows if x.tax is not None), Decimal("0.00")),
                         sum((x.total for x in rows if x.total is not None), Decimal("0.00")))]
    ws.append(["合计", f"{len(rows)} 张", None, None, None, None, None, None, None, None, *total])
    r = ws.max_row
    for c in range(1, len(HEADERS) + 1):
        ws.cell(r, c).font = Font(bold=True)
    for c in MONEY_COLS:
        ws.cell(r, c).number_format = MONEY_FMT
    for i, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{max(ws.max_row - 1, 1)}"
    if unrecognized:
        un = wb.create_sheet("未识别")
        un.append(("文件名", "原因"))
        for cell in un[1]:
            cell.font = Font(bold=True)
        for u in unrecognized:
            un.append((u.name, u.reason))
        un.column_dimensions["A"].width = 40
        un.column_dimensions["B"].width = 40
        un.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
