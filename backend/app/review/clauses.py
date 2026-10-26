"""一级条款划分与解析结束时的任务向量缓存。"""
from dataclasses import dataclass, field

from sqlalchemy import delete, select

from app.db.models import DocNode, TaskFile, TaskClauseVector
from app.embedding import engine
from app.security.crypto import decrypt_text


@dataclass
class Clause:
    file_seq: int
    first_loc: str
    title: str
    text: str = ''
    locs: list[str] = field(default_factory=list)


def first_level(nodes) -> list[Clause]:
    out = []
    for node in nodes:
        if node.part != 'body':
            continue
        seq = int(node.loc.split('-', 1)[0][1:])
        if (not out or out[-1].file_seq != seq or node.kind == 'heading' or
                (node.clause_no and '.' not in node.clause_no)):
            out.append(Clause(seq, node.loc, node.text[:60]))
        clause = out[-1]
        clause.locs.append(node.loc)
        clause.text += ('\n' if clause.text else '') + node.text
    return out


def store_vectors(db, task):
    from types import SimpleNamespace
    rows = db.scalars(select(DocNode).join(TaskFile, TaskFile.id == DocNode.file_id)
                      .where(DocNode.task_id == task.id, DocNode.part == 'body')
                      .order_by(TaskFile.seq, DocNode.ord)).all()
    clauses = first_level([SimpleNamespace(loc=n.loc, part=n.part, kind=n.kind, clause_no=n.clause_no,
                                          text=decrypt_text(n.text_enc)) for n in rows])
    vectors = engine.embed([c.text[:2000] for c in clauses], 'passage')
    db.execute(delete(TaskClauseVector).where(TaskClauseVector.task_id == task.id))
    db.add_all(TaskClauseVector(task_id=task.id, file_seq=c.file_seq, first_loc=c.first_loc,
                               locs=c.locs, embedding=v, model=engine.model_id())
               for c, v in zip(clauses, vectors))
    db.flush()
