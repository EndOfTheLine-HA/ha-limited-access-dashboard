from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
_DUMMY_HASH = _PASSWORD_HASHER.hash("not-the-user-password")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    candidate = password_hash or _DUMMY_HASH
    try:
        return _PASSWORD_HASHER.verify(candidate, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def random_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(left: str | None, right: str | None) -> bool:
    return bool(left and right and hmac.compare_digest(left, right))


class RateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        attempts = self._attempts[key]
        threshold = now - window_seconds
        while attempts and attempts[0] <= threshold:
            attempts.popleft()
        if len(attempts) >= limit:
            return False
        attempts.append(now)
        return True
