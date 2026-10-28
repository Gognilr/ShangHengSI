"""T-50：五个模板的参数、固定消息、原样说明与回复草稿边界。"""
import uuid

import pytest

FILES = {str(uuid.UUID(int=i)): i for i in range(1, 8)}
IDS = list(FILES)


@pytest.mark.parametrize("template,params,message", [
    ("translate", {"file_ids": IDS[:2], "target": "英文"}, ""),
    ("translate", {"file_id": IDS[0], "target": "意大利文"}, ""),
    ("translate", {"target": "英文"}, ""),
    ("summary", {"file_ids": IDS[:4]}, ""),
    ("summary", {"file_ids": IDS[:1], "focus": "自定义正文"}, ""),
    ("crosscheck", {"file_ids": IDS[:1]}, ""),
    ("compare", {"file_ids": IDS[:7]}, ""),
    ("compare", {"file_ids": [IDS[0], IDS[0]]}, ""),
    ("reply", {"file_ids": [], "tone": "正式", "language": "中文"}, ""),
    ("reply", {"file_ids": IDS[:1], "tone": "强硬", "language": "中文"}, ""),
    ("reply", {"file_ids": IDS[:1], "tone": "正式", "language": "法文"}, ""),
    ("summary", {"file_ids": [str(uuid.uuid4())]}, ""),
])
def test_t50_1_invalid_parameters(template, params, message):
    from app.assistant.templates import prepare
    from app.security.errors import AppError
    with pytest.raises(AppError) as caught:
        prepare(template, params, FILES, message)
    assert caught.value.status == 422


@pytest.mark.parametrize("target", ["中文", "英文", "日文", "韩文", "俄文", "西班牙文", "法文", "德文", "阿拉伯文"])
def test_all_translation_targets(target):
    from app.assistant.templates import prepare
    result = prepare("translate", {"file_id": IDS[1], "target": target}, FILES)
    assert result.params["file_ids"] == [IDS[1]] and result.params["target"] == target
    assert result.message == f"把文件 2 翻译为{target}"


def test_template_defaults_and_pasted_reply():
    from app.assistant.templates import prepare
    summary = prepare("summary", {"file_ids": IDS[:2]}, FILES)
    assert summary.params["focus"] == "全部" and summary.message == "概括文件 1、2 的要点"
    reply = prepare("reply", {"file_ids": [], "tone": "正式", "language": "同来函"}, FILES, "普通来函正文")
    assert reply.message == "普通来函正文"
    assert prepare("crosscheck", {"file_ids": IDS[:3]}, FILES).message == "核对文件 1～3 之间是否一致"
    assert prepare("compare", {"file_ids": IDS[:3]}, FILES).message == "比较文件 1～3 的报价"


def test_t50_11_draft_only():
    from app.assistant.templates import draft_only
    assert draft_only("对方要求说明\n\n---\n\n尊敬的客户：这是草稿") == "尊敬的客户：这是草稿"
    assert draft_only("没有草稿分隔线") == ""


@pytest.mark.parametrize("template", ["translate", "summary", "reply", "crosscheck", "compare"])
def test_t50_12_literal_template_resource(template):
    from app.assistant.templates import instruction, RESOURCE
    text = RESOURCE.read_text(encoding="utf-8")
    block = text.split(f"## {template}\n", 1)[1].split("\n## ", 1)[0].strip()
    assert instruction(template) == block
