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
