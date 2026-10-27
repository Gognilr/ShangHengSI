"""T-49 第 5、6 条：代号跨片段时不展示半个代号，offset 按还原后的字符位置计算。"""
import pytest


def test_t49_5_6_thirty_deltas_restore_and_offsets():
    from app.assistant.stream import incremental
    chunks = ["说明"] * 14 + ["[[OR", "G_1]] 的"] + ["后续"] * 14
    assert len(chunks) == 30
    mapping = {"[[ORG_1]]": "虚构星河贸易有限公司"}
    content, shown, offset = "", "", 0
    for chunk in chunks:
        content += chunk
        delta, next_offset = incremental(content, mapping, offset)
        assert "[[" not in delta
        assert next_offset >= offset
        shown += delta
        offset = next_offset
    expected = "说明" * 14 + "虚构星河贸易有限公司 的" + "后续" * 14
    assert shown == expected and offset == len(expected)
    assert incremental(content, mapping, offset) == ("", offset)


@pytest.mark.parametrize("split", range(1, len("开头[[ORG_12]]结尾")))
def test_each_character_split_keeps_offsets_monotonic(split):
    from app.assistant.stream import incremental
    full = "开头[[ORG_12]]结尾"
    mapping = {"[[ORG_12]]": "虚构示例公司"}
    first, offset = incremental(full[:split], mapping, 0)
    second, final_offset = incremental(full, mapping, offset)
    assert first + second == "开头虚构示例公司结尾"
    assert final_offset == len(first + second)
    assert "[[" not in first


def test_only_neutral_mapping_and_unknown_codes():
    from app.assistant.stream import incremental
    text = "[[ORG_1]]、[[ORG_99]]、[公司_Q1]"
    result, offset = incremental(text, {"[[ORG_1]]": "虚构企业", "[公司_Q1]": "不应还原"}, 0)
    assert result == "虚构企业、[[ORG_99]]、[公司_Q1]" and offset == len(result)


def test_incomplete_code_is_withheld_and_terminal_literal_bracket_is_visible():
    from app.assistant.stream import incremental
    assert incremental("正文[[OR", {}, 0) == ("正文", 2)
    assert incremental("正文[", {}, 0) == ("正文", 2)
    assert incremental("正文[", {}, 0, finished=True) == ("正文[", 3)
