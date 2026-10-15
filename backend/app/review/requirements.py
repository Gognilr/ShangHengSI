"""内置审核要求与系统提示词加载（M2-06，SPEC 11.2、12）。

内置要求存放于 resources/builtin_requirements.yaml，系统提示词存放于 resources/prompts/system.md。
两者都在首次使用时读取一次并缓存；修改任一文件须同时更新其中的 version。
"""
from dataclasses import dataclass
from functools import lru_cache

import yaml

from app.redaction.dictionaries import RESOURCES_DIR

_ALL = "all"


@dataclass(frozen=True)
class Requirement:
    id: str
    source: str  # builtin / company / note
    dimension: str | None
    text: str


@lru_cache(maxsize=1)
def _builtin_data() -> dict:
    with open(RESOURCES_DIR / "builtin_requirements.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def builtin_version() -> str:
    return str(_builtin_data()["version"])


def _matches(values: list[str], value: str) -> bool:
    return _ALL in values or value in values


def select_builtin(doc_type: str, stance: str, source: str) -> list[Requirement]:
    """按主文件类型、立场、来源筛选内置要求，保持 YAML 中的顺序。

    stance 为 sales / purchase / other；source 为 outgoing（我方发出）/ incoming（对方发来）。
    条目中写 all 的字段匹配任意值。
    """
    selected = []
    for item in _builtin_data()["requirements"]:
        if (
            _matches(item["doc_types"], doc_type)
            and _matches(item["stances"], stance)
            and _matches(item["sources"], source)
        ):
            selected.append(
                Requirement(
                    id=item["id"],
                    source="builtin",
                    dimension=item.get("dimension"),
                    text=item["text"],
                )
            )
    return selected


@lru_cache(maxsize=1)
def _system_template() -> tuple[str, str]:
    with open(RESOURCES_DIR / "prompts" / "system.md", encoding="utf-8") as f:
        text = f.read()
    # 文件开头是 YAML front matter：---\nversion: "..."\n---\n，之后是正文
    _, front, body = text.split("---\n", 2)
    version = str(yaml.safe_load(front)["version"])
    return version, body.lstrip("\n")


def load_system_template() -> tuple[str, str]:
    """返回 (prompt_version, 模板正文)。正文中的 {JSON_SCHEMA} 占位由调用方替换。"""
    return _system_template()
