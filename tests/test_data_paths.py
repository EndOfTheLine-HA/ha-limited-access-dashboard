"""Storage migration, URL independence and deletion boundary regressions."""

import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "lad_paths",
    Path(__file__).parents[1] / "custom_components/limited_access_dashboard/data_paths.py",
)
paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paths)


def database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            "CREATE TABLE sessions(token_hash, username, csrf_token, created_at, expires_at);"
            "CREATE TABLE audit_log(occurred_at, username, action_id, outcome, remote_ip);"
            "CREATE TABLE designs(name PRIMARY KEY, document);"
            "INSERT INTO designs VALUES('owner', 'saved layout');"
            "INSERT INTO designs VALUES('guest', 'guest layout');"
            "INSERT INTO sessions VALUES('token', 'guest', 'csrf', 'start', 'expiry');"
        )


def test_migrates_and_survives_url_rename_without_reusing_legacy_slug(tmp_path):
    root = tmp_path / "nonstandard-ha-config" / paths.DATA_DIRECTORY
    old = root / "guest.db"
    database(old)
    new = paths.prepare_database(root, "ENTRY1", "guest", policy="legacy")
    assert new == root / "ENTRY1.db"
    assert not old.exists()
    assert paths.is_dashboard_database(root / "ENTRY1.db.migrated")
    assert paths.prepare_database(root, "ENTRY1", "renamed", str(new)) == new
    with closing(sqlite3.connect(new)) as db:
        assert db.execute("SELECT count(*) FROM designs").fetchone()[0] == 2
        assert db.execute("SELECT token_hash FROM sessions").fetchone()[0] == "token"
    other = paths.prepare_database(root, "ENTRY2", "guest", policy="new")
    assert other != new and not other.exists()


def test_external_import_is_copied_and_never_deleted(tmp_path):
    root = tmp_path / paths.DATA_DIRECTORY
    external = tmp_path / "external.db"
    database(external)
    new = paths.prepare_database(root, "ENTRY", "guest", str(external))
    assert new.exists() and external.exists()
    paths.remove_database(external, root)
    assert external.exists()
    paths.remove_database(new, root)
    assert not new.exists()


def test_rejects_recorder_and_arbitrary_paths(tmp_path):
    root = tmp_path / paths.DATA_DIRECTORY
    root.mkdir()
    recorder = root / "home-assistant_v2.db"
    with closing(sqlite3.connect(recorder)) as db:
        db.execute("CREATE TABLE states(entity_id)")
    paths.remove_database(recorder, root)
    assert recorder.exists()
    with pytest.raises(ValueError, match="not a Limited"):
        paths.prepare_database(root, "ENTRY", "guest", str(recorder))
    with pytest.raises(ValueError, match="missing"):
        paths.prepare_database(root, "ENTRY", "guest", str(root / "missing.db"))
    with pytest.raises(ValueError, match="entry ID"):
        paths.prepare_database(root, "../outside", "guest")


def test_migration_includes_uncheckpointed_wal_data(tmp_path):
    root = tmp_path / paths.DATA_DIRECTORY
    old = root / "guest.db"
    database(old)
    with closing(sqlite3.connect(old)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO designs VALUES('latest', 'from WAL')")
        live.commit()
        # Model a legacy external file: preserve it while another process reads it.
        destination = paths.prepare_database(root / "new", "ENTRY", "guest", str(old))
        with closing(sqlite3.connect(destination)) as db:
            assert db.execute("SELECT document FROM designs WHERE name='latest'").fetchone() == (
                "from WAL",
            )


def test_deletion_rejects_symlink_sidecars(tmp_path):
    root = tmp_path / paths.DATA_DIRECTORY
    target = root / "entry.db"
    database(target)
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("keep")
    sidecar = target.with_name(target.name + "-wal")
    try:
        sidecar.symlink_to(unrelated)
    except OSError:
        pytest.skip("Creating symlinks is unavailable")
    paths.remove_database(target, root)
    assert target.exists() and unrelated.read_text() == "keep"


def test_new_dashboard_never_adopts_orphan_layouts_or_resources(tmp_path):
    root = tmp_path / paths.DATA_DIRECTORY
    orphan = root / "guest.db"
    database(orphan)
    destination = paths.prepare_database(root, "NEW", "guest", policy="new")
    assert destination == root / "NEW.db"
    assert not destination.exists()
    assert orphan.exists()
    assert not (root / "NEW.db.migrated").exists()


@pytest.mark.parametrize("external", [False, True])
def test_restart_after_copy_before_entry_save_recovers(tmp_path, monkeypatch, external):
    root = tmp_path / paths.DATA_DIRECTORY
    source = tmp_path / "external.db" if external else root / "guest.db"
    database(source)
    finish = paths._finish_migration

    def interrupted(*args):
        raise RuntimeError("simulated process stop before config entry persistence")

    monkeypatch.setattr(paths, "_finish_migration", interrupted)
    with pytest.raises(RuntimeError):
        paths.prepare_database(root, "ENTRY", "guest", str(source))
    assert source.exists() and (root / "ENTRY.db").exists()
    monkeypatch.setattr(paths, "_finish_migration", finish)
    destination = paths.prepare_database(root, "ENTRY", "guest", str(source))
    assert paths.is_dashboard_database(destination)
    assert source.exists() is external
    if not external:
        backup = root / "ENTRY.db.migrated"
        assert paths.is_dashboard_database(backup)
        paths.remove_migration_backup(root, "OTHER")
        assert backup.exists()
        paths.remove_migration_backup(root, "ENTRY")
        assert not backup.exists()


@pytest.mark.parametrize("temporary", ["ENTRY.migrating", "ENTRY.db.pending"])
def test_partial_copy_is_retried_from_verified_original(tmp_path, temporary):
    root = tmp_path / paths.DATA_DIRECTORY
    source = root / "guest.db"
    database(source)
    (root / temporary).write_bytes(b"interrupted incomplete copy")
    destination = paths.prepare_database(root, "ENTRY", "guest", policy="legacy")
    assert paths.is_dashboard_database(destination)
    assert not (root / temporary).exists()


def test_new_yaml_import_can_start_without_existing_database(tmp_path):
    root = tmp_path / paths.DATA_DIRECTORY
    external = tmp_path / "not-created-yet.db"
    destination = paths.prepare_database(root, "ENTRY", "guest", str(external), "import")
    assert destination == root / "ENTRY.db" and not destination.exists()
    assert not external.exists()


def test_unknown_dual_databases_fail_closed(tmp_path):
    root = tmp_path / paths.DATA_DIRECTORY
    source = root / "guest.db"
    database(source)
    database(root / "ENTRY.db")
    with pytest.raises(ValueError, match="without migration identity"):
        paths.prepare_database(root, "ENTRY", "guest", policy="legacy")
    assert source.exists()
