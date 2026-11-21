#!/usr/bin/env python3
"""Independently validate a completed FFD Backup Toolkit manifest."""
import argparse
import json
from pathlib import Path
from ffd_backup import hash_file


def main():
    parser = argparse.ArgumentParser(description="Validate checksums in an FFD Backup Toolkit export")
    parser.add_argument("backup", type=Path, help="Path to FFD_Backup_* folder")
    args = parser.parse_args()
    folder = args.backup.resolve()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    failures = 0
    files = manifest.get("files", [])
    for entry in files:
        file = (folder / entry["path"]).resolve()
        if not file.is_relative_to(folder) or not file.is_file():
            print(f"MISSING/UNSAFE: {entry['path']}")
            failures += 1
            continue
        expected_bytes, expected_hash = entry["bytes"], entry["sha256"]
        if file.stat().st_size != expected_bytes or hash_file(file) != expected_hash:
            print(f"FAILED: {entry['path']}")
            failures += 1
        else:
            print(f"OK: {entry['path']}")
    print(f"{len(files)} recorded files, {failures} checksum failures; export status: {manifest.get('status')}")
    return 0 if failures == 0 and manifest.get("status") in ("completed", "completed_with_warnings", "apks_only") else 1


if __name__ == "__main__":
    raise SystemExit(main())
