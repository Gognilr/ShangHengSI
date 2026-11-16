"""标书文件属性与图片哈希（bid.md 3.1“文件属性”“相同图片”）：解析时顺带读取，结果加密存入 meta_enc 与 bid_images。

只读原件、不改原件；不另存图片，只存字节 sha256 与尺寸。读取失败只少一项比对，不影响解析。
"""
import hashlib
import io
import posixpath
import re

from app.parsing.docx import _Package

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DC = {"creator": "{http://purl.org/dc/elements/1.1/}creator",
       "last_modified_by": "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}lastModifiedBy",
       "created": "{http://purl.org/dc/terms/}created", "modified": "{http://purl.org/dc/terms/}modified"}
_APP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
MIN_IMAGE_BYTES = 2048  # 分隔线、项目符号等小图不比
MIN_SIDE = 48

# 图片所在页 / 段落的上下文决定类别：厂家资料归“正常重复”，印章签名授权业绩为“中”，其余为“弱”
_MAKER = re.compile(r"检测报告|检验报告|试验报告|产品彩页|彩页|说明书|产品手册|样本|认证证书|型式试验|CCC|3C认证|ISO\s?9001|质量管理体系认证")
_SEAL = re.compile(r"盖章|公章|印章|签字|签名|签章|法定代表人|授权委托书|授权书|业绩|合同|中标通知书|营业执照")


def category(context: str) -> str:
    if _MAKER.search(context or ""):
        return "maker"
    if _SEAL.search(context or ""):
        return "seal"
    return "other"


def _size(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:  # noqa: BLE001 —— EMF / WMF 等读不出尺寸时按字节大小判断
        return 0, 0


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(_W + "t"))


