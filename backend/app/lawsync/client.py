"""固定官方出口。公开函数不接受任务、正文或任意请求参数。"""
import json
import logging
import re
import threading
import time
from datetime import date
from urllib.parse import urlsplit, unquote

import httpx

from app.config import get_settings

API_HOST = 'flk.npc.gov.cn'
FILE_HOST = 'flkoss.obs-bj2.cucloud.cn'
_ID = re.compile(r'^[0-9a-f]{32}$')
_lock = threading.Lock()
_last_request = None
# httpx 的 INFO 请求日志会包含带签名的全文地址；只保留固定的本模块错误码。
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)


class SyncError(Exception):
    """固定错误码，不保存响应、URL 或底层异常。"""


def _request(method, url, body=None, *, file=False):
    global _last_request
    limit = (20 if file else 5)*1024*1024
    try:
        # 串行覆盖开始请求与读完响应，多个线程也不能绕过节流。
        with _lock:
            if _last_request is not None:
                time.sleep(max(0, 2-(time.monotonic()-_last_request)))
            _last_request = time.monotonic()
            with httpx.Client(timeout=30, follow_redirects=False, trust_env=False,
                              proxy=get_settings().lawsync_proxy or None) as connection:
                with connection.stream(method, url, json=body) as response:
                    if response.status_code != 200:
                        raise SyncError('BLOCKED')
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        if len(data)+len(chunk) > limit:
                            raise SyncError('BAD_RESPONSE')
                        data.extend(chunk)
        if file:
            if bytes(data).lstrip().lower().startswith((b'<!doctype html',b'<html')):
                raise SyncError('BLOCKED')
            return bytes(data)
        if bytes(data).lstrip().startswith(b'<'):
            raise SyncError('BLOCKED')
        result = json.loads(data)
        if not isinstance(result,dict) or type(result.get('code')) is not int or result['code'] != 200:
            raise SyncError('BAD_RESPONSE')
        return result
    except httpx.HTTPError:
        raise SyncError('NETWORK') from None
    except (ValueError, TypeError):
        raise SyncError('BAD_RESPONSE') from None


def _day(value):
    if value is None:
        return True
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def list_page(category_codes: tuple[int, ...], status_codes: tuple[int, ...], page: int, page_size: int) -> dict:
    if (type(category_codes) is not tuple or not category_codes or
        any(type(c) is not int or c not in (102,210,320,330,340) for c in category_codes) or
        type(status_codes) is not tuple or not status_codes or
        any(type(c) is not int or c not in (1,2,3,4) for c in status_codes) or
        type(page) is not int or not 1 <= page <= 60 or type(page_size) is not int or not 1 <= page_size <= 100):
        raise ValueError('同步查询参数不正确')
    data = _request('POST', f'https://{API_HOST}/law-search/search/list',
                    {'searchRange':1,'searchType':2,'searchContent':'',
                     'sxrq':[],'gbrq':[],'gbrqYear':[],'zdjgCodeId':[],
                     'flfgCodeId':list(category_codes),'sxx':list(status_codes),
                     'pageNum':page,'pageSize':page_size})
    rows, total = data.get('rows'), data.get('total')
    if not isinstance(rows,list) or len(rows)>page_size or type(total) is not int or total<0:
        raise SyncError('BAD_RESPONSE')
    normalized = []
    for row in rows:
        # 官方字段仅在出口内归一化，领域层继续使用既有契约。
        if isinstance(row,dict):
            row = dict(row,published_on=row.get('gbrq'),effective_on=row.get('sxrq'),
                       category=row.get('flxz'),office=row.get('zdjgName'))
        if (not isinstance(row,dict) or not isinstance(row.get('bbbs'),str) or not _ID.fullmatch(row['bbbs']) or
            not isinstance(row.get('title'),str) or not 0<len(row['title'].strip())<=300 or
            not _day(row.get('published_on')) or not _day(row.get('effective_on')) or
            (row.get('sxx') is not None and (type(row['sxx']) is not int or row['sxx'] not in (1,2,3,4))) or
            not isinstance(row.get('category'),str) or not 0<len(row['category'])<=60 or
            (row.get('office') is not None and (not isinstance(row['office'],str) or len(row['office'])>100))):
            raise SyncError('BAD_RESPONSE')
        normalized.append(row)
    data['rows'] = normalized
    return data


def download_urls(flk_ids: list[str]) -> list[str]:
    if type(flk_ids) is not list or not 1<=len(flk_ids)<=100 or any(type(i) is not str or not _ID.fullmatch(i) for i in flk_ids):
        raise ValueError('官方编号不正确')
    data = _request('POST',f'https://{API_HOST}/law-search/download/batch',
                    [{'bbbs':i,'format':'docx'} for i in flk_ids])
    files = data.get('data')
    urls = [r.get('url') if isinstance(r,dict) else None for r in files] if isinstance(files,list) else None
    if not isinstance(urls,list) or len(urls)!=len(flk_ids) or any(not isinstance(u,str) for u in urls):
        raise SyncError('BAD_RESPONSE')
    for url in urls:
        try: _file_url(url)
        except ValueError: raise SyncError('BAD_RESPONSE') from None
    return urls


def _file_url(url):
    if type(url) is not str:
        raise ValueError('全文地址不正确')
    try:
        parts=urlsplit(url)
        path=unquote(parts.path)
        if (parts.scheme!='https' or parts.netloc!=FILE_HOST or parts.username or parts.password or
            parts.fragment or not path.startswith('/prod/') or '\\' in path or
            any(p in ('.','..') for p in path.split('/')) or any(ord(c)<32 for c in url)):
            raise ValueError('全文地址不正确')
    except (TypeError,ValueError):
        raise ValueError('全文地址不正确') from None


def download_file(url: str) -> bytes:
    _file_url(url)
    return _request('GET',url,file=True)
