"""Argon2id password hashing. Parameters pinned explicitly so a library
upgrade cannot silently weaken them."""
from __future__ import annotations
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError


# OWASP 2024 guidance: time_cost=3, memory_cost=65536 (64 MiB), parallelism=4.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)


def hash_password(plain: str) -> str:
    """Hash a password. Returns an argon2id PHC string."""
    if not plain or len(plain) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(plain) > 1024:
        raise ValueError("password too long")
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verification. Returns False on any failure."""
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except Exception:
        return True