def _docx(data: bytes) -> tuple[dict, list[dict]]:
    pkg = _Package(data)
    props: dict = {}
    core = pkg.xml("docProps/core.xml")
    if core is not None:
        for key, tag in _DC.items():
            el = core.find(tag)
            if el is not None and (el.text or "").strip():
                props[key] = el.text.strip()
    app = pkg.xml("docProps/app.xml")
    if app is not None:
        for key, tag in (("company", "Company"), ("template", "Template"), ("application", "Application")):
            el = app.find(_APP + tag)
            if el is not None and (el.text or "").strip():
                props[key] = el.text.strip()
    settings = pkg.xml("word/settings.xml")
    if settings is not None:
        root = settings.find(f"{_W}rsids/{_W}rsidRoot")
        if root is not None and root.get(_W + "val"):
            props["rsid_root"] = root.get(_W + "val").upper()
        props["rsids"] = sorted({el.get(_W + "val").upper() for el in settings.iter(_W + "rsid") if el.get(_W + "val")})

    # 图片：正文按段落找引用位置取上下文；页眉页脚等未在正文引用的媒体文件也比，上下文为空
    images, seen = [], set()

    def add(name: str, context: str):
        info = pkg.members.get(name.lower())
        if info is None or name.lower() in seen:
            return
        seen.add(name.lower())
        blob = pkg.zf.read(info)
        if len(blob) < MIN_IMAGE_BYTES:
            return
        w, h = _size(blob)
        if w and h and min(w, h) < MIN_SIDE:
            return
        images.append({"page": None, "sha256": hashlib.sha256(blob).hexdigest(), "width": w, "height": h,
                       "category": category(context)})

    doc = pkg.xml("word/document.xml")
    if doc is not None:
        targets = {rid: target for rid, _type, target in pkg.rels("word/document.xml")}
        paragraphs = list(doc.iter(_W + "p"))
        texts = [_text(p) for p in paragraphs]
        for i, p in enumerate(paragraphs):
            refs = [el.get(_R + "embed") for el in p.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip")]
            refs += [el.get(_R + "id") for el in p.iter("{urn:schemas-microsoft-com:vml}imagedata")]
            for rid in filter(None, refs):
                if rid in targets:
                    add(targets[rid], " ".join(texts[max(0, i - 3):i + 2]))
    for name in list(pkg.members):
        if posixpath.dirname(name) == "word/media":
            add(pkg.members[name].filename, "")
    return props, images


def _pdf_value(value):
    from pdfminer.psparser import PSLiteral
    from pdfminer.pdftypes import resolve1
    value = resolve1(value)
    if isinstance(value, bytes):
        for codec in ("utf-16",) if value[:2] in (b"\xfe\xff", b"\xff\xfe") else ("utf-8", "latin-1"):
            try:
                return value.decode(codec).strip("\x00 ")
            except UnicodeDecodeError:
                continue
    if isinstance(value, PSLiteral):
        return str(value.name)
    return value if isinstance(value, str) else None


def _xmp(raw: bytes) -> dict:
    out = {}
    text = raw.decode("utf-8", "ignore")
    for key, tag in (("xmp_document_id", "DocumentID"), ("xmp_original_id", "OriginalDocumentID")):
        m = re.search(rf"xmpMM:{tag}\s*=\s*\"([^\"]+)\"|<xmpMM:{tag}>\s*([^<]+?)\s*</xmpMM:{tag}>", text)
        if m:
            out[key] = (m.group(1) or m.group(2)).strip()
    return out


def _pdf(data: bytes, password: str, nodes) -> tuple[dict, list[dict]]:
    from pdfminer.pdftypes import resolve1, stream_value

    from app.parsing.pdf import _open
    props: dict = {}
    images = []
    page_text: dict[int, str] = {}
    for n in nodes:
        if n.page is not None:
            page_text[n.page] = page_text.get(n.page, "") + n.text
    with _open(data, password, 10**6) as pdf:  # 页数上限已在解析时检查
        doc = pdf.doc
        for key, name in (("author", "Author"), ("creator", "Creator"), ("producer", "Producer"),
                          ("created", "CreationDate"), ("modified", "ModDate")):
            for info in doc.info:
                value = _pdf_value(info.get(name))
                if value:
                    props[key] = value
                    break
        for xref in doc.xrefs:
            ids = resolve1(xref.get_trailer().get("ID"))
            if isinstance(ids, list) and ids:
                first = resolve1(ids[0])
                if isinstance(first, bytes) and first:
                    props["pdf_id"] = first.hex().upper()
                    break
        try:
            meta = resolve1(doc.catalog.get("Metadata"))
            if meta is not None:
                props.update(_xmp(stream_value(meta).get_data()))
        except Exception:  # noqa: BLE001 —— XMP 读不出时只少一项
            pass
        for number, page in enumerate(pdf.pages, 1):
            for image in page.images:
                try:
                    blob = image["stream"].get_data()
                except Exception:  # noqa: BLE001
                    continue
                w, h = (image.get("srcsize") or (0, 0))
                if len(blob) < MIN_IMAGE_BYTES or (w and h and min(w, h) < MIN_SIDE):
                    continue
                images.append({"page": number, "sha256": hashlib.sha256(blob).hexdigest(), "width": int(w or 0),
                               "height": int(h or 0), "category": category(page_text.get(number, ""))})
    return props, images


def collect(ext: str, data: bytes, nodes, password: str | None = None) -> tuple[dict | None, list[dict]]:
    """返回 (属性, 图片)；属性为 None 表示该格式不读属性（旧格式转换件的属性是转换器写的，不能比）。"""
    try:
        if ext == "docx":
            return _docx(data)
        if ext == "pdf":
            return _pdf(data, password or "", nodes)
        if ext in ("jpg", "png") and len(data) >= MIN_IMAGE_BYTES:
            w, h = _size(data)
            return None, [{"page": 1, "sha256": hashlib.sha256(data).hexdigest(), "width": w, "height": h,
                           "category": category(" ".join(n.text for n in nodes))}]
    except Exception:  # noqa: BLE001 —— 读不出属性不影响解析，覆盖说明会写“未读取属性”
        return None, []
    return None, []
