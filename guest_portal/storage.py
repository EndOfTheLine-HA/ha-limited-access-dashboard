from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .security import random_token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Session:
    username: str
    csrf_token: str
    expires_at: datetime


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    username TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    remote_ip TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_occurred_at ON audit_log(occurred_at);
                """
            )

    def create_session(self, username: str, expires_at: datetime) -> tuple[str, Session]:
        token = random_token()
        csrf_token = random_token()
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (_token_hash(token), username, csrf_token, now.isoformat(), expires_at.isoformat()),
            )
        return token, Session(username=username, csrf_token=csrf_token, expires_at=expires_at)

    def get_session(self, token: str, now: datetime) -> Session | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT username, csrf_token, expires_at FROM sessions WHERE token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= now:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),)
                )
                return None
            return Session(row["username"], row["csrf_token"], expires_at)

    def delete_session(self, token: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def log(
        self,
        *,
        username: str,
        action_id: str,
        outcome: str,
        detail: str,
        remote_ip: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_log
                    (occurred_at, username, action_id, outcome, detail, remote_ip)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    username,
                    action_id,
                    outcome,
                    detail[:300],
                    remote_ip[:100],
                ),
            )
