"""生成定位层虚构样本；只在 Docker 中运行，不含真实业务内容。"""
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml


def build():
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("甲方应当按时").bold = True
    p.add_run("支付合同价款。").italic = True
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.paragraphs[0].add_run("交付地点另行约定。")
    cell.add_paragraph("验收期限为十日。")
    doc.add_paragraph("及时通知，及时通知。")
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    p = doc.add_paragraph("附件：")
    p._p.append(parse_xml(f'<w:hyperlink {ns}><w:r><w:t>虚构附件</w:t></w:r></w:hyperlink>'))
    doc.add_paragraph("双方协商解决争议。", style="List Number")
    doc.sections[0].header.paragraphs[0].add_run("虚构合同页眉")
    # 被删除的段落标记：解析节点合并，但原 XML 中仍保留两个段落。
    p = doc.add_paragraph("合并前段")
    p._p.get_or_add_pPr().append(parse_xml(f'<w:rPr {ns}><w:del w:id="1"/></w:rPr>'))
    doc.add_paragraph("合并后段")
    p = doc.add_paragraph("域：")
    for xml in ('<w:fldChar w:fldCharType="begin"/>', '<w:instrText> REF example </w:instrText>',
                '<w:fldChar w:fldCharType="separate"/>', '<w:t>域结果</w:t>',
                '<w:fldChar w:fldCharType="end"/>'):
        p._p.append(parse_xml(f'<w:r {ns}>{xml}</w:r>'))
    p = doc.add_paragraph("直接文字")
    p._p.append(parse_xml(f'<w:ins {ns} w:id="2"><w:r><w:t>修订文字</w:t></w:r></w:ins>'))
    p.add_run("直接文字")
    p = doc.add_paragraph()
    p.add_run("边界前\t边界后")
    doc.save(Path(__file__).with_name("anchors.docx"))


if __name__ == "__main__":
    build()
