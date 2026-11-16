"""B-01 的虚构上传样本，运行时在内存生成，不需要模型或外部文件。"""
import io

from docx import Document
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen.canvas import Canvas

PASSWORD = "fictional-bid-pass"


def document():
    doc = Document()
    doc.add_paragraph("虚构项目投标函：本样本仅用于验证分组上传和原文加密存储。")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def pdf(*, encrypted=True, pages=1):
    out = io.BytesIO()
    c = Canvas(out, encrypt=StandardEncryption(PASSWORD) if encrypted else None)
    for _ in range(pages):
        c.drawString(30, 750, "FICTIONAL BID DOCUMENT FOR PASSWORD AND PAGE LIMIT TEST ONLY")
        c.showPage()
    c.save()
    return out.getvalue()


# B-02 虚构招标文件：原文 ★ 4 处（含一处表格行）、否决句 3 处；合同章节与格式章节的“应当 / 必须”不应进入清单。
TENDER_STARS = 4
TENDER_VETOES = 3


def tender():
    doc = Document()
    doc.add_heading("第一章 招标公告", level=1)
    doc.add_paragraph("本项目为虚构的松青示范仪器采购，最高投标限价：人民币 380 万元。投标有效期为 90 日历天。")
    doc.add_paragraph("投标保证金：人民币 5 万元，以银行保函或电汇方式提交。本项目不接受联合体投标。报价为含税价。")
    doc.add_heading("第二章 投标人须知", level=1)
    doc.add_paragraph("★投标人须具有有效的营业执照。★投标人近三年须有两项同类仪器供货业绩。")
    doc.add_paragraph("投标报价超过最高投标限价的，其投标将被否决。")
    doc.add_paragraph("投标文件未按要求密封的，招标人不予受理。")
    doc.add_paragraph("投标人必须按招标文件规定的格式填写开标一览表。")
    doc.add_heading("第三章 技术要求", level=1)
    table = doc.add_table(rows=2, cols=3)
    for i, text in enumerate(["序号", "参数", "要求"]):
        table.cell(0, i).text = text
    table.cell(1, 0).text = "★"
    table.cell(1, 1).text = "测量精度"
    table.cell(1, 2).text = "不低于 0.01 级"
    doc.add_paragraph("★交货期不超过合同签订后 30 日，未响应的视为无效投标。")
    doc.add_heading("第四章 合同条款", level=1)
    doc.add_paragraph("卖方应当在验收合格后十五日内开具发票。")
    doc.add_heading("第五章 投标文件格式", level=1)
    doc.add_paragraph("投标函：我方必须遵守招标文件的全部规定（此处为格式范本）。")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
