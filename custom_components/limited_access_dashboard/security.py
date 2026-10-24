from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_PASSWORD_HASHER = PasswordHasher()
_DUMMY_HASH = _PASSWORD_HASHER.hash("not-the-password-used-by-any-account")

MIN_PASSWORD_LENGTH = 12
# Bounds the work an unauthenticated login request can make Argon2 do.
MAX_PASSWORD_LENGTH = 256
MAX_USERNAME_LENGTH = 64


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must contain at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must contain at most {MAX_PASSWORD_LENGTH} characters")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Check a password. Always runs Argon2, even without an account hash.

    Blocking (about 100 ms on a Raspberry Pi): call it from an executor.
    """
    candidate = password_hash or _DUMMY_HASH
    try:
        return _PASSWORD_HASHER.verify(candidate, password) and password_hash is not None
    except (InvalidHashError, VerifyMismatchError):
        return False


def credential_fingerprint(password_hash: str) -> str:
    """Identifies the current password without revealing anything about it.

    Each Argon2 hash carries a fresh random salt, so any password change, even
    to the same password, gives a new fingerprint and ends existing sessions.
    """
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()


def random_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(first: str | None, second: str | None) -> bool:
    return bool(first and second and secrets.compare_digest(first, second))


class RateLimiter:
    """Sliding-window limiter whose memory stays bounded under attack.

    Keys with no recent events are dropped, and at most `max_keys` keys are
    kept: the least recently used is evicted first. Callers must not build keys
    from unbounded user input.
    """

    def __init__(self, max_keys: int = 4096) -> None:
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events.pop(key, None) or deque()
            while events and events[0] <= cutoff:
                events.popleft()
            allowed = len(events) < limit
            if allowed:
                events.append(now)
            if events:
                self._events[key] = events
                while len(self._events) > self._max_keys:
                    self._events.popitem(last=False)
            return allowed

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
