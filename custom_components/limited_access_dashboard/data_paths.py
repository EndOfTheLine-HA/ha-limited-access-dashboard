"""Stable, managed dashboard databases and conservative legacy migration."""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from pathlib import Path

_LOGGER = logging.getLogger(__name__)
DATA_DIRECTORY = "limited-access-dashboard-data"


def is_dashboard_database(path: Path) -> bool:
    """Never treat an arbitrary SQLite file as integration-owned data."""
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            required = {
                "sessions": {"token_hash", "username", "csrf_token", "created_at", "expires_at"},
                "audit_log": {"occurred_at", "username", "action_id", "outcome", "remote_ip"},
            }
            return all(
                columns <= {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                for table, columns in required.items()
            )
    except sqlite3.Error:
        return False


def remove_database(path: Path, data_directory: Path) -> None:
    """Only delete a verified dashboard DB directly inside the managed directory."""
    root = data_directory.resolve()
    candidates = (path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm"))
    if (
        path.suffix != ".db"
        or path.resolve().parent != root
        or any(item.is_symlink() for item in candidates)
        or not is_dashboard_database(path)
    ):
        _LOGGER.warning("Not removing unmanaged or unrecognized database: %s", path.name)
        return
    for candidate in candidates:
        candidate.unlink(missing_ok=True)


def prepare_database(
    data_directory: Path, entry_id: str, slug: str, previous_path: str | None = None
) -> Path:
    """Copy legacy data once to an entry-ID filename, independent of URL names."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", entry_id):
        raise ValueError("Invalid configuration entry ID")
    data_directory.mkdir(parents=True, exist_ok=True)
    destination = data_directory / f"{entry_id}.db"
    source = Path(previous_path) if previous_path else data_directory / f"{slug}.db"
    if destination.is_symlink():
        raise ValueError("Dashboard database must not be a symlink")
    if source.resolve() == destination.resolve():
        return destination
    if destination.exists():
        # A completed migration may be retried before config entry persistence.
        if source.exists():
            raise ValueError("Both old and new dashboard databases exist; refusing to guess")
        return destination
    if not source.exists():
        if previous_path:
            raise ValueError(
                "Configured dashboard database is missing; restore it before upgrading"
            )
        return destination
    if not is_dashboard_database(source):
        raise ValueError("Legacy database is not a Limited Access Dashboard database")
    temporary = destination.with_suffix(".migrating")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("Incomplete database migration; inspect the .migrating file")
    try:
        with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as old:
            with closing(sqlite3.connect(temporary)) as new:
                old.backup(new)
                if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Dashboard database integrity check failed")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    # External YAML databases are copied, but never deleted by the integration.
    if source.resolve().parent == data_directory.resolve():
        remove_database(source, data_directory)
    else:
        _LOGGER.warning("Imported external dashboard database retained at %s", source)
    return destination
