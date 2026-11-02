from app.redaction.normalize import normalize_value, normalize_with_map
from app.parsing.model import strip_invisible


def test_normalization_maps_every_output_to_original():
    result = normalize_with_map('臺\u200bＡа一\U000e0061')
    assert result.text == '台aa1'
    assert result.index_map == [0, 2, 3, 4]
    assert normalize_value('臺\u200bＡа一') == result.text
    assert normalize_with_map('臺\u200bＡа一', preserve_case=True).text == '台Aa1'
    assert strip_invisible('甲\u202e\u061c\u2064\u2069\U000e0061乙') == '甲乙'
