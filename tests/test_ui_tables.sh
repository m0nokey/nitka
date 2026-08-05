#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/ui.sh
source "$ROOT_DIR/lib/ui.sh"

output="$(ui_print_table "  " "   " 0 \
    $'IP\tSTATUS\tMODE' \
    $'1\tVPN unavailable\tSSH proxy' \
    $'2\tActive\tXray')"

grep -Fqx '  IP   STATUS            MODE' <<<"$output"
grep -Fqx '  1    VPN unavailable   SSH proxy' <<<"$output"
grep -Fqx '  2    Active            Xray' <<<"$output"

printf '%s\n' 'UI table width test passed.'
