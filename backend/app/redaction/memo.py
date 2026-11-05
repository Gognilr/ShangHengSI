"""逐段识别的进程内缓存（V31-U5F-03 续）。

识别函数只取决于段文字（和列提示等少量参数）与固定词典；决定一个不确定项后整份重算时，绝大多数段文字不变，
按参数缓存命中结果即可只为变了的段重新识别。只在进程内、有上限，不写日志、不持久化；每次返回新列表。
"""
from functools import lru_cache

SIZE = 32768


def by_text(fn):
    cached = lru_cache(maxsize=SIZE)(lambda *args: tuple(fn(*args)))
    if fn.__code__.co_argcount == 2:
        def call(text, column=None):
            return list(cached(text, column))
    else:
        def call(text):
            return list(cached(text))
    call.__doc__, call.__name__, call.__wrapped__ = fn.__doc__, fn.__name__, fn
    call.cache_clear = cached.cache_clear
    return call
