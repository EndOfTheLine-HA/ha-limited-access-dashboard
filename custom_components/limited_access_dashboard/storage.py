from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .security import random_token, token_hash

# The audit log is written for unauthenticated login attempts, so both its
# row count and the size of each field are capped.
AUDIT_LOG_MAX_ROWS = 5000
AUDIT_FIELD_MAX_LENGTH = 200


@dataclass(frozen=True)
class Session:
    username: str
    csrf_token: str
    expires_at: datetime
    # Fingerprint of the password hash the session was created with. None for
    # sessions created before fingerprints existed; those are never accepted.
    credential: str | None = None


class Store:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Commit (or roll back) and always close: `with sqlite3.connect()` only commits."""
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS designs (name TEXT PRIMARY KEY, document TEXT NOT NULL)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    credential TEXT
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}
            if "credential" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN credential TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    username TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    remote_ip TEXT NOT NULL
                )
                """
            )

    def create_session(
        self, username: str, expires_at: datetime, credential: str
    ) -> tuple[str, Session]:
        token = random_token()
        csrf_token = random_token()
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))
            connection.execute(
                """
                INSERT INTO sessions
                    (token_hash, username, csrf_token, created_at, expires_at, credential)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash(token),
                    username,
                    csrf_token,
                    now.isoformat(),
                    expires_at.isoformat(),
                    credential,
                ),
            )
        return token, Session(username, csrf_token, expires_at, credential)

    def get_design(self, name: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document FROM designs WHERE name = ?", (name,)
            ).fetchone()
        return json.loads(row["document"]) if row else None

    def save_design(self, name: str, document: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO designs (name, document) VALUES (?, ?)",
                (name, json.dumps(document, allow_nan=False)),
            )
            if name == "owner":
                connection.execute("DELETE FROM designs WHERE name = 'guest'")

    def get_session(self, token: str) -> Session | None:
        now = datetime.now(UTC)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT username, csrf_token, expires_at, credential
                FROM sessions WHERE token_hash = ?
                """,
                (token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= now:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),)
                )
                return None
            return Session(row["username"], row["csrf_token"], expires_at, row["credential"])

    def delete_session(self, token: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def log(
        self,
        *,
        username: str,
        action_id: str,
        outcome: str,
        detail: str,
        remote_ip: str,
    ) -> None:
        values = [
            str(value)[:AUDIT_FIELD_MAX_LENGTH]
            for value in (username, action_id, outcome, detail, remote_ip)
        ]
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_log
                    (occurred_at, username, action_id, outcome, detail, remote_ip)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.now(UTC).isoformat(), *values),
            )
            connection.execute(
                "DELETE FROM audit_log WHERE id <= ?",
                (cursor.lastrowid - AUDIT_LOG_MAX_ROWS,),
            )

    def recent_audit(self, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT occurred_at, username, action_id, outcome, detail, remote_ip "
                    "FROM audit_log ORDER BY id DESC LIMIT ?",
                    (min(100, max(1, limit)),),
                )
            ]
