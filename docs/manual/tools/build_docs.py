"""把 docs/manual 下的说明书 Markdown 生成 Word（.docx）。只处理本目录文档用到的 Markdown 写法：
标题（# ## ###）、段落、**加粗**、`代码`、[链接](地址)、表格、图片、- 列表（可缩进一级）、1. 列表、> 引用、``` 代码块。

在 backend 测试镜像中运行（含 python-docx 与 Pillow），仓库根目录执行：
  docker run --rm -v "${PWD}/docs:/d" aireview-backend-test python /d/manual/tools/build_docs.py /d /d/manual/dist
PDF 由 LibreOffice 从生成的 docx 转换，命令见 docs/manual/README.md。"""
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
DOCS = ["manual/系统架构说明书.md", "manual/系统使用说明书.md", "ops/运维说明.md"]
FONT, MONO = "Microsoft YaHei", "Consolas"
INK, INK3, BRAND, TINT, HAIR = "0E1726", "5E6B7E", "078A76", "E6F7F2", "C9D2DD"
TEXT_W = Cm(16.4)


def set_font(run, name=FONT, size=None, bold=None, color=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for k in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(k), name)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def shade(el, fill):
    pr = el.get_or_add_tcPr() if el.tag.endswith("}tc") else el.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


def para_border_left(p, color=BRAND):
    ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    for k, v in (("w:val", "single"), ("w:sz", "18"), ("w:space", "8"), ("w:color", color)):
        left.set(qn(k), v)
    bdr.append(left)
    ppr.append(bdr)


def add_inline(p, text, size=None, color=None, bold=False):
    """**加粗**、`代码`；链接只留文字。"""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**"):
            set_font(p.add_run(part[2:-2]), size=size, bold=True, color=color)
        elif part.startswith("`"):
            set_font(p.add_run(part[1:-1]), name=MONO, size=(size or 10.5) - 0.5, color="4F3DD8")
        else:
            set_font(p.add_run(part), size=size, bold=bold or None, color=color)


def styles(doc):
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    st.font.size = Pt(10.5)
    st.font.color.rgb = RGBColor.from_string(INK)
    st.paragraph_format.line_spacing = 1.4
    st.paragraph_format.space_after = Pt(5)
    for name, size, color, before in (("Heading 1", 20, INK, 0), ("Heading 2", 15, BRAND, 16), ("Heading 3", 12.5, INK, 10)):
        h = doc.styles[name]
        h.font.name = FONT
        h.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
        h.font.size = Pt(size)
        h.font.bold = True
        h.font.color.rgb = RGBColor.from_string(color)
        h.paragraph_format.space_before = Pt(before)
        h.paragraph_format.space_after = Pt(6)
        h.paragraph_format.keep_with_next = True


def header_footer(section, title):
    hp = section.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(hp.add_run(f"商衡SI · {title}"), size=8.5, color=INK3)
    fp = section.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run()
    set_font(run, size=9, color=INK3)
    for tag, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), tag)
            run._r.append(fc)
        else:
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = text
            run._r.append(it)


def table(doc, rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    ncol = len(cells[0])
    t = doc.add_table(rows=len(cells), cols=ncol)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    tblpr = t._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "bottom", "insideH", "left", "right", "insideV"):
        b = OxmlElement(f"w:{side}")
        b.set(qn("w:val"), "single" if side in ("top", "bottom", "insideH") else "nil")
        b.set(qn("w:sz"), "4")
        b.set(qn("w:color"), HAIR)
        borders.append(b)
    tblpr.append(borders)
    for i, row in enumerate(cells):
        for j in range(ncol):
            cell = t.cell(i, j)
            cell.paragraphs[0].paragraph_format.space_after = Pt(1)
            text = row[j] if j < len(row) else ""
            add_inline(cell.paragraphs[0], text, size=9.5, bold=(i == 0))
            if i == 0:
                shade(cell._tc, TINT)
    if len(cells) > 1:  # 表头跨页重复
        trpr = t.rows[0]._tr.get_or_add_trPr()
        th = OxmlElement("w:tblHeader")
        th.set(qn("w:val"), "true")
        trpr.append(th)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def image(doc, src_dir, alt, path):
    f = (src_dir / path).resolve()
    w, h = Image.open(f).size
    width = TEXT_W if w >= 1200 else Cm(min(16.4, w / 96 * 2.54))
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(str(f), width=width)
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(cap.add_run(alt), size=9, color=INK3)


