"""维护组逐批向量化；删除范本后不再写入。"""
import uuid
from sqlalchemy import select
from app.db.models import StandardClause,StandardTemplate
from app.embedding import engine
from app.security.crypto import decrypt_text
from app.worker import job_handler


@job_handler('template_embed')
def handle_embed(db,payload):
    tid=uuid.UUID(payload['template_id'])
    while True:
        row=db.get(StandardTemplate,tid,with_for_update=True,populate_existing=True)
        if row is None:return
        items=db.scalars(select(StandardClause).where(StandardClause.template_id==tid,StandardClause.embedding.is_(None))
                         .order_by(StandardClause.seq).limit(16).with_for_update()).all()
        if not items:db.commit();return
        vectors=engine.embed([decrypt_text(c.text_enc) for c in items],'passage')
        for c,v in zip(items,vectors):c.embedding=v
        db.commit()
