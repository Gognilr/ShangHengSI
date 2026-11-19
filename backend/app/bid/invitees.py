"""发标单位一览表（受邀投标单位名单，V34-B-01b / V34Q-56）：只用本地规则识别，不调用模型。

* 表格（Word / PDF 表格单元格、扫描页表格单元格、Excel 单元格）：前几行中有“单位名称 / 投标人 / 受邀单位”一类表头的列；
  同一表头行含报价、金额、得分的表跳过（开标记录、评分表）。
* 段落与扫描页文字（只在表格规则一家都没取到时）：在“发标 / 投标单位一览表 / 邀请单位名单”一类标题之后的同页及下一页内（Word 无页码时取其后若干段），
  按单位名称后缀取名；扫描页表格已由 D-02 生成单元格的先走表格规则，网格不成形时才落到这里。
* 4～60 字、去序号、规范化去重、剔除招标人与代理机构，至少 2 家才算识别成功；每个名称记出处（file_id、loc）。

名单开标前不得泄露（《招标投标法》第二十二条）：只存 parameters_enc 的加密 JSON，日志只写数量。
"""
import re
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import DocNode, TaskFile
from app.redaction.normalize import normalize_value
from app.redaction.tables import table_cell
from app.security import crypto
from app.security.errors import AppError

MIN_NAMES = 2
MAX_NAMES = 200
_WHO = r"(?:(?:受邀|拟邀请?|邀请|发标|入围)(?:投标)?(?:单位|人|企业|供应商)|投标(?:单位|人))"
_HEADER = re.compile(rf"^(?:{_WHO}(?:名称|全称)?|(?:单位|供应商|企业|公司)(?:名称|全称))$")
_SKIP = re.compile(r"报价|金额|得分|评分|分值|价格|总价|投标价")
_TITLE = re.compile(r"(?:发标|投标|邀请|受邀|拟邀请?|入围)[^。；\n]{0,8}?(?:单位|投标人|供应商|企业)[^。；\n]{0,6}?(?:一览表|名单|名录|清单)")
_SUFFIX = (r"(?:股份有限公司|有限责任公司|有限公司|股份公司|集团公司|分公司|公司|集团|研究院|设计院|研究所|设计所|事务所|"
           r"合作社|中心|厂)")
_ORG = re.compile(r"[一-鿿（）()·A-Za-z]{2,56}?" + _SUFFIX + r"(?:[（(][一-鿿]{2,10}[）)])?")
_ORDINAL = re.compile(r"^(?:序号)?\s*(?:[（(]?\d{1,3}[）)．.、,，]?|[一二三四五六七八九十]+[、．.]|[①-⑳])\s*")
_LEAD = re.compile(r"^(?:邀请|受邀|单位名称|投标人|投标单位|供应商|名称)\s*[:：]?")
_PLACEHOLDER = re.compile(r"盖章|签字|签章|填写|名称[:：]?$|^[\s_\-—…．.]*$|\d{3,}")
_OWNER = re.compile(r"(?:招标人|采购人|招标单位|采购单位|建设单位|项目业主|业主单位|招标代理机构|采购代理机构|代理机构)(?:名称)?\s*[:：]\s*"
                    r"([^\s，,。；;：:（(]{4,60})")
_WINDOW = 80  # 无页码（Word）时标题后取的段数


@dataclass
class _Node:
    file_id: str
    loc: str
    kind: str
    page: int | None
    ocr: bool
    text: str


def key(name: str) -> str:
    """规范化后的比对键：去空白、全半角（NFKC）、繁简与易混字符。"""
    return normalize_value(re.sub(r"\s", "", name or ""))


def clean(raw: str) -> str:
    text = re.sub(r"\s+", "", raw or "")
    text = _ORDINAL.sub("", text)
    text = _LEAD.sub("", text)
    return text.strip("：:，,。；;、")


def _plausible(name: str, *, need_suffix: bool) -> bool:
    if not 4 <= len(name) <= 60 or _PLACEHOLDER.search(name) or re.search(r"[:：。；;]", name):
        return False
    return bool(re.search(_SUFFIX + r"(?:[（(][一-鿿]{2,10}[）)])?$", name)) or (not need_suffix and len(name) >= 6
                                                                         and bool(re.fullmatch(r"[一-鿿（）()·]+", name)))


def _nodes(db, task_id) -> list[_Node]:
    rows = db.execute(select(DocNode.file_id, DocNode.loc, DocNode.kind, DocNode.page, DocNode.ocr_conf, DocNode.text_enc)
                      .join(TaskFile, TaskFile.id == DocNode.file_id)
                      .where(DocNode.task_id == task_id, DocNode.part == "body", TaskFile.bidder_id.is_(None),
                             TaskFile.parser_version.is_not(None))
                      .order_by(TaskFile.seq, DocNode.ord))
    return [_Node(str(f), loc, kind, page, kind == "ocr_block" or conf is not None, crypto.decrypt_text(enc) if enc else "")
            for f, loc, kind, page, conf, enc in rows]