def code_block(doc, code):
    for ln in code or [""]:
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.left_indent = Cm(0.3)
        pf.space_after = Pt(0)
        pf.line_spacing = 1.15
        shade(p._p, "F3F6F9")
        set_font(p.add_run(ln or " "), name=MONO, size=8.5, color="334155")
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def build(name):
    md = SRC / name
    lines = md.read_text(encoding="utf-8").splitlines()
    doc = Document()
    styles(doc)
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21)
    sec.left_margin = sec.right_margin = Cm(2.3)
    sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(2.0)
    title = lines[0].lstrip("# ").strip()
    doc_name = title.replace("商衡SI ", "")

    # 封面：标题、开头的信息表（第一个二级标题之前；没有时只写适用版本）
    i, meta = 1, []
    first = next((k for k, l in enumerate(lines) if l.startswith("## ")), len(lines))
    tbl = next((k for k in range(1, first) if lines[k].startswith("|")), None)
    if tbl is not None:
        i = tbl
        while i < len(lines) and lines[i].startswith("|"):
            if not re.fullmatch(r"\|[-| ]+\|", lines[i].strip()):
                meta.append(lines[i])
            i += 1
    else:
        meta = ["| 项目 | 内容 |", "| 适用版本 | V3.6.2 |"]
    for _ in range(6):
        doc.add_paragraph()
    mark = SRC / "manual" / "img" / "brand-mark.png"
    if mark.exists():
        doc.add_paragraph().add_run().add_picture(str(mark), width=Cm(2.6))
    p = doc.add_paragraph()
    set_font(p.add_run("商衡SI"), size=30, bold=True, color=BRAND)
    p = doc.add_paragraph()
    set_font(p.add_run(doc_name), size=30, bold=True)
    p = doc.add_paragraph()
    set_font(p.add_run("企业安全脱敏与智能审查平台"), size=13, color=INK3)
    for _ in range(4):
        doc.add_paragraph()
    table(doc, meta)

    # 目录（静态，列出一、二级标题）
    body = lines[i:]
    fence, heads = False, []
    for l in body:
        if l.startswith("```"):
            fence = not fence
        elif not fence and re.match(r"#{1,2} ", l):
            heads.append(l)
    p = doc.add_paragraph()
    p.add_run().add_break(WD_BREAK.PAGE)
    doc.add_heading("目录", level=1)
    for h in heads:
        lvl = 1 if h.startswith("# ") else 2
        tp = doc.add_paragraph()
        tp.paragraph_format.left_indent = Cm(0 if lvl == 1 else 0.8)
        tp.paragraph_format.space_after = Pt(2 if lvl == 2 else 6)
        set_font(tp.add_run(h.lstrip("# ").strip()), size=11 if lvl == 1 else 10.5, bold=(lvl == 1), color=INK if lvl == 1 else None)

    new_sec = doc.add_section(WD_SECTION.NEW_PAGE)
    new_sec.header.is_linked_to_previous = False
    new_sec.footer.is_linked_to_previous = False
    header_footer(new_sec, doc_name)
    first_h1 = True
    rows, para, code, fence = [], [], [], False

    def flush_para():
        if para:
            add_inline(doc.add_paragraph(), "".join(para))
            para.clear()

    for line in body + [""]:
        s = line.rstrip()
        if s.startswith("```"):
            flush_para()
            if fence:
                code_block(doc, code)
                code = []
            fence = not fence
            continue
        if fence:
            code.append(line)
            continue
        if rows and not s.startswith("|"):
            table(doc, rows)
            rows = []
        if s.startswith("|"):
            flush_para()
            if not re.fullmatch(r"\|[-| ]+\|", s.strip()):
                rows.append(s)
            continue
        if not s.strip():
            flush_para()
            continue
        if s.strip() == "---":
            flush_para()
            continue
        m = re.match(r"(#{1,3}) (.+)", s)
        if m:
            flush_para()
            level = len(m.group(1))
            if level == 1:
                if not first_h1:
                    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
                first_h1 = False
            doc.add_heading(m.group(2).strip(), level=level)
            continue
        m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", s.strip())
        if m:
            flush_para()
            image(doc, md.parent, m.group(1), m.group(2))
            continue
        m = re.match(r"(\s*)(- |\d+\. )(.*)", s)
        if m:
            flush_para()
            indent = 1 if len(m.group(1)) >= 2 else 0
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.left_indent = Cm(0.75 + 0.7 * indent)
            pf.first_line_indent = Cm(-0.5)
            pf.space_after = Pt(3)
            mark_ = "•" if m.group(2) == "- " else m.group(2).strip()
            set_font(p.add_run(f"{mark_} "), color=BRAND if mark_ == "•" else None, bold=mark_ != "•")
            add_inline(p, m.group(3))
            continue
        if s.startswith(">"):
            flush_para()
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.3)
            para_border_left(p)
            shade(p._p, "F3F6F9")
            add_inline(p, s.lstrip("> ").strip())
            continue
        para.append(s.strip())
    flush_para()

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / Path(name).name.replace(".md", ".docx")
    doc.core_properties.title = title
    doc.core_properties.author = "商衡SI"
    doc.save(target)
    print(target)


for d in sys.argv[3:] or DOCS:
    build(d)
