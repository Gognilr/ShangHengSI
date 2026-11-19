"""本地校验的公共纯数据结构（设计 M6 第二节）。调用方只传入正文节点。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckNode:
    loc: str
    text: str
    file_seq: int
    clause_no: str | None
    hidden_spans: list[list[int]] | None = None
    formula: str | None = None
    sheet: str | None = None
    cell_ref: str | None = None
    ocr_conf: float | None = None  # OCR 识读的节点（扫描页块、扫描表格单元格）才有


@dataclass(frozen=True)
class CheckFile:
    seq: int
    is_main: bool
    ext: str
    doc_type: str | None
    warnings: list[str]


@dataclass
class CheckResult:
    code: str
    severity: str
    locs: list[str]
    file_seq: int | None
    detail: str
    ai_summary: str
    unsure: str | None = None  # 因数字来自 OCR 识读而降为提示时，原来的检查代码（V34-D-02）


SEVERITY = {"L-13": "high", "L-14": "high", "L-15": "high", "L-16": "low", "L-01": "high", "L-02": "high", "L-03": "medium", "L-04": "high", "L-05": "high",
            "L-06": "medium", "L-07": "medium", "L-08": "medium", "L-INFO": "low",
            "L-09": "high", "L-10": "medium", "L-11": "medium",
            "L-17": "high", "L-18": "medium", "L-19": "medium", "L-20": "medium", "L-21": "high"}


# 涉及计算的检查：数字来自 OCR 识读时不报为确定的错误（V34-D-02）
OCR_SOFTENED = frozenset({"L-01", "L-02", "L-03", "L-04", "L-05", "L-13", "L-14"})


def soften_ocr(results: list[CheckResult], nodes) -> list[CheckResult]:
    """计算类结果涉及 OCR 识读的节点时改为“请核对原件”的提示（L-INFO，low），原代码记在 unsure。

    扫描件上的数字可能识读错：实测 PP-OCRv6 把“41,000.00”读成“41,0.00”时置信度仍有 0.98，
    置信度阈值挑不全错读，所以凡是 OCR 数字一律降级；置信度低于 LOW_OCR_CONF 的另外注明。
    """
    from app.parsing.model import LOW_OCR_CONF

    confs = {n.loc: n.ocr_conf for n in nodes if getattr(n, "ocr_conf", None) is not None}
    out = []
    for r in results:
        found = [confs[loc] for loc in r.locs if loc in confs]
        if r.code not in OCR_SOFTENED or not found:
            out.append(r)
            continue
        note = "数字来自扫描件识读" + ("，识读置信度低" if min(found) < LOW_OCR_CONF else "") + "，可能是识读错误，请核对原件"
        out.append(CheckResult("L-INFO", "low", r.locs, r.file_seq, f"{r.detail}。{note}",
                               f"{r.ai_summary}（{note}）", unsure=r.code))
    return out
