"""Build the native integration archive from tracked runtime assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "limited_access_dashboard"


def main():
    version = json.loads((COMPONENT / "manifest.json").read_text())["version"]
    destination = ROOT / "dist" / f"limited-access-dashboard-{version}.zip"
    destination.parent.mkdir(exist_ok=True)
    files = sorted(
        path
        for path in COMPONENT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    for name in ("icon.png", "icon@2x.png"):
        if (ROOT / "brand" / name).read_bytes() != (COMPONENT / "brand" / name).read_bytes():
            raise ValueError(f"Brand copies differ: {name}")
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_symlink():
                raise ValueError(f"Cannot package symlink: {path}")
            relative = path.relative_to(ROOT).as_posix()
            info = ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        for path in files:
            assert archive.read(path.relative_to(ROOT).as_posix()) == path.read_bytes()
    print(f"{destination}: {len(files)} runtime files")
    print(f"SHA256: {hashlib.sha256(destination.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
