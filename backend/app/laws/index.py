"""L-10 的本地法规快照：只读取正文实际引用的标题与允许的别名。"""
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select

from app.db.models import Law

logger = logging.getLogger(__name__)
PREFIX = '中华人民共和国'
# 保留导入目录的分类原文；这些名称对应已核实的法律子类。
LAW_CATEGORIES = ('法律', '宪法相关法', '民法商法', '行政法', '经济法',
                  '社会法', '生态环境法', '刑法', '诉讼与非诉讼程序法')


def canonical(title: str) -> str:
    return title.removeprefix(PREFIX)


@dataclass(frozen=True)
class LawVersion:
    status: str
    published_on: date | None
    repealed_on: date | None
    replaced_by: str | None


LawIndex = dict[str, list[LawVersion]]


def load(db, titles: set[str]) -> LawIndex:
    if not titles:
        return {}
    # 全称与短名统一为同一键，两种库内标题的所有版本都参与判定，不派生其他简称。
    names = {name for title in titles for name in (canonical(title), PREFIX+canonical(title))}
    try:
        # 读库报错只回滚保存点，后续校验结果仍可在原事务中持久化。
        with db.begin_nested():
            rows = db.execute(select(Law.title, Law.status, Law.published_on, Law.repealed_on, Law.replaced_by)
                              .where(Law.title.in_(names), or_(Law.category.in_(LAW_CATEGORIES + ('行政法规', '司法解释')),
                                                             Law.source == 'builtin'))
                              .order_by(Law.published_on.desc().nullslast(), Law.id.desc())).all()
        index: LawIndex = {}
        for title, status, published, repealed, replacement in rows:
            index.setdefault(canonical(title), []).append(LawVersion(status, published, repealed, replacement))
        return index
    except Exception:
        # 异常文字可能包含查询标题或其他任务原文，只写固定提示，且不输出堆栈。
        logger.warning('法规库读取失败，本次跳过废止法律校验')
        return {}
