"""The archive must depend only on committed blobs, not checkout or zlib bytes."""

import importlib.util
import subprocess
from pathlib import Path
from shutil import which
from zipfile import ZIP_STORED, ZipFile

spec = importlib.util.spec_from_file_location(
    "lad_build", Path(__file__).parents[1] / "scripts/build_release.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_build_ignores_dirty_untracked_and_line_ending_changes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    prefix = "custom_components/limited_access_dashboard"
    for directory in (repo / prefix / "brand", repo / "brand"):
        directory.mkdir(parents=True)
        for name in ("icon.png", "icon@2x.png"):
            (directory / name).write_bytes(b"binary icon")
    (repo / prefix / "manifest.json").write_text('{"version":"0.6.1"}\n')
    source = repo / prefix / "test.py"
    source.write_bytes(b"# committed source\n")
    for arguments in (
        ["init"],
        ["-c", "core.autocrlf=false", "add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "test"],
    ):
        subprocess.run([which("git"), *arguments], cwd=repo, check=True, capture_output=True)  # noqa: S603
    monkeypatch.setattr(builder, "ROOT", repo)
    first = builder.build("HEAD", tmp_path / "first").read_bytes()
    source.write_bytes(b"# dirty checkout\r\n")
    (repo / prefix / "private.txt").write_text("must never ship")
    second_path = builder.build("HEAD", tmp_path / "second")
    assert second_path.read_bytes() == first
    with ZipFile(second_path) as archive:
        assert archive.read(f"{prefix}/test.py") == b"# committed source\n"
        assert f"{prefix}/private.txt" not in archive.namelist()
        assert all(item.compress_type == ZIP_STORED for item in archive.infolist())
