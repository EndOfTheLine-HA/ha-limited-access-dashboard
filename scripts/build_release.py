"""Build a byte-reproducible native archive from a committed Git snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from shutil import which
from zipfile import ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = "custom_components/limited_access_dashboard/"


def build(ref: str, output_directory: Path) -> Path:
    git = which("git")
    if git is None or ref.startswith("-"):
        raise ValueError("Git and a valid commit ref are required")

    def run(*arguments):
        return subprocess.run(  # noqa: S603
            [git, *arguments], cwd=ROOT, check=True, capture_output=True
        ).stdout

    files = {}
    listing = run("ls-tree", "-rz", ref, "--", COMPONENT, "brand/icon.png", "brand/icon@2x.png")
    for record in listing.split(b"\0"):
        if not record:
            continue
        header, filename = record.split(b"\t", 1)
        mode, kind, object_id = header.split()
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise ValueError(f"Cannot package non-file: {filename!r}")
        files[filename.decode("utf-8")] = run("cat-file", "blob", object_id.decode("ascii"))
    version = json.loads(files[f"{COMPONENT}manifest.json"])["version"]
    for name in ("icon.png", "icon@2x.png"):
        if files[f"brand/{name}"] != files[f"{COMPONENT}brand/{name}"]:
            raise ValueError(f"Brand copies differ: {name}")
    output_directory.mkdir(parents=True, exist_ok=True)
    destination = output_directory / f"limited-access-dashboard-{version}.zip"
    # No compression: DEFLATE bytes otherwise depend on the platform's zlib build.
    with ZipFile(destination, "w", compression=ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            if not name.startswith(COMPONENT):
                continue
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            assert archive.read(name) == files[name]
        print(f"{destination}: {len(archive.namelist())} runtime files from {ref}")
    print(f"SHA256: {hashlib.sha256(destination.read_bytes()).hexdigest()}")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ref", default="HEAD", help="Committed Git ref; working-tree edits are excluded"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    build(args.ref, args.output_dir)


if __name__ == "__main__":
    main()
