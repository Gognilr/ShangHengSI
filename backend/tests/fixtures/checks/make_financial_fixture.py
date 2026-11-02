"""虚构财务样本。通过写入 XML 缓存值，验证读取显示值与公式的不同路径。"""
import io
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET

from openpyxl import Workbook


def make_workbook(bad=True):
    wb = Workbook(); wb.remove(wb.active)
    balance = wb.create_sheet('资产负债表')
    for row in [['项目', '期末余额', '期初余额'], ['资产总计', 102 if bad else 100, 80],
                ['负债合计', 60, 50], ['所有者权益合计', 40, 30],
                ['负债和所有者权益总计', 100, 80], ['货币资金', 20, 10], ['未分配利润', 15, 10]]:
        balance.append(row)
    profit = wb.create_sheet('利润表')
    for row in [['项目', '本期金额'], ['营业收入', 100], ['营业成本', 60], ['税金及附加', 5],
                ['销售费用', 5], ['管理费用', 5], ['研发费用', 5], ['财务费用', 0],
                ['其他收益', 0], ['投资收益', 0], ['公允价值变动收益', 0], ['信用减值损失', -5],
                ['资产减值损失', 0], ['资产处置收益', 0], ['营业利润', 15], ['营业外收入', 0],
                ['营业外支出', 0], ['利润总额', 15], ['所得税费用', 10], ['净利润', 5]]:
        profit.append(row)
    cash = wb.create_sheet('现金流量表')
    for row in [['项目', '本期金额'], ['经营活动产生的现金流量净额', 10],
                ['投资活动产生的现金流量净额', 0], ['筹资活动产生的现金流量净额', 0],
                ['汇率变动对现金及现金等价物的影响', 0], ['现金及现金等价物净增加额', 10],
                ['期初现金及现金等价物余额', 10], ['期末现金及现金等价物余额', 20]]:
        cash.append(row)
    ws = wb.create_sheet('月度明细')
    for row in [['项目', '人民币', None, '合计'], [None, '一月', '二月', None],
                ['材料甲', 10, 20, '=SUM(B3:C3)'], ['材料乙', 10, 20, '=SUM(B4:C4)'],
                ['材料丙', 10, 20, 31 if bad else '=SUM(B5:C5)'],
                ['材料丁', 10, 20, '=SUM(B6:C6)'], ['材料戊', 10, 20, '=SUM(B7:C7)'],
                ['材料小计', 50, 101 if bad else 100, 150], ['合计', 50, 101 if bad else 100, 150]]:
        ws.append(row)
    ws.merge_cells('B1:C1')
    if bad:
        ws['F1'] = '辅助计算'; ws['F3'] = '=VLOOKUP(B3,B3:C4,2,FALSE)'
        ws['F4'] = "='资产负债表'!$B$3+5"  # 跨表、绝对引用。
    buf = io.BytesIO(); wb.save(buf)
    src, output = ZipFile(io.BytesIO(buf.getvalue())), io.BytesIO()
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(output, 'w', ZIP_DEFLATED) as dest:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == 'xl/worksheets/sheet4.xml':
                root = ET.fromstring(data)
                for c in root.findall('.//s:c', ns):
                    if c.find('s:f', ns) is not None:
                        v = c.find('s:v', ns)
                        if v is None: v = ET.SubElement(c, '{'+ns['s']+'}v')
                        v.text = '31' if bad and c.attrib['r'] == 'D3' else '65' if c.attrib['r'] == 'F4' else '30'
                data = ET.tostring(root)
            dest.writestr(item, data)
    return output.getvalue()


if __name__ == '__main__':
    Path(__file__).with_name('financial.xlsx').write_bytes(make_workbook())
