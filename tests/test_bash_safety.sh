#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# Bash expands every RHS in one `local` command before assigning any of its
# names. Under `set -u`, this is unsafe when a default value references
# another variable declared earlier in the same command.
matches="$(python3 - "$ROOT_DIR" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
assignment = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=")
reference = re.compile(r"\$\{[0-9]+:-\$([A-Za-z_][A-Za-z0-9_]*)")

for directory in (root / "controller", root / "lib"):
    for path in sorted(directory.glob("*.sh")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not re.match(r"^[ \t]*local(?:[ \t]|$)", line):
                continue
            assigned = {match.group(1) for match in assignment.finditer(line)}
            for match in reference.finditer(line):
                if match.group(1) in assigned:
                    print(f"{path}:{line_number}:{line}")
PY
 )" || true

if [[ -n "$matches" ]]; then
    printf '%s\n' "Unsafe dependent local declaration found:" >&2
    printf '%s\n' "$matches" >&2
    exit 1
fi

printf '%s\n' 'Bash local declaration safety test passed.'
