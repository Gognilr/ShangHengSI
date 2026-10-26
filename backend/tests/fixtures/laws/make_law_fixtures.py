"""T-41 虚构法规目录，全部在内存生成，不使用官方样本。"""
from io import BytesIO

from openpyxl import Workbook

HEADERS = ['标题', '公布日期', '施行日期', '法律法规分类']


def catalog_bytes(rows, headers=None):
    book = Workbook()
    sheet = book.active
    sheet.append(HEADERS if headers is None else headers)
    for row in rows:
        sheet.append(row)
    out = BytesIO()
    book.save(out)
    book.close()
    return out.getvalue()
