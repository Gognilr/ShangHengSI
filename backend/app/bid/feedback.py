"""本人标书反馈及项目内核验，敏感内容随项目或账号加密存储。"""
import hashlib
import json
import uuid

from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import BidProject, User
from app.redaction.normalize import normalize_value
from app.security import crypto
from app.security.errors import AppError, NotFoundError

REASONS = {
    "template": "招标范本或标准规范",
    "manufacturer": "同一厂家资料",
    "recognition": "识别错误",
    "model": "模型判断有误",
    "other": "其他",
}


def load(raw):
    return json.loads(crypto.decrypt_text(raw)) if raw else {}


def encrypt(value):
    return crypto.encrypt_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def agency(project):
    from app.bid.requirements import load_state
    text = load_state(project)["values"].get("agency") or ""
    return normalize_value(text)


def owner_state(db, owner_id, *, lock=False):
    user = db.get(User, owner_id, populate_existing=lock, with_for_update=lock)
    return user, load(user.bid_feedback_enc)


def source_keys(item, reason):
    """只用可复现的同源标识；标题、同类别和相似度不足以建立联动。"""
    sources = set()
    for row in item["rows"]:
        if item["type"] == "image" and row.get("sha256"):
            sources.add("图片:" + row["sha256"])
        elif item["type"] == "similar" and reason == "template":
            left, right = (normalize_value(row.get(side, {}).get("text", "")) for side in ("left", "right"))
            if left and left == right:
                sources.add("范本:" + hashlib.sha256(left.encode()).hexdigest())
        elif (item["type"] == "props" and row["label"].startswith("生成软件") and
              row.get("value") and reason == "recognition"):
            sources.add("生成软件:" + normalize_value(row["value"]))
    return sources


def memory_matches(item, rules, organization):
    if not organization:
        return False
    for rule in rules:
        if rule["agency"] == organization:
            keys = source_keys(item, rule["reason"])
            # 混有其他证据的条目仍须展示。
            if keys and keys <= set(rule["sources"]) and len(item["rows"]) == len(keys):
                return True
    return False


def apply(view, project, owner):
    state = load(project.conclusion_enc)
    rules = owner.get("rules", [])
    scoped = agency(project)
    reviews = state.get("reviews", {})
    items = {str(i["id"]): i for i in view["items"]}
    for item in items.values():
        if memory_matches(item, rules, scoped):
            item["reference_reason"] = "本人反馈记忆"
    for review in reviews.values():
        if review["status"] != "excluded" or not all(i in items for i in review.get("evidence", [])):
            continue
        for iid in review.get("evidence", []) + review.get("linked", []):
            if iid in items:
                items[iid]["reference_reason"] = review["reason_label"]
    state["reviews"] = {key: review for key, review in reviews.items()
                        if all(i in items for i in review.get("evidence", []))}
    return state


def sign(db, task, value):
    from app.bid.results import check_view
    project = db.get(BidProject, task.id)
    state = load(project.conclusion_enc)
    if value:
        conclusion = check_view(db, task)["conclusion"]
        if not conclusion["certain"]:
            raise AppError(409, "NO_CERTAIN", "没有需要签认的确凿项")
        state["signed"] = {"at": utcnow().isoformat(), "items": sorted(e["id"] for e in conclusion["certain"])}
    else:
        state.pop("signed", None)
    project.conclusion_enc = encrypt(state)


def decide(db, task, key, status, reason=None, detail=None, remember=False):
    from app.bid.results import check_view
    project = db.get(BidProject, task.id)
    state = load(project.conclusion_enc)
    reviews = state.setdefault("reviews", {})
    if status == "pending":
        old = reviews.pop(key, None)
        if old and old.get("memory"):
            revoke(db, task.owner_id, old["memory"], missing_ok=True)
        state.pop("signed", None)
        project.conclusion_enc = encrypt(state)
        return
    view = check_view(db, task)
    conclusion = view["conclusion"]
    entry = next((e for e in conclusion["verify"] + conclusion["certain"] if e["id"] == key), None)
    if entry is None:
        raise NotFoundError("NOT_FOUND", "核验事项不存在，请刷新页面")
    case = next((c for c in conclusion["cases"] if c["id"] == key), None)
    evidence = list(dict.fromkeys(e["item_id"] for e in case["evidence"] if e["tier"] != "reference")) if case else []
    if entry["item_id"]:
        evidence = [entry["item_id"]]
    if status == "excluded" and (reason not in REASONS or reason == "other" and not detail):
        raise AppError(422, "REVIEW_REASON", "请选择核验原因，其他原因须填写说明")
    review = {"status": status, "entry": entry, "evidence": evidence, "linked": [],
              "reason": reason, "reason_label": REASONS.get(reason), "detail": detail}
    if status == "excluded":
        items = {str(i["id"]): i for i in view["items"]}
        sources = set().union(*(source_keys(items[i], reason) for i in evidence if i in items))
        for iid, item in items.items():
            if iid in evidence or item.get("reference_reason") or item["review_status"] != "pending":
                continue
            keys = source_keys(item, reason)
            if keys and keys <= sources and len(item["rows"]) == len(keys):
                review["linked"].append(iid)
        if remember:
            organization = agency(project)
            if reason not in {"template", "manufacturer", "recognition"} or not organization or not sources:
                raise AppError(422, "NO_FEEDBACK_SOURCE", "记住此项需要项目代理机构与可复用的范本、图片或生成软件证据")
            owner, personal = owner_state(db, task.owner_id, lock=True)
            token = str(uuid.uuid4())
            personal.setdefault("rules", []).append({"id": token, "agency": organization, "reason": reason,
                                                      "label": entry["category"], "sources": sorted(sources)})
            owner.bid_feedback_enc = encrypt(personal)
            review["memory"] = token
    reviews[key] = review
    state.pop("signed", None)
    project.conclusion_enc = encrypt(state)


def settings(db, owner_id):
    from app.db.models import Task
    _, data = owner_state(db, owner_id)
    totals = {}
    projects = db.scalars(select(BidProject.conclusion_enc).join(Task, Task.id == BidProject.task_id)
                          .where(Task.owner_id == owner_id, BidProject.conclusion_enc.is_not(None)))
    for encrypted in projects:
        for review in load(encrypted).get("reviews", {}).values():
            category = review["entry"]["category"]
            stats = totals.setdefault(category, {"verified": 0, "total": 0})
            stats["total"] += 1
            stats["verified"] += int(review["status"] == "verified")
    return {"rules": [{k: r[k] for k in ("id", "agency", "label")} | {"reason": REASONS[r["reason"]]}
                      for r in data.get("rules", [])],
            "stats": [{"category": k, **s} for k, s in sorted(totals.items())]}


def revoke(db, owner_id, token, *, missing_ok=False):
    owner, data = owner_state(db, owner_id, lock=True)
    rules = data.get("rules", [])
    keep = [r for r in rules if r["id"] != token]
    if len(keep) == len(rules) and not missing_ok:
        raise NotFoundError("NOT_FOUND", "反馈记忆不存在")
    data["rules"] = keep
    owner.bid_feedback_enc = encrypt(data)
