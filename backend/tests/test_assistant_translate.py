"""T-50：翻译分段、完整节点位置对齐与按书写系统还原。"""
import pytest


def test_t50_2_segment_9500_characters():
    from app.assistant.translate import segments
    nodes = [(f"F1-P{i}", "文" * 500) for i in range(1, 20)]
    result = segments(nodes, 4000)
    assert len(result) == 3
    assert [pair for part in result for pair in part] == nodes
    assert all(len("\n".join(f"⟦{loc}⟧ {text}" for loc, text in part)) <= 4000 for part in result)


def test_oversize_node_stays_whole():
    from app.assistant.translate import segments
    assert segments([("F1-P1", "文" * 5000), ("F1-P2", "短文")], 4000) == [
        [("F1-P1", "文" * 5000)], [("F1-P2", "短文")]]


def test_t50_5_alignment_and_whole_segment_fallback():
    from app.assistant.translate import align
    nodes = [("F1-P1", "一"), ("F1-P2", "二")]
    assert align("⟦F1-P1⟧ One\n⟦F1-P2⟧ Two", nodes) == ({"F1-P1": "One", "F1-P2": "Two"}, False)
    missing = "⟦F1-P1⟧ One\nTwo"
    assert align(missing, nodes) == ({"F1-P1": missing}, True)
    unknown = "⟦F1-P1⟧ One\n⟦F9-P9⟧ Two"
    assert align(unknown, nodes) == ({"F1-P1": unknown}, True)


def test_t50_6_target_script_mapping():
    from app.assistant.translate import target_mapping
    mapping = {"[[ORG_1]]": "星河公司", "[[ORG_1]]#1": "Xinghe Co.", "[[AMT_1]]": "12,345.00"}
    assert target_mapping(mapping, "英文") == {"[[ORG_1]]": "Xinghe Co.", "[[AMT_1]]": "12,345.00"}
    assert target_mapping(mapping, "日文")["[[ORG_1]]"] == "星河公司"


@pytest.mark.parametrize("target,alias", [("日文", "ほし会社"), ("韩文", "별빛회사"), ("俄文", "Звезда"), ("阿拉伯文", "شركة النجوم")])
def test_script_specific_alternates(target, alias):
    from app.assistant.translate import target_mapping
    assert target_mapping({"[[ORG_1]]": "星河公司", "[[ORG_1]]#1": alias}, target)["[[ORG_1]]"] == alias


@pytest.mark.parametrize("remaining", [{"monthly": 10, "daily": 2}, {"monthly": 2, "daily": None}])
def test_t50_3_quota_uses_smaller_remaining_limit(remaining):
    from app.assistant.translate import require_quota
    from app.security.errors import AppError
    with pytest.raises(AppError) as caught:
        require_quota(3, remaining)
    assert caught.value.status == 409 and caught.value.code == "ASSIST_QUOTA"
    assert caught.value.message == "翻译需要调用 3 次，今日剩余 2 次"


def test_unlimited_or_sufficient_quota():
    from app.assistant.translate import require_quota
    require_quota(3, {"monthly": None, "daily": None})
    require_quota(3, {"monthly": 3, "daily": 8})
