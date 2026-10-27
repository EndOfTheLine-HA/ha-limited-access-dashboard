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


def _metadata(path: Path) -> dict[str, str]:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='lad_storage'").fetchone():
            return {}
        return dict(db.execute("SELECT key, value FROM lad_storage"))


def mark_database(path: Path, entry_id: str) -> None:
    """Bind a verified managed database to its configuration entry."""
    if not is_dashboard_database(path):
        raise ValueError("Managed database is not a Limited Access Dashboard database")
    identity = _metadata(path).get("entry_id")
    if identity and identity != entry_id:
        raise ValueError("Dashboard database belongs to another configuration entry")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS lad_storage (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        db.execute("INSERT OR REPLACE INTO lad_storage VALUES ('entry_id', ?)", (entry_id,))


def _row_counts(db: sqlite3.Connection) -> dict[str, int]:
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
    return {
        # Identifiers come from sqlite_schema and are double-quote escaped.
        name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]  # noqa: S608
        for name in tables
    }


def _discard_temporary(path: Path) -> None:
    candidates = (
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
    )
    if any(item.is_symlink() or (item.exists() and not item.is_file()) for item in candidates):
        raise ValueError("Unsafe migration temporary file; inspect it before retrying")
    for item in candidates:
        item.unlink(missing_ok=True)


def _copy_snapshot(source: Path, target: Path, entry_id: str, origin: str) -> None:
    temporary = target.with_name(target.name + ".pending")
    _discard_temporary(temporary)
    try:
        with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as old:
            # Keep counts and backup on the same read snapshot, including WAL data.
            old.execute("BEGIN")
            counts = _row_counts(old)
            with closing(sqlite3.connect(temporary)) as new:
                old.backup(new)
                if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Dashboard database integrity check failed")
                if _row_counts(new) != counts:
                    raise ValueError("Dashboard migration row counts do not match")
                new.execute(
                    "CREATE TABLE IF NOT EXISTS lad_storage "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                new.executemany(
                    "INSERT OR REPLACE INTO lad_storage VALUES (?, ?)",
                    [("entry_id", entry_id), ("migration_source", origin)],
                )
                new.commit()
        temporary.replace(target)
    finally:
        _discard_temporary(temporary)


def _finish_migration(source: Path, destination: Path, data_directory: Path, entry_id: str) -> None:
    if not source.exists() or source.resolve().parent != data_directory.resolve():
        return
    archive = data_directory / f"{entry_id}.db.migrated"
    if archive.exists() or archive.is_symlink():
        if (
            not is_dashboard_database(archive)
            or _metadata(archive).get("entry_id") != entry_id
            or _metadata(archive).get("migration_source") != str(source.resolve())
        ):
            raise ValueError("Unrecognized migration backup; original database retained")
    else:
        _copy_snapshot(destination, archive, entry_id, str(source.resolve()))
    # A verified, consistent migration backup remains until this entry is removed.
    remove_database(source, data_directory)


def remove_migration_backup(data_directory: Path, entry_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", entry_id):
        raise ValueError("Invalid configuration entry ID")
    path = data_directory / f"{entry_id}.db.migrated"
    if path.resolve().parent != data_directory.resolve():
        return
    if is_dashboard_database(path) and _metadata(path).get("entry_id") == entry_id:
        path.unlink()


def prepare_database(
    data_directory: Path,
    entry_id: str,
    slug: str,
    previous_path: str | None = None,
    policy: str = "new",
) -> Path:
    """Only old entries discover slug files; new entries never adopt orphan data.

    A completed copy carries its entry ID and source identity inside the DB,
    allowing restart before config-entry persistence without guessing at files.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", entry_id):
        raise ValueError("Invalid configuration entry ID")
    if policy not in {"legacy", "new", "import"}:
        raise ValueError("Invalid database migration policy")
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        raise ValueError("Invalid dashboard URL name")
    data_directory.mkdir(parents=True, exist_ok=True)
    destination = data_directory / f"{entry_id}.db"
    source = (
        Path(previous_path)
        if previous_path
        else (data_directory / f"{slug}.db" if policy == "legacy" else destination)
    )
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise ValueError("Dashboard database must be a regular file")
    if source.resolve() == destination.resolve():
        if destination.exists():
            mark_database(destination, entry_id)
        elif previous_path and policy != "import":
            raise ValueError(
                "Configured dashboard database is missing; restore it before upgrading"
            )
        return destination
    if destination.exists():
        if not is_dashboard_database(destination):
            raise ValueError("Unrecognized managed dashboard database")
        metadata = _metadata(destination)
        completed = metadata.get("entry_id") == entry_id and metadata.get(
            "migration_source"
        ) == str(source.resolve())
        if source.exists() and not completed:
            raise ValueError(
                "Both old and new dashboard databases exist without migration identity"
            )
        mark_database(destination, entry_id)
        if completed:
            _finish_migration(source, destination, data_directory, entry_id)
        return destination
    if not source.exists():
        if previous_path and policy != "import":
            raise ValueError(
                "Configured dashboard database is missing; restore it before upgrading"
            )
        return destination
    if not is_dashboard_database(source):
        raise ValueError("Legacy database is not a Limited Access Dashboard database")
    # v0.6.1 used this temporary name. A verified source permits a safe retry.
    _discard_temporary(destination.with_suffix(".migrating"))
    _copy_snapshot(source, destination, entry_id, str(source.resolve()))
    _finish_migration(source, destination, data_directory, entry_id)
    if source.resolve().parent != data_directory.resolve():
        _LOGGER.warning("Imported external dashboard database retained at %s", source)
    return destination
