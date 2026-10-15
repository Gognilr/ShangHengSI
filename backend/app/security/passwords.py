"""密码哈希与强度规则（SPEC 6.6、17.1）。

哈希使用 argon2id（argon2-cffi 默认参数，即 RFC 9106 推荐的低内存配置）。
密码明文不得出现在日志和异常消息中。
"""
import re
from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.security.errors import AppError

MIN_LENGTH = 8
# SPEC 只规定下限；设上限是为了防止超长输入拖慢哈希（open_questions Q-27）
MAX_LENGTH = 128

_LETTER = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"[0-9]")

_hasher = PasswordHasher(type=Type.ID)


class WeakPasswordError(AppError):
    def __init__(self) -> None:
        super().__init__(
            422,
            "PASSWORD_TOO_WEAK",
            f"新密码至少 {MIN_LENGTH} 位（不超过 {MAX_LENGTH} 位），须同时包含字母和数字",
        )


def is_strong_password(password: str) -> bool:
    """至少 8 位，同时含英文字母和数字（SPEC 6.6）。"""
    return (
        MIN_LENGTH <= len(password) <= MAX_LENGTH
        and _LETTER.search(password) is not None
        and _DIGIT.search(password) is not None
    )


def validate_password_strength(password: str) -> None:
    if not is_strong_password(password):
        raise WeakPasswordError()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """校验密码。哈希格式损坏时也返回 False，不抛出异常。"""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """哈希参数低于当前配置时返回 True，登录成功后可据此重新哈希。"""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash("dummy-password-for-timing-0")


def dummy_verify(password: str) -> None:
    """用户名不存在时也做一次同样耗时的校验，避免通过响应时间判断用户名是否存在。"""
    verify_password(_dummy_hash(), password)
