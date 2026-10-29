"""国家、企业类型、常见用语词典（M2-05，SPEC 9.4、9.5）。

词典文件在 backend/resources/ 下，启动后只读取一次并缓存。
英文词条按单词边界匹配、不区分大小写（“Iran”不命中“Irani”）；中文词条按原样子串匹配。
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

RESOURCES_DIR = Path(__file__).resolve().parents[2] / "resources"

_DEFAULT_ORG_TYPE = "类型未知"
# 中国行政区划：省、自治区、市、县、区、镇、乡前至少 2 个汉字（R1-06）
_CN_ADMIN = re.compile(r"[一-鿿]{2,}(?:省|自治区|市|县|区|镇|乡)")


def _term_pattern(term: str) -> str:
    """把一个词条转成正则。首尾是英文字母或数字时，要求两侧不是英文字母或数字。"""
    body = re.escape(term)
    if term[0].isascii() and term[0].isalnum():
        body = r"(?<![A-Za-z0-9])" + body
    if term[-1].isascii() and term[-1].isalnum():
        body = body + r"(?![A-Za-z0-9])"
    return body


@lru_cache(maxsize=1)
def _country_index() -> tuple[re.Pattern, dict[str, str]]:
    """返回 (合并后的正则, 小写词条到国家名的映射)。

    同一个词条在词典中只能属于一个国家（测试保证），所以映射是唯一的。
    """
    with open(RESOURCES_DIR / "countries.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    mapping: dict[str, str] = {}
    for entry in data["countries"]:
        name = entry["name"]
        for term in [name, *entry.get("aliases", []), *entry.get("places", [])]:
            mapping[term.casefold()] = name
    # 长的词条排在前面：同一位置上优先取最长的一个
    terms = sorted(mapping, key=len, reverse=True)
    pattern = re.compile("|".join(_term_pattern(t) for t in terms), re.IGNORECASE)
    return pattern, mapping


def find_country(text: str) -> str | None:
    """返回文本中最长的一处国家或地区名对应的中文名；没有则返回 None。

    匹配只看词条本身：多个词条重叠时取最长的；长度相同时取出现在前面的。
    词典中查不到时，含中国行政区划（山东省、临邑县、朝阳区等）的判为“中国”。
    """
    pattern, mapping = _country_index()
    best = None
    for match in pattern.finditer(text):
        if best is None or len(match.group()) > len(best.group()):
            best = match
    if best is None:
        return "中国" if _CN_ADMIN.search(text) else None
    return mapping[best.group().casefold()]


@lru_cache(maxsize=1)
def _org_types() -> tuple[tuple[str, tuple[re.Pattern, ...]], ...]:
    with open(RESOURCES_DIR / "org_types.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return tuple(
        (
            entry["name"],
            tuple(re.compile(_term_pattern(k), re.IGNORECASE) for k in entry["keywords"]),
        )
        for entry in data["types"]
    )


def find_org_type(name: str) -> str:
    """按企业名称中的关键词返回类型；types 按顺序检查，先命中先定，都不命中为“类型未知”。"""
    for type_name, patterns in _org_types():
        if any(p.search(name) for p in patterns):
            return type_name
    return _DEFAULT_ORG_TYPE


@lru_cache(maxsize=1)
def common_terms() -> frozenset[str]:
    """常见合同用语（SPEC 9.5 用于排除疑似遗漏）。每行一个，# 开头为注释，空行忽略。"""
    terms = set()
    with open(RESOURCES_DIR / "common_terms.txt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.add(line)
    return frozenset(terms)


@lru_cache(maxsize=1)
def cn_surnames() -> tuple[str, ...]:
    """中文姓氏：忽略注释与空行，复姓优先（R4 11.1）。"""
    with open(RESOURCES_DIR / "cn_surnames.txt", encoding="utf-8") as f:
        terms = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    return tuple(sorted(terms, key=len, reverse=True))


@lru_cache(maxsize=1)
def hanzi_pinyin() -> dict[str, tuple[str, ...]]:
    """离线 GB2312 汉字全部读音；资源由固定 Unihan 版本生成。"""
    rows = (RESOURCES_DIR / 'hanzi_pinyin.txt').read_text(encoding='utf-8').splitlines()
    return {parts[0]: tuple(parts[1:]) for row in rows if row and not row.startswith('#')
            if len(parts := row.split()) > 1}


@lru_cache(maxsize=1)
def en_words() -> frozenset[str]:
    """SCOWL size 50 普通词；来源及完整许可见 en_words.README.md。"""
    return frozenset((RESOURCES_DIR / 'en_words.txt').read_text(encoding='utf-8').splitlines())
