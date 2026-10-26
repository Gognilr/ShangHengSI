"""T-44 2、3：虚构法规逐条拆分。"""
import io

from docx import Document

from app.laws.articles import split
from app.parsing.docx import parse_docx


def law_doc():
    doc = Document()
    for text in ['示例供货合同法', '（示例题注）', '目录', '第一编　总则', '第一章　范围',
                 '第一条　示例适用范围。', '第二款独立段落。', '第三十条规定的内容继续适用。',
                 '第二章　约定', '第一百二十条之一　示例交付。', '一、示例解释分部',
                 '第一千零一条　示例责任。', '附 则']:
        doc.add_paragraph(text)
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = '示例附表文字。'
    doc.sections[0].header.paragraphs[0].text = '第二条　页眉不参与。'
    doc.sections[0].footer.paragraphs[0].text = '第三条　页脚不参与。'
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def test_t44_2_3_articles_exact():
    got = split(parse_docx(law_doc(), 1).nodes)
    assert [(a.article_no, a.seq, a.text) for a in got] == [
        ('第一条', 1, '示例适用范围。\n第二款独立段落。\n第三十条规定的内容继续适用。'),
        ('第一百二十条之一', 2, '示例交付。'),
        ('第一千零一条', 3, '示例责任。\n示例附表文字。')]


def test_no_articles():
    assert split(parse_docx(_doc_without_articles(), 1).nodes) == []


def _doc_without_articles():
    d = Document()
    d.add_paragraph('示例前言')
    o = io.BytesIO()
    d.save(o)
    return o.getvalue()
