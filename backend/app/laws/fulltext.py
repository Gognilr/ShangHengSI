"""法规全文压缩包的有界读取、预览和原子替换。"""
import io
import re
import zipfile
from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import delete, select

from app.db.base import utcnow
from app.db.models import Law, LawArticle, LawImportArticle
from app.laws.articles import split
from app.laws.catalog import CatalogError
from app.parsing import docx, legacy
from app.tasks import filecheck


def preview(db, record, data):
    diff = {'matched': [], 'unmatched': []}
    seen = set()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        raise CatalogError('全文文件不是有效的 ZIP 压缩包') from None
    with archive:
        infos = archive.infolist()
        if (len(infos) > 200 or sum(i.file_size for i in infos) > 200*1024*1024 or
                any(i.file_size > 20*1024*1024 for i in infos)):
            raise CatalogError('全文压缩包解压后超过限制')
        for info in infos:
            path = info.filename.replace('\\', '/')
            if PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or re.match(r'^[A-Za-z]:', path):
                raise CatalogError('全文压缩包含不安全路径')
        db.execute(delete(LawImportArticle).where(LawImportArticle.import_id == record.id))
        for info in infos:
            name = PurePosixPath(info.filename.replace('\\', '/')).name
            match = re.fullmatch(r'(.+)_(\d{8})\.(docx|doc)', name, re.I) if not info.is_dir() else None
            reason = '文件名或格式不符合要求'
            law = None
            if match:
                try:
                    published = datetime.strptime(match[2], '%Y%m%d').date()
                    law = db.scalar(select(Law).where(Law.title == match[1], Law.published_on == published))
                except ValueError:
                    pass
                reason = '法规目录中没有对应版本'
            if law is not None:
                if law.id in seen:
                    reason = '同一法规版本重复，只取第一个文件'
                else:
                    value = archive.read(info)
                    # 按内容识别旧 DOC，官方有扩展名为 DOCX 的 OLE 文件。
                    if value.startswith(filecheck.OLE_MAGIC):
                        filecheck.check_ole(io.BytesIO(value), 'doc', '法规全文')
                        value = legacy.convert(value, 'doc')
                    filecheck.check_ooxml(io.BytesIO(value), 'docx', '法规全文')
                    articles = split(docx.parse_docx(value, 1).nodes)
                    reason = '未识别到条文'
                    if articles:
                        seen.add(law.id)
                        db.add_all(LawImportArticle(import_id=record.id, title=law.title,
                                   published_on=law.published_on, article_no=a.article_no,
                                   seq=a.seq, text=a.text) for a in articles)
                        diff['matched'].append(dict(law_id=law.id, title=law.title,
                            published_on=law.published_on.isoformat(), articles=len(articles),
                            replace=law.fulltext_at is not None))
                        continue
            diff['unmatched'].append({'file': info.filename[:255], 'reason': reason})
    record.diff = diff
    record.added = sum(not m['replace'] for m in diff['matched'])
    record.changed = sum(m['replace'] for m in diff['matched'])
    record.skipped = len(diff['unmatched'])


def apply(db, record):
    from app.laws.job import enqueue_embed
    from app.laws.imports import _stale
    for item in record.diff['matched']:
        law = db.get(Law, item['law_id'], populate_existing=True)
        if (law is None or law.title != item['title'] or law.published_on is None or
                law.published_on.isoformat() != item['published_on']):
            _stale()
        articles = db.scalars(select(LawImportArticle).where(LawImportArticle.import_id == record.id,
                              LawImportArticle.title == law.title,
                              LawImportArticle.published_on == law.published_on)
                              .order_by(LawImportArticle.seq)).all()
        if len(articles) != item['articles']:
            _stale()
        db.execute(delete(LawArticle).where(LawArticle.law_id == law.id))
        db.add_all(LawArticle(law_id=law.id, article_no=a.article_no, seq=a.seq, text=a.text) for a in articles)
        law.fulltext_at = utcnow()
    db.execute(delete(LawImportArticle).where(LawImportArticle.import_id == record.id))
    enqueue_embed(db)
