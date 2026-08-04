#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_HOME="$(mktemp -d)"
TEST_STATE_HOME="$TEST_HOME/.local/state"
trap 'rm -rf "$TEST_HOME"' EXIT

run_tui() {
    local input="$1" output status=0

    if output="$(printf '%s' "$input" | timeout 10 env \
        HOME="$TEST_HOME" \
        XDG_STATE_HOME="$TEST_STATE_HOME" \
        bash "$ROOT_DIR/controller/entrypoint.sh" 2>&1)"; then
        :
    else
        status=$?
        printf '%s\n' "$output" >&2
        printf 'TUI exited unexpectedly with status %s.\n' "$status" >&2
        return 1
    fi
    TEST_OUTPUT="$output"
}

run_tui $'x\n'
[[ "$TEST_OUTPUT" == *"1. VPN servers"* ]]
[[ "$TEST_OUTPUT" == *"2. Add VPN server"* ]]
[[ "$TEST_OUTPUT" == *"3. Vault"* ]]
[[ "$TEST_OUTPUT" != *"3. Cascade VPN"* ]]

run_tui $'2\nb\nx\n'
[[ "$TEST_OUTPUT" == *"1. Single-node Xray"* ]]
[[ "$TEST_OUTPUT" == *"2. Cascade VPN"* ]]

run_tui $'3\nb\nx\n'

mkdir -p "$TEST_STATE_HOME/nitka/backups/user"
printf '%s\n' '{"nodes":{}}' >"$TEST_HOME/vault.json"
tar -C "$TEST_HOME" -czf \
    "$TEST_STATE_HOME/nitka/backups/user/vault-20260729T050736Z.tar.gz" \
    vault.json

run_tui $'3\n2\nb\nx\n'
[[ "$TEST_OUTPUT" == *"User backup | 2026-07-29 05:07:36 UTC"* ]]
[[ "$TEST_OUTPUT" == *"Path: $TEST_STATE_HOME/nitka/backups/user/vault-20260729T050736Z.tar.gz"* ]]
