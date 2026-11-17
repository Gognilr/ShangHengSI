"""标书项目的个人文件夹（最多三级）与全公司分类（V34-B-01a）。

文件夹只属于本人：他人的文件夹一律 404；删除时子文件夹一并删除，其中的项目由外键置空，回到“未归档”。
分类由业务管理员维护；停用的分类不能再选，已用的项目保留。
"""
import uuid

from sqlalchemy import func, select

from app.db.models import BidCategory, BidFolder, BidProject, Task
from app.security.errors import AppError, NotFoundError

MAX_DEPTH = 3


def folder(db, user, raw) -> BidFolder:
    try:
        row = db.get(BidFolder, uuid.UUID(str(raw)))
    except ValueError:
        row = None
    if row is None or row.owner_id != user.id:
        raise NotFoundError("FOLDER_NOT_FOUND", "文件夹不存在")
    return row


def _all(db, user) -> list[BidFolder]:
    return list(db.scalars(select(BidFolder).where(BidFolder.owner_id == user.id).order_by(BidFolder.seq, BidFolder.name)))


def depth(rows: dict, row: BidFolder) -> int:
    n = 1
    while row.parent_id is not None:
        row, n = rows[row.parent_id], n + 1
    return n


def subtree(db, user, root: uuid.UUID) -> list[uuid.UUID]:
    """文件夹及其全部下级的 id（筛选时包含子文件夹里的项目）。"""
    children: dict = {}
    for row in _all(db, user):
        children.setdefault(row.parent_id, []).append(row.id)
    out, stack = [], [root]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out


def tree(db, user, filters=()) -> dict:
    """平铺的文件夹（前端按 parent_id 组树）与各自直接包含的项目数，另给全部与未归档的数量。"""
    base = (Task.owner_id == user.id, Task.kind == "bid")
    counts = dict(db.execute(select(BidProject.folder_id, func.count()).join(Task).where(*base, *filters)
                             .group_by(BidProject.folder_id)).all())
    rows = _all(db, user)
    return {"folders": [{"id": r.id, "parent_id": r.parent_id, "name": r.name, "count": counts.get(r.id, 0)} for r in rows],
            "all": sum(counts.values()), "unfiled": counts.get(None, 0)}


def _name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise AppError(422, "FOLDER_NAME", "请填写文件夹名称")
    return name


def create(db, user, name: str, parent_id) -> BidFolder:
    parent = folder(db, user, parent_id) if parent_id else None
    if parent is not None and depth({r.id: r for r in _all(db, user)}, parent) >= MAX_DEPTH:
        raise AppError(422, "FOLDER_TOO_DEEP", f"文件夹最多 {MAX_DEPTH} 级")
    siblings = select(func.max(BidFolder.seq)).where(BidFolder.owner_id == user.id,
                                                     BidFolder.parent_id == parent.id if parent else BidFolder.parent_id.is_(None))
    row = BidFolder(owner_id=user.id, parent_id=parent.id if parent else None, name=_name(name), seq=(db.scalar(siblings) or 0) + 1)
    db.add(row)
    db.flush()
    return row


def rename(db, user, fid, name: str) -> BidFolder:
    row = folder(db, user, fid)
    row.name = _name(name)
    return row


def remove(db, user, fid) -> None:
    db.delete(folder(db, user, fid))  # 子文件夹级联删除；项目的 folder_id 由外键置空


# ---------------------------------------------------------------- 分类

def categories(db) -> list[dict]:
    rows = db.scalars(select(BidCategory).order_by(BidCategory.seq, BidCategory.id))
    return [{"id": c.id, "name": c.name, "enabled": c.enabled} for c in rows]


def choose_category(db, raw, current: int | None = None) -> int | None:
    """新选的分类必须存在且启用；项目原来的分类即使已停用也可保留。"""
    if raw is None:
        return None
    if raw == current:
        return current
    row = db.get(BidCategory, raw)
    if row is None or not row.enabled:
        raise AppError(422, "CATEGORY_UNAVAILABLE", "该分类已停用或不存在，请重新选择")
    return row.id


def category_name(db, category_id: int | None) -> str | None:
    row = db.get(BidCategory, category_id) if category_id is not None else None
    return row.name if row else None
