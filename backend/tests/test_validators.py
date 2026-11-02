"""校验器的代表性构造与变异。"""
from app.redaction import validators as v


def test_checksum_validators():
    prefix = '11010120000102001'
    last = '10X98765432'[sum(int(c) * w for c, w in zip(
        prefix, (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2))) % 11]
    assert v.id_card(prefix + last)
    assert not v.id_card(prefix + ('0' if last != '0' else '1'))
    assert not v.id_card('110101200002300011')
    chars = '0123456789ABCDEFGHJKLMNPQRTUWXY'
    prefix = '91110101MA0000000'
    last = chars[(31 - sum(chars.index(c) * w for c, w in zip(
        prefix, (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28))) % 31) % 31]
    assert v.uscc(prefix + last)
    assert not v.uscc(prefix + ('0' if last != '0' else '1'))
    assert v.iban('GB82 WEST 1234 5698 7654 32')
    assert v.iban('gb82 west 1234 5698 7654 32')
    assert not v.iban('GB83WEST12345698765432')
    assert v.bic('FICTCNBJXXX')
    assert not v.bic('FICTZZBJXXX')
    prefix = '620000000000001'
    valid = next(prefix + str(n) for n in range(10) if v.luhn(prefix + str(n)))
    assert v.luhn(valid)
    assert not v.luhn(valid[:-1] + str((int(valid[-1]) + 1) % 10))