def _excluded(nodes) -> set[str]:
    """招标人与代理机构的名称（“招标人：某某公司”写法）。"""
    out = set()
    for n in nodes:
        for m in _OWNER.finditer(n.text):
            name = clean(m.group(1))
            if len(name) >= 4:
                out.add(key(name))
    return out


def _from_tables(nodes) -> tuple[list[dict], set[str]]:
    """返回 (名称条目, 已成形的表格标识)。"""
    tables: dict[str, dict[int, dict[str, _Node]]] = {}
    for n in nodes:
        cell = table_cell(n.loc)
        if cell:
            tables.setdefault(cell[0], {}).setdefault(cell[2], {})[cell[1]] = n
    found, used = [], set()
    for table, rows in tables.items():
        order = sorted(rows)
        for r in order[:5]:
            header = rows[r]
            cols = [c for c, n in header.items() if _HEADER.match(re.sub(r"\s", "", n.text))]
            if not cols:
                continue
            if any(_SKIP.search(n.text) for n in header.values()):
                break
            col = cols[0]
            entries = []
            for below in order[order.index(r) + 1:]:
                n = rows[below].get(col)
                name = clean(n.text) if n else ""
                if n and _plausible(name, need_suffix=False):
                    entries.append({"name": name, "source": "scan" if n.ocr else "table", "file_id": n.file_id, "loc": n.loc})
            if len(entries) >= MIN_NAMES:
                found += entries
                used.add(table)
            break
    return found, used


def _from_text(nodes, used_tables) -> list[dict]:
    """标题之后同页及下一页（无页码时其后 _WINDOW 段）的单位名称。"""
    found = []
    for i, title in enumerate(nodes):
        if table_cell(title.loc) or len(title.text) > 60 or not _TITLE.search(title.text):
            continue
        for n in nodes[i + 1:i + 1 + (_WINDOW if title.page is None else 10 ** 6)]:
            if title.page is not None and (n.file_id != title.file_id or n.page is None or n.page > title.page + 1):
                break
            if n.file_id != title.file_id:
                break
            cell = table_cell(n.loc)
            if cell and cell[0] in used_tables:
                continue
            for line in n.text.splitlines() or [n.text]:
                for m in _ORG.finditer(clean(line)):
                    name = clean(m.group(0))
                    if _plausible(name, need_suffix=True):
                        found.append({"name": name, "source": "scan" if n.ocr else "text", "file_id": n.file_id, "loc": n.loc})
    return found


def recognize(db, task_id) -> list[dict]:
    """识别名单：[{name, source(table / scan / text), file_id, loc}]；不足 2 家时返回空表。"""
    nodes = _nodes(db, task_id)
    # 表格识别成功时不再按标题取段落：一览表下方的“说明”常列出不参与的单位（用户样本，2026-11-19）
    entries, _used = _from_tables(nodes)
    entries = entries or _from_text(nodes, set())
    skip, out, seen = _excluded(nodes), [], set()
    for e in entries:
        k = key(e["name"])
        if k in seen or k in skip:
            continue
        seen.add(k)
        out.append(e)
    return out[:MAX_NAMES] if len(out) >= MIN_NAMES else []


def refresh(db, task_id, state) -> None:
    """重新识别；用户改过的名单不覆盖。"""
    if not state.get("invitees_edited"):
        state["invitees"] = recognize(db, task_id)


def replace(state, names: list[str]) -> None:
    """用户改名单：保留原有条目的出处，新增的记为手工；规范化后同名拒绝。"""
    old = {key(e["name"]): e for e in state.get("invitees") or []}
    out, seen = [], set()
    for raw in names:
        name = re.sub(r"\s+", " ", raw or "").strip()
        if not name:
            continue
        if len(name) > 255:
            raise AppError(422, "INVITEE_NAME", "单位名称过长")
        k = key(name)
        if k in seen:
            raise AppError(422, "INVITEE_DUPLICATE", f"名单中有重复的单位：{name}")
        seen.add(k)
        prev = old.get(k)
        out.append(prev | {"name": name} if prev else {"name": name, "source": "manual", "file_id": None, "loc": None})
    if len(out) > MAX_NAMES:
        raise AppError(422, "INVITEE_TOO_MANY", f"名单最多 {MAX_NAMES} 家")
    state["invitees"], state["invitees_edited"] = out, True


def names(state) -> list[str]:
    return [e["name"] for e in state.get("invitees") or []]


def baseline_skip(state) -> tuple[set[str], set[str]]:
    """本地检查的招标文件基线中要剔除的 (表格标识, 节点位置)：名单表格整张、段落 / OCR 行逐个。"""
    tables, locs = set(), set()
    for e in state.get("invitees") or []:
        loc = e.get("loc")
        if not loc:
            continue
        cell = table_cell(loc)
        if cell and e.get("source") in ("table", "scan"):
            tables.add(cell[0])
        else:
            locs.add(loc)
    return tables, locs


def view(state) -> list[dict]:
    return [{"name": e["name"], "source": e.get("source", "manual"), "file_id": e.get("file_id"), "loc": e.get("loc")}
            for e in state.get("invitees") or []]
