#!/usr/bin/env python3
"""Extract Daytona sources without overlaying existing local changes."""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOTS = ("daytona", "environments")
REQUIRED = (
    "daytona/shared_mcp_daytona.py",
    "daytona/shared_mcp_config.py",
    "daytona/deploy/image-lock.json",
    "environments/__init__.py",
    "environments/daytona_env.py",
)


class SetupError(Exception):
    """An archive or destination cannot be used safely."""


def unpack(archive: Path, stage: Path) -> None:
    """Validate all members before extracting into a fresh staging directory."""
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        if len(entries) > 5000:
            raise SetupError("Daytona archive contains too many members")
        seen: set[str] = set()
        files: set[str] = set()
        size = 0
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            normalized = path.as_posix()
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if (
                not name
                or "\\" in name
                or name.startswith("/")
                or any(part in (".", "..") for part in name.split("/") if part)
                or not path.parts
                or path.parts[0] not in ROOTS
                or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                or (entry.is_dir() and kind == stat.S_IFREG)
                or (not entry.is_dir() and kind == stat.S_IFDIR)
                or normalized in seen
            ):
                raise SetupError(f"Unsafe or duplicate archive member: {name!r}")
            seen.add(normalized)
            if not entry.is_dir():
                files.add(normalized)
            size += entry.file_size
            if size > 128 * 1024 * 1024:
                raise SetupError("Daytona archive exceeds the uncompressed size limit")
        if not all(name in files for name in REQUIRED):
            raise SetupError("Daytona archive is missing required files")
        zf.extractall(stage)
        for entry in entries:
            if not entry.is_dir():
                (stage / entry.filename).chmod((entry.external_attr >> 16) & 0o777 or 0o644)


def comparable_files(root: Path) -> dict[str, Path]:
    """Ignore generated caches, but refuse symlinks when comparing sources."""
    files: dict[str, Path] = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in dirs:
            if (base / name).is_symlink():
                raise SetupError(f"Refusing to compare a symlink: {base / name}")
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        for name in names:
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise SetupError(f"Refusing to compare a non-regular file: {path}")
            if name.endswith((".pyc", ".pyo")) or name == ".DS_Store":
                continue
            files[path.relative_to(root).as_posix()] = path
    return files


def matches(existing: Path, extracted: Path) -> bool:
    left, right = comparable_files(existing), comparable_files(extracted)
    return left.keys() == right.keys() and all(
        filecmp.cmp(left[name], right[name], shallow=False)
        and bool(left[name].stat().st_mode & 0o111) == bool(right[name].stat().st_mode & 0o111)
        for name in left
    )


def setup(archive: Path, destination: Path) -> None:
    if not destination.is_dir():
        raise SetupError(f"Destination directory does not exist: {destination}")
    with tempfile.TemporaryDirectory(prefix=".daytona-setup-", dir=destination) as tmp:
        stage = Path(tmp)
        unpack(archive, stage)
        # Preflight both roots before installing either one; never overlay sources.
        for name in ROOTS:
            target = destination / name
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise SetupError(f"{target} is not a regular directory; move it aside before setup")
            if target.is_dir() and not matches(target, stage / name):
                raise SetupError(f"{target} differs from {archive}; preserve it and move it aside")
        for name in ROOTS:
            target = destination / name
            if target.is_dir():
                print(f"Using {target} (contents match the Daytona archive)")
            else:
                shutil.copytree(stage / name, target, symlinks=False)
                print(f"Extracted {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("daytona.zip"))
    parser.add_argument("--destination", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        setup(args.archive, args.destination)
    except (SetupError, OSError, zipfile.BadZipFile) as exc:
        print(f"Daytona setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
