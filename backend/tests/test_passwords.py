"""密码哈希与强度规则测试（SPEC 6.6、17.1）。"""
import pytest

from app.security.passwords import (
    MAX_LENGTH,
    WeakPasswordError,
    dummy_verify,
    hash_password,
    is_strong_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)


def test_hash_is_argon2id_and_salted():
    h1 = hash_password("Passw0rd!")
    h2 = hash_password("Passw0rd!")
    assert h1.startswith("$argon2id$")
    assert h1 != h2
    assert "Passw0rd" not in h1


def test_verify_correct_and_wrong():
    h = hash_password("abc12345")
    assert verify_password(h, "abc12345") is True
    assert verify_password(h, "abc12346") is False
    assert verify_password(h, "") is False


@pytest.mark.parametrize("bad_hash", ["", "not-a-hash", "$argon2id$v=19$broken"])
def test_verify_with_corrupt_hash_returns_false(bad_hash):
    assert verify_password(bad_hash, "abc12345") is False


def test_needs_rehash():
    assert needs_rehash(hash_password("abc12345")) is False
    assert needs_rehash("not-a-hash") is True


def test_dummy_verify_runs_without_error():
    dummy_verify("anything1")


@pytest.mark.parametrize(
    "password",
    ["abcd1234", "A1bcdefg", "12345678a", "密码abc12345", "a1" * 4, "x" * (MAX_LENGTH - 1) + "1"],
)
def test_strong_passwords_accepted(password):
    assert is_strong_password(password)
    validate_password_strength(password)


@pytest.mark.parametrize(
    "password",
    [
        "",
        "abc1234",  # 7 位
        "abcdefgh",  # 没有数字
        "12345678",  # 没有字母
        "密码密码密码密码1",  # 中文不算字母
        "!!!!!!!!",
        "a" * MAX_LENGTH + "1",  # 超过上限
    ],
)
def test_weak_passwords_rejected(password):
    assert not is_strong_password(password)
    with pytest.raises(WeakPasswordError) as exc:
        validate_password_strength(password)
    assert exc.value.status == 422
    assert exc.value.code == "PASSWORD_TOO_WEAK"
    # 错误消息不回显密码
    if password:
        assert password not in exc.value.message
