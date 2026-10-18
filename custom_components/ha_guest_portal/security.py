from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_PASSWORD_HASHER = PasswordHasher()
_DUMMY_HASH = _PASSWORD_HASHER.hash("not-the-password-used-by-any-account")


def verify_password(password_hash: str | None, password: str) -> bool:
    candidate = password_hash or _DUMMY_HASH
    try:
        return _PASSWORD_HASHER.verify(candidate, password) and password_hash is not None
    except (InvalidHashError, VerifyMismatchError):
        return False


def random_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(first: str | None, second: str | None) -> bool:
    return bool(first and second and secrets.compare_digest(first, second))


class RateLimiter:
    def __init__(self) -> None:
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True
