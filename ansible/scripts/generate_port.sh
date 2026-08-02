#!/usr/bin/env bash
set -Eeuo pipefail

generated_ports=()

_rand32() {
    od -An -N4 -tu4 /dev/urandom | tr -d ' '
}

_rand_range() {
    local min="${1:-}" max="${2:-}" range lim r
    [[ -n "$min" && -n "$max" && "$min" =~ ^[0-9]+$ && "$max" =~ ^[0-9]+$ && "$min" -lt "$max" ]] || {
        printf '%s\n' "invalid range values" >&2
        return 1
    }
    range=$((max - min + 1))
    lim=$(((4294967296 / range) * range - 1))
    while :; do
        r="$(_rand32)"
        ((r <= lim)) && break
    done
    printf '%s\n' $((r % range + min))
}

_bad_bot_patterns() {
    local s="${1-}"
    local n=${#s} i=0
    ((n == 0)) && return 1
    for ((i = 0; i + 1 < n; i++)); do
        [[ "${s:i:1}" == "${s:i+1:1}" ]] && return 0
    done
    [[ "$s" == *01234* || "$s" == *12345* || "$s" == *23456* || "$s" == *34567* || "$s" == *45678* || "$s" == *56789* ]] && return 0
    if ((n >= 5)); then
        for ((i = 0; i + 4 < n; i++)); do
            [[ "${s:i:1}" == "${s:i+2:1}" && "${s:i+2:1}" == "${s:i+4:1}" ]] && return 0
        done
    fi
    if ((n == 5)); then
        [[ "${s:0:1}" == "${s:4:1}" && "${s:1:1}" == "${s:3:1}" ]] && return 0
        [[ "${s:0:1}" == "${s:3:1}" && "${s:1:1}" == "${s:4:1}" ]] && return 0
    fi
    if ((n >= 4)); then
        for ((i = 0; i + 3 < n; i++)); do
            [[ "${s:i:1}" == "${s:i+2:1}" && "${s:i+1:1}" == "${s:i+3:1}" ]] && return 0
        done
    fi
    return 1
}

generate_port() {
    local min="${1:-}" max="${2:-}" port prev diff i d1 d2 ok
    [[ -n "$min" && -n "$max" && "$min" =~ ^[0-9]+$ && "$max" =~ ^[0-9]+$ && "$min" -lt "$max" ]] || {
        printf '%s\n' "invalid range values" >&2
        return 1
    }
    while :; do
        port="$(_rand_range "$min" "$max")"
        if ((${#generated_ports[@]} > 0)); then
            for prev in "${generated_ports[@]}"; do
                [[ "$prev" == "$port" ]] && continue 2
            done
        fi
        _bad_bot_patterns "$port" && continue
        ok=1
        for ((i = 0; i < ${#port} - 1; i++)); do
            d1=${port:$i:1}
            d2=${port:$i+1:1}
            diff=$((d1 > d2 ? d1 - d2 : d2 - d1))
            ((diff < 2)) && {
                ok=0
                break
            }
        done
        ((ok)) || continue
        generated_ports+=("$port")
        printf '%s\n' "$port"
        break
    done
}

generate_port "$@"
