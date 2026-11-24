"""维护组逐批向量化；删除范本后不再写入。"""
import uuid
from sqlalchemy import select
from app.db.models import ContentDraft,StandardClause,StandardTemplate
from app.embedding import engine
from app.security.crypto import decrypt_text
from app.worker import job_handler


@job_handler('template_embed')
def handle_embed(db,payload):
    if "content_id" in payload:
        row = db.get(ContentDraft, uuid.UUID(payload["content_id"]), with_for_update=True)
        if row is not None and row.kind == "template" and row.status == "active" and row.embedding is None:
            row.embedding, = engine.embed([decrypt_text(row.text_enc)], "passage")
        db.commit()
        return
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
