"""仅逐字稿启用的口语金额；不猜币种或换算模糊数值。"""
import re

from app.redaction.detect import Hit

PATTERN = re.compile(r'(?<![\d一二三四五六七八九十百千万亿两零〇])'
                     r'(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千两零〇]+)'
                     r'(?:多)?(?:千万|万|个亿|亿|块钱|块)(?:多|块钱|块)?')


def find_amounts(text):
    out = []
    for m in PATTERN.finditer(text):
        after = text[m.end():]
        if re.match(r'(?:人|次|个(?:人|台|件)|台|件|吨|米|平方米|区域|地方|土地|石头|蛋糕)', after):
            continue
        if m.group() in ('一万', '千万') and re.match(r'(?:一|别|不要|不可|不能|注意)', after):
            continue
        out.append(Hit(m.start(), m.end(), 'AMOUNT', m.group()))
    return out
