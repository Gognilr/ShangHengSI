"""XML 与 OFD 的结构化数据（设计第三节取值顺序第 1 条）。

XML：数电票的 EInvoice 文件，按元素名直接读字段。OFD：只读其中的结构化数据（内嵌的发票 XML，或
Tags 中 CustomTag 指向的文字对象），读不出的提示“请改传 PDF”。解析一律用 lxml 的安全设置。
"""
import io
import posixpath
import re
import zipfile

from lxml import etree

from app.invoice.layout import KINDS, QR_KINDS
from app.invoice.model import Invoice, norm_date, parse_money

MSG_NOT_INVOICE_XML = "不是发票 XML"
MSG_OFD = "OFD 中读不出结构化数据，请改传 PDF"
MAX_XML_BYTES = 20 * 1024 * 1024


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False,
                           remove_comments=True, remove_pis=True)


def parse_xml(data: bytes):
    try:
        return etree.fromstring(data, _parser())
    except (etree.XMLSyntaxError, ValueError):
        return None


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1]


# 字段 → 可能的元素名（数电票 XML 与 OFD CustomTag 的常见写法）
FIELDS = {
    "number": ("InvoiceNumber", "InvoiceNo", "EIid"),
    "code": ("InvoiceCode",),
    "date": ("IssueTime", "IssueDate", "RequestTime"),
    "buyer_name": ("BuyerName",),
    "buyer_tax": ("BuyerIdNum", "BuyerTaxID", "BuyerTaxNo", "BuyerTaxpayerID"),
    "seller_name": ("SellerName",),
    "seller_tax": ("SellerIdNum", "SellerTaxID", "SellerTaxNo", "SellerTaxpayerID"),
    "amount": ("TotalAmWithoutTax", "TaxExclusiveTotalAmount"),
    "tax": ("TotalTaxAm", "TaxTotalAmount"),
    "total": ("TotalTax-includedAmount", "TaxInclusiveTotalAmount"),
    "total_cn": ("TotalTax-includedAmountInChinese", "TaxInclusiveTotalAmountInChinese"),
    "remark": ("Remark", "Note"),
}


def _fill(inv: Invoice, values: dict[str, list[str]]) -> Invoice:
    """values：元素名 → 文字（按出现顺序）。"""
    def first(names):
        for n in names:
            for v in values.get(n, []):
                if v.strip():
                    return v.strip()
        return ""

    for key, names in FIELDS.items():
        v = first(names)
        if not v:
            continue
        if key in ("amount", "tax", "total"):
            setattr(inv, key, parse_money(v))
        elif key == "date":
            m = re.search(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})", v)
            inv.date = (norm_date(*m.groups()) or "") if m else ""
        elif key in ("buyer_tax", "seller_tax"):
            setattr(inv, key, re.sub(r"\s", "", v).upper())
        elif key in ("number", "code"):
            setattr(inv, key, re.sub(r"\s", "", v))
        else:
            setattr(inv, key, v)
    inv.items = [v.strip() for v in values.get("ItemName", []) if v.strip()]
    rates = []
    for v in values.get("TaxRate", []):
        v = v.strip()
        try:
            f = float(v.rstrip("%"))
            rates.append(v if v.endswith("%") else f"{f * 100:g}%" if f < 1 else f"{f:g}%")
        except ValueError:
            if v:
                rates.append(v)
    inv.rates = list(dict.fromkeys(rates))
    return inv


def _kind(text: str, root_is_einvoice: bool) -> str:
    text = re.sub(r"\s", "", text).replace("（", "(").replace("）", ")")
    for pattern, kind in KINDS:
        if pattern.search(text):
            if root_is_einvoice and kind in ("增值税专用发票", "增值税普通发票"):
                return "数电票（增值税专用发票）" if "专用" in kind else "数电票（普通发票）"
            return kind
    if root_is_einvoice:
        if "专用" in text:
            return "数电票（增值税专用发票）"
        if "普通" in text:
            return "数电票（普通发票）"
    return ""


def invoice_from_xml_root(root, source: str) -> Invoice | None:
    values: dict[str, list[str]] = {}
    labels = []
    for el in root.iter():
        name = _local(el)
        if not name:
            continue
        text = el.text or ""
        if len(el) == 0:
            values.setdefault(name, []).append(text)
        if name in ("LabelName", "InvoiceType", "EInvoiceType", "TypeName"):
            labels.append("".join(el.itertext()))
    inv = _fill(Invoice(source=source), values)
    is_einvoice = _local(root) == "EInvoice"
    inv.kind = _kind(" ".join(labels), is_einvoice)
    if not inv.kind and values.get("InvoiceType"):
        inv.kind = QR_KINDS.get(values["InvoiceType"][0].strip(), "")
    if not inv.number or (inv.total is None and inv.amount is None):
        return None
    return inv


def read_xml(data: bytes) -> Invoice | str:
    root = parse_xml(data)
    if root is None:
        return MSG_NOT_INVOICE_XML
    return invoice_from_xml_root(root, "xml") or MSG_NOT_INVOICE_XML


def read_ofd(data: bytes) -> Invoice | str:
    """OFD 是 zip：先找内嵌的发票 XML，再按 CustomTag 取文字对象。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".xml")
                 and i.file_size <= MAX_XML_BYTES]
        docs = {}
        for info in infos[:500]:
            root = parse_xml(zf.read(info))
            if root is not None:
                docs[info.filename] = root
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        return MSG_OFD
    for name, root in docs.items():
        if _local(root) == "EInvoice":
            inv = invoice_from_xml_root(root, "ofd")
            if inv is not None:
                return inv
    # 文字对象：ID → 文字（页面与模板中的 TextObject 的 TextCode）
    texts: dict[str, str] = {}
    for root in docs.values():
        for el in root.iter():
            if _local(el) == "TextObject" and el.get("ID"):
                texts[el.get("ID")] = "".join(t.text or "" for t in el.iter() if _local(t) == "TextCode")
    values: dict[str, list[str]] = {}
    for name, root in docs.items():
        if "tag" not in posixpath.basename(name).lower():
            continue
        for el in root.iter():
            refs = [c for c in el if _local(c) == "ObjectRef"]
            if not refs:
                continue
            value = "".join(texts.get((c.text or "").strip(), "") for c in refs)
            values.setdefault(_local(el), []).append(value)
    if not values:
        return MSG_OFD
    inv = _fill(Invoice(source="ofd"), values)
    title = " ".join(texts.values())
    inv.kind = _kind(title, False)
    if not inv.number or (inv.total is None and inv.amount is None):
        return MSG_OFD
    return inv
