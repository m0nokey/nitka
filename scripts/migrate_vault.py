#!/usr/bin/env python3
"""Migrate a decrypted Nitka Vault JSON document to schema v2.

The encrypted Vault is handled by the shell Vault layer.  This utility only
transforms the decrypted JSON and never replaces the input before the new
document has passed canonical schema validation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Allow the command to be run as `python3 scripts/migrate_vault.py ...` from
# the repository root without requiring callers to set PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_schema import VAULT_SCHEMA_VERSION, migrate_state, sync_canonical_state


def _read(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
    except OSError as exc:
        raise SystemExit(f"cannot read Vault JSON: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Vault JSON is invalid: {exc}") from exc
    if not isinstance(state, dict):
        raise SystemExit("Vault JSON must contain an object")
    return state


def _write_atomic(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="decrypted Vault JSON")
    parser.add_argument("output", type=Path, nargs="?", help="migrated JSON output")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="replace the input after creating a .v1.backup copy",
    )
    args = parser.parse_args()

    if args.in_place and args.output is not None:
        parser.error("--in-place cannot be combined with output")
    if not args.in_place and args.output is None:
        parser.error("provide output or use --in-place")

    source = args.input.resolve()
    target = source if args.in_place else args.output.resolve()
    state = _read(source)
    migrated = sync_canonical_state(migrate_state(state))

    if args.in_place:
        backup = source.with_name(f"{source.name}.v1.backup")
        shutil.copy2(source, backup)
        _write_atomic(source, migrated)
    else:
        _write_atomic(target, migrated)

    print(
        f"Migrated Vault JSON to schema v{VAULT_SCHEMA_VERSION}: {target}"
        + (f" (backup: {source.with_name(source.name + '.v1.backup')})" if args.in_place else "")
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"migration rejected: {exc}") from exc
