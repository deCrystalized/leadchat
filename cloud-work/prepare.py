"""Restore the exact reviewed input into a new cloud working directory."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or not path.parts:
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def main() -> None:
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / "manifest.json").read_text())
    destination = package.parent / "design-workspace"
    if len(sys.argv) == 2:
        destination = Path(sys.argv[1]).resolve()
    elif len(sys.argv) > 2:
        raise ValueError("Usage: prepare.py [new-destination-directory]")

    chunks = []
    for part in manifest["parts"]:
        name = safe_name(part["path"])
        data = package.joinpath(*name.parts).read_bytes()
        if len(data) != part["bytes"] or digest(data) != part["sha256"]:
            raise ValueError(f"Archive part mismatch: {name}")
        chunks.append(data)
    archive_data = b"".join(chunks)
    if digest(archive_data) != manifest["archiveSha256"]:
        raise ValueError("Archive checksum mismatch")

    expected = {row["path"]: row for row in manifest["files"]}
    if len(expected) != len(manifest["files"]):
        raise ValueError("Duplicate manifest path")
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        entries = archive.infolist()
        if len(entries) != len(expected) or {row.filename for row in entries} != set(expected):
            raise ValueError("Archive inventory mismatch")
        # Validate every entry before writing any file, including existing work.
        for entry in entries:
            path = safe_name(entry.filename)
            if stat.S_ISLNK(entry.external_attr >> 16) or entry.is_dir():
                raise ValueError(f"Non-file archive entry: {entry.filename}")
            row = expected[entry.filename]
            if entry.file_size != row["bytes"] or digest(archive.read(entry)) != row["sha256"]:
                raise ValueError(f"Source mismatch: {entry.filename}")
            target = destination.joinpath(*path.parts)
            for ancestor in [target, *target.parents]:
                if ancestor.is_symlink():
                    raise ValueError(f"Symlink in destination: {entry.filename}")
                if ancestor == destination:
                    break
            if target.exists() and (not target.is_file() or digest(target.read_bytes()) != row["sha256"]):
                raise ValueError(f"Existing work differs; no files changed: {entry.filename}")
        for entry in entries:
            target = destination.joinpath(*safe_name(entry.filename).parts)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
            target.chmod(0o755 if expected[entry.filename].get("executable") else 0o644)
    print(json.dumps({"restoredFiles": len(expected), "destination": str(destination),
                      "frontend": manifest["frontend"], "native": manifest["native"],
                      "cloudTaskStarted": False}))


if __name__ == "__main__":
    main()
