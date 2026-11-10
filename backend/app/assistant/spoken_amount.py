"""口语金额：逐字稿、智能助手消息与追问启用（V31Q-114 R3）；不猜币种或换算模糊数值。审核文档仍只用文档规则。"""
import re

from app.redaction.detect import Hit

_CN = '一二三四五六七八九十百千两零〇'
_NUM = rf'(?:\d+(?:\.\d+)?|[{_CN}]+(?:点[{_CN}]+)?)'
# 万、亿可以连写（“一亿两千万”“三万五千”“一万一千块”），整串作为一个金额，不留下半截数字。
PATTERN = re.compile(rf'(?<![\d{_CN}万亿.点])(?:'
                     rf'(?:{_NUM}多?(?:千万|万|个亿|亿))+(?:{_NUM})?多?(?:块钱|块)?'
                     rf'|(?P<core>{_NUM})多?(?P<kuai>块钱|块))')
_COUNTED = re.compile(r'(?:人|次|个(?:人|台|件)|台|件|吨|米|平方米|区域|地方|土地|石头|蛋糕)')


def _pieces(text, m):
    """“这一块”“一块儿去”“分成三块屏幕”是量词或副词，不是金额。"""
    if m['kuai'] == '块钱':
        return False
    before, after = text[m.start() - 1:m.start()], text[m.end():m.end() + 1]
    if before in tuple('这那哪每第') or after == '儿' or m['core'] == '一':
        return True
    core = m['core']
    small = float(core) < 100 if core[0].isdigit() else not re.search('[百千]', core)
    return small and not (after == '' or after == '的' or not re.match(r'\w', after))


def find_amounts(text):
    # 只增不减：文档规则照常生效（出口复检也用文档规则），本规则不撤销它的命中。
    out = []
    for m in PATTERN.finditer(text):
        after = text[m.end():]
        if _COUNTED.match(after):
            continue
        if m['kuai'] is not None and _pieces(text, m):
            continue
        # “千万别”“千万要”是副词；“上千万”“几千万”仍是金额。
        if m.group().startswith('千万') and text[m.start() - 1:m.start()] not in tuple('上几数近好'):
            continue
        out.append(Hit(m.start(), m.end(), 'AMOUNT', m.group()))
    return out


# 口语单价（V31Q-114 R3）：“每吨4800”“报价每台 1,260”“4800一吨”。只认计价单位前后的阿拉伯数字（至少 2 位），
# 数字后紧跟数量、时间单位或百分号的不算（“每台2个”“每吨含水12%”）。
_UNIT = r'(?:吨|台|件|套|个|只|箱|桶|包|袋|公斤|千克|斤|米|平方米|平米|立方米|方|升|kg|KG|Kg|MT|mt)'
_PRICE_NUM = r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,}(?:\.\d+)?'
# 中文数字单价须含十、百、千、万之一（“每吨四千八”），单个数字（“每吨一”）不算
_PRICE_NUM_CN = rf'[{_CN}]*[十百千万][{_CN}万]*'
_PRICE_AFTER_UNIT = re.compile(rf'每{_UNIT}[ \t]*(?:是|为|约|大概|大约|要|报|卖|按|在|：|:)?[ \t]*'
                               rf'(?P<n>(?:{_PRICE_NUM})(?!\d|[.,]\d)|{_PRICE_NUM_CN}(?![{_CN}]))')
# 识别视图把全角逗号折成半角：只有“逗号 / 点 + 数字”才算数字的一部分（V31Q-114）
_PRICE_BEFORE_UNIT = re.compile(rf'(?<!\d)(?<!\d[.,])(?P<n>{_PRICE_NUM})[ \t]*(?:一|每){_UNIT}(?![A-Za-z0-9])')
_NOT_PRICE_AFTER = re.compile(r'[ \t]*(?:%|％|个|台|件|套|只|天|日|小时|分钟|年|月|周|人|次|吨|米|公斤|千克|斤|号|度|℃)')


def find_unit_prices(text):
    out = []
    for m in _PRICE_AFTER_UNIT.finditer(text):
        if not _NOT_PRICE_AFTER.match(text[m.end('n'):]):
            out.append(Hit(m.start('n'), m.end('n'), 'PRICE', m['n']))
    for m in _PRICE_BEFORE_UNIT.finditer(text):
        if not any(h.start < m.end('n') and m.start('n') < h.end for h in out):
            out.append(Hit(m.start('n'), m.end('n'), 'PRICE', m['n']))
    return out


# 口述编号（V31Q-114 R7）：“合同编号用RH二零二六零三一五”。编号标签后由字母、阿拉伯数字与中文数字（含幺、两）组成、
# 至少 4 个数字的值按 REFNO；对照键把中文数字折成阿拉伯数字，与书面写法共用代号。
_CN_DIGITS = '零〇一二三四五六七八九幺两'
_CN_DIGIT_MAP = str.maketrans('零〇一二三四五六七八九幺两', '001234567891' + '2')
_SPOKEN_REF = re.compile(
    r'(?:提单编号|提单号|信用证号|报关单号|项目编号|招标编号|报价单号|运单号|集装箱号|合同编号|合同号|订单编号|订单号|发票编号|发票号|单号)'
    rf'[ \t]*(?:用|是|为|叫|写|：|:)?[ \t]*(?P<v>[A-Za-z0-9{_CN_DIGITS}][A-Za-z0-9{_CN_DIGITS}\-/]{{2,40}}[A-Za-z0-9{_CN_DIGITS}])')


def find_spoken_refs(text):
    out = []
    for m in _SPOKEN_REF.finditer(text):
        value = m['v']
        if sum(c.isdigit() or c in _CN_DIGITS for c in value) < 4 or not any(c in _CN_DIGITS for c in value):
            continue  # 纯阿拉伯数字与字母的编号由文档规则负责
        key = re.sub(r'[\s\-/]', '', value.translate(_CN_DIGIT_MAP)).upper()
        out.append(Hit(m.start('v'), m.end('v'), 'REFNO', key))
    return out
