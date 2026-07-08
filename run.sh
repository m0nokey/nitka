#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
tcp_dir="${script_dir}/tcp"
compose_file="${script_dir}/compose.yml"
state_root="${NITKA_STATE_ROOT:-${XDG_STATE_HOME:-${HOME}/.local/state}/nitka}"
vault_dir="${state_root}/vault"
vault_file="${vault_dir}/secrets.env.vault"
generated_dir="${state_root}/materialized"
project_state="${generated_dir}/project.env"
port_generator="${tcp_dir}/scripts/generate_port.sh"
vault_password_file=""
quiet_run_files=()
runner_image_ready="no"
nitka_debug="${NITKA_DEBUG:-0}"

export NITKA_STATE_DIR="${generated_dir}"

theme::init() {
    if [[ -t 1 ]]; then
        COLOR_RESET=$'\033[0m'
        COLOR_TEXT=$'\033[97m'
        COLOR_HEADER=$'\033[38;5;183m'
        COLOR_LINE=$'\033[38;5;117m'
        COLOR_INFO=$'\033[38;5;117m'
        COLOR_WARN=$'\033[38;5;221m'
        COLOR_ERROR=$'\033[38;5;203m'
        COLOR_DEBUG=$'\033[38;5;147m'
        COLOR_TRACE=$'\033[38;5;110m'
        COLOR_SUBTITLE=$'\033[38;5;189m'
        COLOR_MUTED=$'\033[38;5;245m'
    else
        COLOR_RESET=''
        COLOR_TEXT=''
        COLOR_HEADER=''
        COLOR_LINE=''
        COLOR_INFO=''
        COLOR_WARN=''
        COLOR_ERROR=''
        COLOR_DEBUG=''
        COLOR_TRACE=''
        COLOR_SUBTITLE=''
        COLOR_MUTED=''
    fi
}

log::info() {
    printf '%s[INFO]%s %s%s%s\n' \
        "$COLOR_INFO" \
        "$COLOR_RESET" \
        "$COLOR_TEXT" \
        "$*" \
        "$COLOR_RESET"
}

log::warn() {
    printf '%s[WARN]%s %s%s%s\n' \
        "$COLOR_WARN" \
        "$COLOR_RESET" \
        "$COLOR_TEXT" \
        "$*" \
        "$COLOR_RESET"
}

log::error() {
    printf '%s[ERROR]%s %s%s%s\n' \
        "$COLOR_ERROR" \
        "$COLOR_RESET" \
        "$COLOR_TEXT" \
        "$*" \
        "$COLOR_RESET" >&2
}

cleanup_transient() {
    if [[ -n "${vault_password_file:-}" ]]; then
        rm -f "${vault_password_file}" 2>/dev/null || true
        vault_password_file=""
    fi
    if ((${#quiet_run_files[@]} > 0)); then
        rm -f "${quiet_run_files[@]}" 2>/dev/null || true
        quiet_run_files=()
    fi
    find "${state_root}" -maxdepth 1 -type f -name 'quiet-run.*' -delete 2>/dev/null || true
    rm -f "${state_root}"/secrets.env.plain.* 2>/dev/null || true
    rm -f "${vault_file}".tmp.* 2>/dev/null || true
    prune_vault_backups
    vault_set_readonly
}

vault_set_writable() {
    mkdir -p "${vault_dir}" 2>/dev/null || true
    chmod 0700 "${vault_dir}" 2>/dev/null || true
    if command -v chattr >/dev/null 2>&1; then
        chattr -i "${vault_file}" 2>/dev/null || true
    elif command -v chflags >/dev/null 2>&1; then
        chflags nouchg "${vault_file}" 2>/dev/null || true
    fi
    [[ -f "${vault_file}" ]] && chmod 0600 "${vault_file}" 2>/dev/null || true
}

vault_set_readonly() {
    if command -v chattr >/dev/null 2>&1 && [[ -f "${vault_file}" ]]; then
        chattr +i "${vault_file}" 2>/dev/null || true
    elif command -v chflags >/dev/null 2>&1 && [[ -f "${vault_file}" ]]; then
        chflags uchg "${vault_file}" 2>/dev/null || true
    fi
    [[ -f "${vault_file}" ]] && chmod 0400 "${vault_file}" 2>/dev/null || true
}

prune_vault_backups() {
    local keep_count=20 backup_dir pattern backup_file
    local backups=()
    local sorted_backups=()

    backup_dir="$(dirname -- "${vault_file}")"
    pattern="${backup_dir}/$(basename -- "${vault_file}").bak.*"

    shopt -s nullglob
    backups=( ${pattern} )
    shopt -u nullglob

    (( ${#backups[@]} > 0 )) || return 0

    while IFS= read -r backup_file; do
        sorted_backups+=("$backup_file")
    done < <(printf '%s\n' "${backups[@]}" | LC_ALL=C sort -r)

    if (( ${#sorted_backups[@]} > keep_count )); then
        for backup_file in "${sorted_backups[@]:keep_count}"; do
            rm -f -- "$backup_file"
        done
    fi
}

seed_policy_files_from_examples() {
    local src dst
    for src in \
        "${tcp_dir}/routing/rules.example.yml" \
        "${tcp_dir}/shadowrocket.example.conf"; do
        case "$src" in
            */routing/rules.example.yml) dst="${tcp_dir}/routing/rules.yml" ;;
            */shadowrocket.example.conf) dst="${tcp_dir}/shadowrocket.conf" ;;
            *) continue ;;
        esac
        if [[ ! -f "${dst}" && -f "${src}" ]]; then
            mkdir -p "$(dirname -- "${dst}")"
            cp "${src}" "${dst}"
            chmod 0644 "${dst}"
        fi
    done
}

state_backup_default_path() {
    local backup_root="${state_root}/backups"
    mkdir -p "$backup_root"
    printf '%s\n' "${backup_root}/vault-$(date -u '+%Y%m%dT%H%M%SZ').tar.gz"
}

backup_state() {
    local output="${1:-}"
    require_file "${vault_file}"
    prepare_state_dirs

    if [[ -z "$output" ]]; then
        output="$(state_backup_default_path)"
    else
        mkdir -p "$(dirname -- "$output")"
    fi

    tar -C "${vault_dir}" -czf "$output" secrets.env.vault
    printf '%s\n' "$output"
}

restore_state() {
    local archive="$1" tmpdir extracted_vault backup_path archive_entry
    [[ -n "$archive" ]] || {
        log::error "Missing archive path."
        return 1
    }
    require_file "$archive"
    prepare_state_dirs
    vault_set_writable
    tmpdir="$(mktemp -d "${state_root}/restore.XXXXXX")"

    archive_entry="$(
        tar -tzf "$archive" 2>/dev/null | awk '
            $0 == "secrets.env.vault" { print $0; exit }
            $0 == "vault/secrets.env.vault" { print $0; exit }
        '
    )"

    if [[ -z "$archive_entry" ]]; then
        rm -rf "$tmpdir"
        log::error "Backup archive does not contain secrets.env.vault."
        return 1
    fi

    if ! tar -C "$tmpdir" -xzf "$archive" "$archive_entry"; then
        rm -rf "$tmpdir"
        log::error "Could not extract backup archive."
        return 1
    fi

    case "$archive_entry" in
        vault/secrets.env.vault) extracted_vault="${tmpdir}/vault/secrets.env.vault" ;;
        *) extracted_vault="${tmpdir}/secrets.env.vault" ;;
    esac

    if [[ -d "${vault_dir}" ]]; then
        backup_path="${vault_dir}.restore.$(date -u '+%Y%m%dT%H%M%SZ')"
        mv "${vault_dir}" "${backup_path}"
    fi

    mkdir -p "${vault_dir}"
    chmod 0700 "${vault_dir}"
    mv "${extracted_vault}" "${vault_file}"
    chmod 0600 "${vault_file}"
    rm -rf "$tmpdir"
    prune_vault_backups
    vault_set_readonly
    materialize_from_vault
    printf '%s\n' "${vault_file}"
}

forget_quiet_run_file() {
    local target="$1"
    local item
    local original=("${quiet_run_files[@]}")
    quiet_run_files=()
    for item in "${original[@]}"; do
        [[ "$item" == "$target" ]] && continue
        quiet_run_files+=("$item")
    done
}

on_exit() {
    cleanup_transient
}

on_signal() {
    lock_state
    exit 130
}

trap on_exit EXIT
trap on_signal INT TERM HUP

usage() {
    usage_item() {
        local command="$1"
        local description="$2"
        printf '    %s%-18s%s %s%s%s\n' \
            "$COLOR_LINE" \
            "$command" \
            "$COLOR_RESET" \
            "$COLOR_TEXT" \
            "$description" \
            "$COLOR_RESET"
    }

    printf '%sUsage%s\n' "$COLOR_HEADER" "$COLOR_RESET"
    printf '    %s./run.sh --setup%s\n' "$COLOR_LINE" "$COLOR_RESET"
    printf '    %s./run.sh --deploy%s\n' "$COLOR_LINE" "$COLOR_RESET"
    printf '    %s./run.sh --show-links%s\n' "$COLOR_LINE" "$COLOR_RESET"
    printf '    %s./run.sh --rotate xray%s\n' "$COLOR_LINE" "$COLOR_RESET"
    printf '    %s./run.sh --rotate ssh-tun%s\n' "$COLOR_LINE" "$COLOR_RESET"
    printf '    %s./run.sh --help%s\n' "$COLOR_LINE" "$COLOR_RESET"
    printf '\n'

    printf '%sRecommended%s\n' "$COLOR_HEADER" "$COLOR_RESET"
    usage_item "--setup" "First run wizard: initialize state, bootstrap nodes, harden SSH, deploy, and print VLESS links."
    usage_item "--deploy" "Redeploy egress first, then ingress."
    usage_item "--show-links" "Print generated Shadowrocket-compatible VLESS links."
    usage_item "--debug" "Enable verbose Ansible tracing and debug diagnostics for any command."
    printf '\n'

    printf '%sSetup Flow%s\n' "$COLOR_HEADER" "$COLOR_RESET"
    usage_item "--init" "Create encrypted local runtime config."
    usage_item "--build" "Build the portable Ansible runner image."
    usage_item "--bootstrap" "Create ingress/egress management users and keys."
    usage_item "--harden-ssh" "Harden OpenSSH after bootstrap and verify rollback-safe access."
    printf '\n'

    printf '%sDeploy Targets%s\n' "$COLOR_HEADER" "$COLOR_RESET"
    usage_item "--egress" "Deploy only the egress node."
    usage_item "--ingress" "Deploy only the ingress node."
    usage_item "--syntax" "Run Ansible syntax checks."
    printf '\n'

    printf '%sOperations%s\n' "$COLOR_HEADER" "$COLOR_RESET"
    usage_item "--rotate xray" "Regenerate Xray credentials, deploy ingress, and print new links."
    usage_item "--rotate ssh-tun" "Regenerate internal SSH TUN keypair and deploy both nodes."
    usage_item "--rekey" "Change the password for the encrypted Vault state."
    usage_item "--uninstall" "Remove only this project from both nodes."
    usage_item "--lock" "Remove local decrypted/materialized cache."
    usage_item "--save-state" "Encrypt current local materialized config into the Vault."
    usage_item "--backup-state [path]" "Archive the encrypted Vault file for transfer and restore."
    usage_item "--restore-state <archive>" "Restore the encrypted Vault file from a backup archive."
    usage_item "--state-path" "Show encrypted state path."
    usage_item "--check-updates" "Read-only check for newer Xray and clash-rs releases."
    usage_item "--audit-images egress|ingress|all" "Run remote Trivy audit for current Docker images, then clean Trivy artifacts."
}

compose() {
    docker compose -f "${compose_file}" "$@"
}

ansible_runner_image_name() {
    awk '
        /^[[:space:]]*image:[[:space:]]*/ {
            sub(/^[[:space:]]*image:[[:space:]]*/, "")
            print
            exit
        }
    ' "${compose_file}"
}

ansible_runner_base_image_ref() {
    awk '/^FROM[[:space:]]+/ { print $2; exit }' "${script_dir}/Dockerfile" 2>/dev/null || true
}

docker_image_exists() {
    local image="$1"
    docker image inspect "$image" >/dev/null 2>&1
}

file_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{ print $1 }'
    else
        shasum -a 256 "$1" | awk '{ print $1 }'
    fi
}

docker_hub_manifest_digest() {
    local image_ref="$1" repo tag token digest
    [[ "$image_ref" == *:* ]] || return 0
    repo="${image_ref%:*}"
    tag="${image_ref##*:}"
    if [[ "$repo" != */* ]]; then
        repo="library/${repo}"
    fi

    token="$(curl -fsSL     --connect-timeout 10     --max-time 30     "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull"     | sed -E 's/.*"token"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/' 2>/dev/null || true)"
    [[ -n "$token" ]] || return 0

    digest="$(curl -fsSI     --connect-timeout 10     --max-time 30     -H "Authorization: Bearer ${token}"     -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'     "https://registry-1.docker.io/v2/${repo}/manifests/${tag}"     | tr -d '\r'     | awk 'tolower($1)=="docker-content-digest:" { print $2; exit }' 2>/dev/null || true)"
    printf '%s' "$digest"
}

resolve_image_digest() {
    local image_ref="$1" digest="" inspect_line=""
    [[ -n "$image_ref" ]] || return 0

    if command -v docker >/dev/null 2>&1; then
        inspect_line="$(docker buildx imagetools inspect "$image_ref" 2>/dev/null | awk '/^Digest:/ { print $2; exit }' || true)"
        if [[ -n "$inspect_line" ]]; then
            digest="$inspect_line"
        else
            digest="$(docker manifest inspect "$image_ref" 2>/dev/null | grep -m1 '"digest"' | sed -E 's/.*"digest"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/' || true)"
        fi
    fi

    if [[ -z "$digest" && "$image_ref" != *.*/* ]]; then
        digest="$(docker_hub_manifest_digest "$image_ref")"
    fi

    printf '%s' "$digest"
}

image_label() {
    local image="$1" label="$2"
    docker image inspect "$image" --format "{{ index .Config.Labels \"$label\" }}" 2>/dev/null || true
}

github_latest_release_tag() {
    local repo="$1"
    curl -fsSL \
        --connect-timeout 10 \
        --max-time 30 \
        "https://api.github.com/repos/${repo}/releases?per_page=20" \
    | sed -nE 's/.*"tag_name"[[:space:]]*:[[:space:]]*"(v[0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' \
    | head -n1
}

configured_updater_image() {
    local service="$1" file="${tcp_dir}/group_vars/ingress.yml"
    awk -v service="$service" '
        $1 == "-" && $2 == "service:" {
            in_service = ($3 == service)
            next
        }
        in_service && $1 == "image:" {
            print $2
            exit
        }
    ' "$file" 2>/dev/null || true
}

image_tag() {
    local image="$1" tag
    tag="${image##*:}"
    [[ "$tag" != "$image" ]] || tag=""
    printf '%s' "$tag"
}

without_v_prefix() {
    local value="$1"
    printf '%s' "${value#v}"
}

print_update_check_line() {
    local name="$1" image="$2" latest_tag="$3" current_tag latest_no_v current_no_v status
    current_tag="$(image_tag "$image")"
    latest_no_v="$(without_v_prefix "$latest_tag")"
    current_no_v="$(without_v_prefix "$current_tag")"

    if [[ -z "$image" ]]; then
        printf '%-8s configured image: not found\n' "$name"
        return 1
    fi

    if [[ -z "$latest_tag" ]]; then
        printf '%-8s current=%s latest=unknown status=check-failed\n' "$name" "$image"
        return 1
    fi

    if [[ "$current_no_v" == "$latest_no_v" ]]; then
        status="current"
    elif [[ "$current_tag" == "latest" || "$current_tag" == "Alpha" ]]; then
        status="floating-tag"
    else
        status="update-available"
    fi

    printf '%-8s current=%s latest=%s status=%s\n' "$name" "$image" "$latest_tag" "$status"
}

check_updates() {
    local xray_image clashrs_image xray_latest clashrs_latest xray_latest_image clashrs_latest_image

    xray_image="$(configured_updater_image xray)"
    clashrs_image="$(configured_updater_image clashrs)"
    xray_latest="$(github_latest_release_tag XTLS/Xray-core || true)"
    clashrs_latest="latest"

    ui_title "Update Check"
    printf 'This is read-only. It does not pull images, edit files, or deploy.\n\n'

    print_update_check_line "xray" "$xray_image" "$xray_latest"
    print_update_check_line "clash-rs" "$clashrs_image" "$clashrs_latest"

    xray_latest_image="ghcr.io/xtls/xray-core:$(without_v_prefix "$xray_latest")"
    clashrs_latest_image="ghcr.io/watfaq/clash-rs:${clashrs_latest}"

    printf '\nManual verification commands\n'
    printf '%s\n\n' '----------------------------'
    if [[ -n "$xray_latest" ]]; then
        printf 'docker pull %s\n' "$xray_latest_image"
        printf 'docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache/ aquasec/trivy:latest image --severity HIGH,CRITICAL --ignore-unfixed %s\n\n' "$xray_latest_image"
    fi
    if [[ -n "$clashrs_latest" ]]; then
        printf 'docker pull %s\n' "$clashrs_latest_image"
        printf 'docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache/ aquasec/trivy:latest image --severity HIGH,CRITICAL --ignore-unfixed %s\n\n' "$clashrs_latest_image"
    fi

    printf 'After testing, update the pinned image tag in Ansible and deploy ingress deliberately.\n'
}

ansible_runner_image_current() {
    local image="$1" base_digest="$2" dockerfile_hash="$3"
    local local_base_digest local_dockerfile_hash
    local_base_digest="$(image_label "$image" 'nitka.ansible.base-digest')"
    local_dockerfile_hash="$(image_label "$image" 'nitka.ansible.dockerfile-sha256')"

    [[ -n "$base_digest" && "$local_base_digest" == "$base_digest" ]] || return 1
    [[ -n "$dockerfile_hash" && "$local_dockerfile_hash" == "$dockerfile_hash" ]] || return 1
    return 0
}

quiet_run() {
    local tmp status
    prepare_state_dirs
    tmp="$(mktemp "${state_root}/quiet-run.XXXXXX")"
    quiet_run_files+=("$tmp")
    set +e
    "$@" >"$tmp" 2>&1
    status=$?
    set -e
    if [[ "$status" -ne 0 ]]; then
        cat "$tmp" >&2
    fi
    rm -f "$tmp"
    forget_quiet_run_file "$tmp"
    return "$status"
}

build_ansible_runner() {
    local mode="${1:-}" image base_ref base_digest dockerfile_hash
    prepare_state_dirs

    image="$(ansible_runner_image_name)"
    base_ref="$(ansible_runner_base_image_ref)"
    base_digest="$(resolve_image_digest "$base_ref")"
    dockerfile_hash="$(file_sha256 "${script_dir}/Dockerfile")"

    if [[ -n "$base_digest" ]]; then
        [[ "${nitka_debug}" == "1" ]] && log::info "Ansible base image ${base_ref}: ${base_digest}"
    else
        if [[ "${NITKA_FORCE_BUILD:-0}" != "1" ]] && docker_image_exists "$image"; then
            log::warn "Could not resolve Ansible base image digest for ${base_ref}; keeping existing runner image ${image}"
            runner_image_ready="yes"
            return 0
        fi
        log::warn "Could not resolve Ansible base image digest for ${base_ref}; rebuilding runner image without digest guard"
    fi

    if [[ "${NITKA_FORCE_BUILD:-0}" != "1" && -n "$base_digest" ]] && ansible_runner_image_current "$image" "$base_digest" "$dockerfile_hash"; then
        [[ "${nitka_debug}" == "1" ]] && log::info "Ansible runner image is current, skipping build: ${image}"
        runner_image_ready="yes"
        return 0
    fi

    if [[ "$mode" == "quiet" && "${nitka_debug}" != "1" ]]; then
        quiet_run compose build \
            --build-arg "NITKA_ANSIBLE_BASE_IMAGE_DIGEST=${base_digest}" \
            --build-arg "NITKA_ANSIBLE_DOCKERFILE_SHA256=${dockerfile_hash}" \
            ansible
    else
        compose build \
            --build-arg "NITKA_ANSIBLE_BASE_IMAGE_DIGEST=${base_digest}" \
            --build-arg "NITKA_ANSIBLE_DOCKERFILE_SHA256=${dockerfile_hash}" \
            ansible
    fi
    runner_image_ready="yes"
}

build_ansible_runner_no_cache() {
    local base_ref base_digest dockerfile_hash
    prepare_state_dirs
    base_ref="$(ansible_runner_base_image_ref)"
    base_digest="$(resolve_image_digest "$base_ref")"
    dockerfile_hash="$(file_sha256 "${script_dir}/Dockerfile")"

    if [[ -z "$base_digest" ]] && docker_image_exists "$(ansible_runner_image_name)"; then
        log::warn "Could not resolve Ansible base image digest for ${base_ref}; keeping existing runner image $(ansible_runner_image_name)"
        runner_image_ready="yes"
        return 0
    fi

    compose build --no-cache \
        --build-arg "NITKA_ANSIBLE_BASE_IMAGE_DIGEST=${base_digest}" \
        --build-arg "NITKA_ANSIBLE_DOCKERFILE_SHA256=${dockerfile_hash}" \
        ansible
    runner_image_ready="yes"
}

ansible_run() {
    local args=("$@")
    local run_args=(run --rm)

    if [[ "${nitka_debug}" == "1" ]]; then
        run_args+=(
            -e ANSIBLE_CALLBACKS_ENABLED=profile_tasks
            -e ANSIBLE_VERBOSITY=3
        )
        case "${args[0]:-}" in
            ansible|ansible-playbook)
                args+=(-e nitka_debug=true -vvv)
                ;;
        esac
    fi

    compose "${run_args[@]}" ansible "${args[@]}"
}

vault_command() {
    if [[ "${runner_image_ready}" != "yes" ]]; then
        build_ansible_runner quiet
    fi

    if [[ "${nitka_debug}" == "1" ]]; then
        compose run --rm -T \
            -v "${state_root}:${state_root}" \
            ansible \
            ansible-vault "$@"
    else
        quiet_run compose run --rm -T \
            -v "${state_root}:${state_root}" \
            ansible \
            ansible-vault "$@"
    fi
}

rekey_vault() {
    prepare_state_dirs
    require_file "${vault_file}"
    if [[ "${runner_image_ready}" != "yes" ]]; then
        build_ansible_runner quiet
    fi

    printf '%sVault Rekey%s\n' "$COLOR_HEADER" "$COLOR_RESET"
    printf 'Vault file:\n  %s\n\n' "${vault_file}"
    compose run --rm -it \
        -v "${state_root}:${state_root}" \
        ansible \
        ansible-vault rekey "${vault_file}"
}

shell_quote() {
    local value="${1-}"
    printf "'%s'" "${value//\'/\'\\\'\'}"
}

b64_file() {
    base64 < "$1" | tr -d '\n'
}

write_b64_file() {
    local value="$1"
    local path="$2"
    local mode="$3"
    mkdir -p "$(dirname -- "$path")"
    printf '%s' "$value" | base64 -d > "$path"
    chmod "$mode" "$path"
}

prepare_state_dirs() {
    mkdir -p "${vault_dir}" "${generated_dir}"
    chmod 0700 "${state_root}" "${vault_dir}" "${generated_dir}"
    seed_policy_files_from_examples
}

lock_state() {
    rm -rf "${generated_dir}"
    if [[ -n "${vault_password_file:-}" ]]; then
        rm -f "${vault_password_file}"
        vault_password_file=""
    fi
    vault_set_readonly
}

ensure_vault_password_file() {
    local vault_password vault_password_confirm status
    local vault_body
    if [[ -n "${vault_password_file:-}" && -f "${vault_password_file}" ]]; then
        return 0
    fi

    vault_body="Create a password to encrypt local generated state.\nVault file:\n  ${vault_file}"

    if [[ -f "${vault_file}" ]]; then
        screen_prompt vault_password "Encrypted State" "$vault_body" "Enter Vault password" "" "no" "yes" "no"
    else
        while true; do
            screen_prompt vault_password "Encrypted State" "$vault_body" "Create Vault password" "" "no" "yes" "no"
            status=0
            screen_prompt vault_password_confirm "Encrypted State" "$vault_body" "Confirm Vault password" "" "yes" "yes" "no" || status=$?
            if [[ "$status" == "10" ]]; then
                continue
            fi
            if [[ "${vault_password}" == "${vault_password_confirm}" ]]; then
                break
            fi
            log::warn "Vault passwords do not match."
            sleep 1
        done
    fi

    mkdir -p "${state_root}"
    vault_password_file="${state_root}/.vault-password.$$"
    printf '%s\n' "${vault_password}" > "${vault_password_file}"
    chmod 0600 "${vault_password_file}"
}

vault_encrypt_file() {
    local input="$1"
    local output="$2"
    ensure_vault_password_file
    vault_command encrypt --vault-password-file "${vault_password_file}" --output "${output}" "${input}"
}

atomic_install_vault() {
    local encrypted_tmp="$1"
    local backup_path

    [[ -s "$encrypted_tmp" ]] || {
        log::error "Encrypted Vault output is empty; keeping existing Vault untouched."
        return 1
    }

    if [[ -f "${vault_file}" ]]; then
        backup_path="${vault_file}.bak.$(date -u '+%Y%m%dT%H%M%SZ')"
        cp -p "${vault_file}" "${backup_path}"
        chmod 0600 "${backup_path}"
    fi

    mv -f "${encrypted_tmp}" "${vault_file}"
    chmod 0600 "${vault_file}"
    prune_vault_backups
}

vault_decrypt_file() {
    local input="$1"
    local output="$2"
    ensure_vault_password_file
    vault_command decrypt --vault-password-file "${vault_password_file}" --output "${output}" "${input}"
}

vault_plain_path() {
    printf '%s\n' "${state_root}/secrets.env.plain.$$"
}

write_secret_var() {
    local file="$1"
    local name="$2"
    local value="$3"
    printf '%s=%s\n' "$name" "$(shell_quote "$value")" >> "$file"
}

remove_project_state_keys() {
    local tmp key
    [[ -f "${project_state}" ]] || return 0
    tmp="${project_state}.tmp.$$"
    cp "${project_state}" "${tmp}"
    for key in "$@"; do
        grep -v "^${key}=" "${tmp}" > "${tmp}.next" || true
        mv -f "${tmp}.next" "${tmp}"
    done
    mv -f "${tmp}" "${project_state}"
    chmod 0600 "${project_state}"
}

write_project_state() {
    local tmp current_obfs_host current_obfs_path
    tmp="${project_state}.tmp.$$"
    mkdir -p "$(dirname -- "${project_state}")"
    : > "$tmp"
    chmod 0600 "$tmp"

    current_obfs_host="${ingress_xray_obfs_host:-}"
    current_obfs_path="${ingress_xray_obfs_path:-}"
    if [[ -z "$current_obfs_host" && -f "${tcp_dir}/group_vars/ingress.yml" ]]; then
        current_obfs_host="$(last_group_var_value "${tcp_dir}/group_vars/ingress.yml" ingress_xray_obfs_host)"
    fi
    if [[ -z "$current_obfs_path" && -f "${tcp_dir}/group_vars/ingress.yml" ]]; then
        current_obfs_path="$(last_group_var_value "${tcp_dir}/group_vars/ingress.yml" ingress_xray_obfs_path)"
    fi
    current_obfs_host="${current_obfs_host:-example.com}"
    current_obfs_path="${current_obfs_path:-/}"

    {
        printf 'initialized_at=%s\n' "${initialized_at:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}"
        printf 'ingress_ip=%s\n' "${ingress_ip}"
        printf 'ingress_initial_user=%s\n' "${ingress_initial_user}"
        printf 'ingress_initial_port=%s\n' "${ingress_initial_port}"
        printf 'ingress_auth_method=%s\n' "${ingress_auth_method}"
        if [[ "${ingress_auth_method}" == "password" && -n "${ingress_initial_password:-}" ]]; then
            printf 'ingress_initial_password=%s\n' "$(shell_quote "${ingress_initial_password}")"
        fi
        printf 'egress_ip=%s\n' "${egress_ip}"
        printf 'egress_initial_user=%s\n' "${egress_initial_user}"
        printf 'egress_initial_port=%s\n' "${egress_initial_port}"
        printf 'egress_auth_method=%s\n' "${egress_auth_method}"
        if [[ "${egress_auth_method}" == "password" && -n "${egress_initial_password:-}" ]]; then
            printf 'egress_initial_password=%s\n' "$(shell_quote "${egress_initial_password}")"
        fi
        printf 'ssh_tun_port=%s\n' "${ssh_tun_port}"
        printf 'xhttp_port=%s\n' "${xhttp_port}"
        printf 'reality_port=%s\n' "${reality_port}"
        printf 'ingress_xray_obfs_host=%s\n' "$(shell_quote "${current_obfs_host}")"
        printf 'ingress_xray_obfs_path=%s\n' "$(shell_quote "${current_obfs_path}")"
        printf 'ingress_management_user=%s\n' "${ingress_management_user:-ingress}"
        printf 'ingress_management_port=%s\n' "${ingress_management_port:-$ingress_initial_port}"
        printf 'ingress_sshd_port=%s\n' "${ingress_sshd_port:-$ingress_initial_port}"
        printf 'egress_management_user=%s\n' "${egress_management_user:-egress}"
        printf 'egress_management_port=%s\n' "${egress_management_port:-$egress_initial_port}"
        printf 'egress_sshd_port=%s\n' "${egress_sshd_port:-$egress_initial_port}"
        [[ -n "${bootstrap_completed_at:-}" ]] && printf 'bootstrap_completed_at=%s\n' "${bootstrap_completed_at}"
        [[ -n "${harden_ssh_completed_at:-}" ]] && printf 'harden_ssh_completed_at=%s\n' "${harden_ssh_completed_at}"
    } > "$tmp"

    mv -f "$tmp" "${project_state}"
    chmod 0600 "${project_state}"
}

sync_project_state_from_group_vars() {
    local group_vars_file="${tcp_dir}/group_vars/ingress.yml"
    local group_obfs_host group_obfs_path

    [[ -f "${project_state}" && -f "$group_vars_file" ]] || return 0

    group_obfs_host="$(last_group_var_value "$group_vars_file" ingress_xray_obfs_host)"
    group_obfs_path="$(last_group_var_value "$group_vars_file" ingress_xray_obfs_path)"

    [[ -n "$group_obfs_host" || -n "$group_obfs_path" ]] || return 0

    # shellcheck disable=SC1090
    source "${project_state}"

    if [[ -n "$group_obfs_host" ]]; then
        ingress_xray_obfs_host="$group_obfs_host"
    fi
    if [[ -n "$group_obfs_path" ]]; then
        ingress_xray_obfs_path="$group_obfs_path"
    fi

    write_project_state
}

persist_vault() {
    local plain encrypted_tmp
    plain="$(vault_plain_path)"
    encrypted_tmp="${vault_file}.tmp.$$"
    prepare_state_dirs
    vault_set_writable
    : > "$plain"
    chmod 0600 "$plain"

    require_file "${project_state}"
    sync_project_state_from_group_vars
    write_secret_var "$plain" "project_env_b64" "$(b64_file "${project_state}")"

    for item in \
        "bootstrap_ingress_private_key_b64:${generated_dir}/bootstrap/ingress/id_ed25519" \
        "bootstrap_egress_private_key_b64:${generated_dir}/bootstrap/egress/id_ed25519" \
        "management_ingress_private_key_b64:${generated_dir}/management/ingress/id_ed25519" \
        "management_ingress_public_key:${generated_dir}/management/ingress/id_ed25519.pub" \
        "management_egress_private_key_b64:${generated_dir}/management/egress/id_ed25519" \
        "management_egress_public_key:${generated_dir}/management/egress/id_ed25519.pub" \
        "ssh_tun_private_key_b64:${generated_dir}/egress/ssh/id_ed25519" \
        "ssh_tun_public_key:${generated_dir}/egress/ssh/id_ed25519.pub" \
        "xray_xhttp_uuid:${generated_dir}/ingress/state/xhttp_uuid" \
        "xray_reality_uuid:${generated_dir}/ingress/state/reality_uuid" \
        "xray_xhttp_port:${generated_dir}/ingress/state/xhttp_port" \
        "xray_reality_port:${generated_dir}/ingress/state/reality_port" \
        "xray_reality_short_id:${generated_dir}/ingress/state/reality_short_id" \
        "xray_reality_private_key:${generated_dir}/ingress/state/reality_private_key" \
        "xray_reality_public_key:${generated_dir}/ingress/state/reality_public_key" \
        "share_links_b64:${generated_dir}/ingress/share-links.txt" \
        "routing_rules_b64:${tcp_dir}/routing/rules.yml" \
        "shadowrocket_conf_b64:${tcp_dir}/shadowrocket.conf"; do
        name="${item%%:*}"
        path="${item#*:}"
        if [[ -f "$path" ]]; then
            case "$name" in
                *_b64) write_secret_var "$plain" "$name" "$(b64_file "$path")" ;;
                *) write_secret_var "$plain" "$name" "$(tr -d '\n' < "$path")" ;;
            esac
        fi
    done

    rm -f "${encrypted_tmp}"
    if ! vault_encrypt_file "$plain" "${encrypted_tmp}"; then
        rm -f "${encrypted_tmp}"
        rm -f "$plain"
        log::error "Vault encryption failed; keeping existing Vault untouched."
        vault_set_readonly
        return 1
    fi

    if ! atomic_install_vault "${encrypted_tmp}"; then
        rm -f "${encrypted_tmp}"
        rm -f "$plain"
        vault_set_readonly
        return 1
    fi

    rm -f "$plain"
    vault_set_readonly
}

materialize_from_vault() {
    local plain
    [[ -f "${vault_file}" ]] || return 0
    plain="$(vault_plain_path)"
    prepare_state_dirs
    vault_decrypt_file "${vault_file}" "$plain"
    chmod 0600 "$plain"
    # shellcheck disable=SC1090
    source "$plain"
    rm -f "$plain"

    [[ -n "${project_env_b64:-}" ]] && write_b64_file "$project_env_b64" "${project_state}" 0600
    [[ -n "${bootstrap_ingress_private_key_b64:-}" ]] && write_b64_file "$bootstrap_ingress_private_key_b64" "${generated_dir}/bootstrap/ingress/id_ed25519" 0600
    [[ -n "${bootstrap_egress_private_key_b64:-}" ]] && write_b64_file "$bootstrap_egress_private_key_b64" "${generated_dir}/bootstrap/egress/id_ed25519" 0600
    [[ -n "${management_ingress_private_key_b64:-}" ]] && write_b64_file "$management_ingress_private_key_b64" "${generated_dir}/management/ingress/id_ed25519" 0600
    if [[ -n "${management_ingress_public_key:-}" ]]; then
        mkdir -p "${generated_dir}/management/ingress"
        printf '%s\n' "$management_ingress_public_key" > "${generated_dir}/management/ingress/id_ed25519.pub"
        chmod 0644 "${generated_dir}/management/ingress/id_ed25519.pub"
    fi
    [[ -n "${management_egress_private_key_b64:-}" ]] && write_b64_file "$management_egress_private_key_b64" "${generated_dir}/management/egress/id_ed25519" 0600
    if [[ -n "${management_egress_public_key:-}" ]]; then
        mkdir -p "${generated_dir}/management/egress"
        printf '%s\n' "$management_egress_public_key" > "${generated_dir}/management/egress/id_ed25519.pub"
        chmod 0644 "${generated_dir}/management/egress/id_ed25519.pub"
    fi
    [[ -n "${ssh_tun_private_key_b64:-}" ]] && write_b64_file "$ssh_tun_private_key_b64" "${generated_dir}/egress/ssh/id_ed25519" 0600
    if [[ -n "${ssh_tun_public_key:-}" ]]; then
        mkdir -p "${generated_dir}/egress/ssh"
        printf '%s\n' "$ssh_tun_public_key" > "${generated_dir}/egress/ssh/id_ed25519.pub"
        chmod 0644 "${generated_dir}/egress/ssh/id_ed25519.pub"
    fi
    mkdir -p "${generated_dir}/ingress/state"
    for pair in \
        "xray_xhttp_uuid:xhttp_uuid" \
        "xray_reality_uuid:reality_uuid" \
        "xray_xhttp_port:xhttp_port" \
        "xray_reality_port:reality_port" \
        "xray_reality_short_id:reality_short_id" \
        "xray_reality_private_key:reality_private_key" \
        "xray_reality_public_key:reality_public_key"; do
        var="${pair%%:*}"
        file="${pair#*:}"
        value="${!var:-}"
        if [[ -n "$value" ]]; then
            printf '%s\n' "$value" > "${generated_dir}/ingress/state/${file}"
            chmod 0600 "${generated_dir}/ingress/state/${file}"
        fi
    done
    [[ -f "${generated_dir}/ingress/state/reality_public_key" ]] && chmod 0644 "${generated_dir}/ingress/state/reality_public_key"
    [[ -n "${share_links_b64:-}" ]] && write_b64_file "$share_links_b64" "${generated_dir}/ingress/share-links.txt" 0600
    [[ -n "${routing_rules_b64:-}" ]] && write_b64_file "$routing_rules_b64" "${tcp_dir}/routing/rules.yml" 0644
    [[ -n "${shadowrocket_conf_b64:-}" ]] && write_b64_file "$shadowrocket_conf_b64" "${tcp_dir}/shadowrocket.conf" 0644

    if [[ -f "${project_state}" ]]; then
        # shellcheck disable=SC1090
        source "${project_state}"
        generate_runtime_ports
        write_group_vars "${ingress_ip}" "${egress_ip}" "${runtime_ssh_tun_port}" "${runtime_xhttp_port}" "${runtime_reality_port}"
        if [[ -n "${harden_ssh_completed_at:-}" ]]; then
            append_harden_group_vars "${ingress_initial_user}" "${ingress_sshd_port:-${ingress_management_port:-$ingress_initial_port}}" "${egress_initial_user}" "${egress_sshd_port:-${egress_management_port:-$egress_initial_port}}"
        fi
        if [[ -n "${bootstrap_completed_at:-}" ]]; then
            write_management_inventory_with_ports "${ingress_management_port:-$ingress_initial_port}" "${egress_management_port:-$egress_initial_port}"
        else
            write_bootstrap_inventory_from_state
        fi
    fi
}

with_materialized_state() {
    materialize_from_vault
    set +e
    "$@"
    local status=$?
    set -e
    if [[ "$status" -eq 0 && -f "${project_state}" ]]; then
        if ! persist_vault; then
            log::error "Could not save Vault. Keeping materialized state at: ${generated_dir}"
            log::error "Do not run --lock or delete this directory until Vault is saved."
            return 1
        fi
    elif [[ "$status" -ne 0 ]]; then
        log::warn "Command failed; keeping existing Vault state untouched."
    fi
    lock_state
    return "$status"
}

require_file() {
    if [[ ! -f "$1" ]]; then
        printf 'Missing file: %s\n' "$1" >&2
        return 1
    fi
}

ui_title() {
    local title="$1"
    printf '\n%s%s%s\n' "$COLOR_HEADER" "$title" "$COLOR_RESET"
    printf '%s' "$COLOR_LINE"
    printf '%*s' "${#title}" '' | tr ' ' '='
    printf '%s\n\n' "$COLOR_RESET"
}

ui_section() {
    local title="$1"
    printf '\n%s%s%s\n' "$COLOR_SUBTITLE" "$title" "$COLOR_RESET"
    printf '%s' "$COLOR_LINE"
    printf '%*s' "${#title}" '' | tr ' ' '-'
    printf '%s\n\n' "$COLOR_RESET"
}

ui_step() {
    local step="$1"
    local text="$2"
    printf '\n%s[%s]%s %s%s%s\n' "$COLOR_INFO" "$step" "$COLOR_RESET" "$COLOR_TEXT" "$text" "$COLOR_RESET"
}

ui_kv() {
    local key="$1"
    local value="$2"
    printf '  %s%-18s%s %s%s%s\n' "$COLOR_LINE" "${key}:" "$COLOR_RESET" "$COLOR_TEXT" "${value}" "$COLOR_RESET"
}

ui_clear() {
    if [[ -t 1 ]]; then
        clear
        printf '\e[3J'
    fi
}

ui_controls() {
    local allow_back="${1:-no}"
    local allow_main="${2:-yes}"
    printf '\n'
    if [[ "$allow_back" == "yes" ]]; then
        printf '%sb.%s %sBack%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
    fi
    if [[ "$allow_main" == "yes" ]]; then
        printf '%sm.%s %sMain menu%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
    fi
    printf '%sx.%s %sExit%s\n\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
}

exit_program() {
    printf '\n'
    ui_clear
    log::warn "Exiting."
    lock_state
    exit 0
}

yaml_quote() {
    local value="${1-}"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/ }"
    printf '"%s"' "${value}"
}

prompt_yes_no() {
    local var_name="$1"
    local prompt="$2"
    local default_value="${3:-yes}"
    local value
    local suffix

    while true; do
        if [[ "${default_value}" == "yes" ]]; then
            suffix="Y/n"
        else
            suffix="y/N"
        fi
        printf '%s%s%s %s[%s]%s:\n%s> %s' \
            "$COLOR_TEXT" "$prompt" "$COLOR_RESET" \
            "$COLOR_LINE" "$suffix" "$COLOR_RESET" \
            "$COLOR_LINE" "$COLOR_RESET"
        read -r value
        case "$value" in
            x|X|exit|quit) exit_program ;;
        esac
        value="${value:-${default_value}}"
        case "${value}" in
            yes|y|Y)
                printf -v "${var_name}" '%s' "yes"
                return 0
                ;;
            no|n|N)
                printf -v "${var_name}" '%s' "no"
                return 0
                ;;
            *)
                log::warn "Use yes or no."
                ;;
        esac
    done
}

screen_prompt() {
    local var_name="$1"
    local title="$2"
    local body="$3"
    local prompt="$4"
    local default_value="${5-}"
    local allow_back="${6:-no}"
    local secret="${7:-no}"
    local allow_main="${8:-yes}"
    local value
    local label

    while true; do
        ui_clear
        ui_title "$title"
        [[ -n "$body" ]] && printf '%b\n\n' "$body"

        if [[ -n "$default_value" ]]; then
            label="${prompt} [${default_value}]"
        else
            label="${prompt}"
        fi

        printf '%s%s%s: ' "$COLOR_TEXT" "$label" "$COLOR_RESET"

        if [[ "$secret" == "yes" ]]; then
            read -r -s value
            printf '\n'
        else
            read -r value
        fi

        case "$value" in
            x|X|exit|quit)
                exit_program
                ;;
            b|B|back)
                if [[ "$allow_back" == "yes" ]]; then
                    return 10
                fi
                ;;
            m|M|main)
                if [[ "$allow_main" == "yes" ]]; then
                    return 20
                fi
                ;;
        esac

        value="${value:-${default_value}}"
        if [[ -n "$value" ]]; then
            printf -v "$var_name" '%s' "$value"
            return 0
        fi

        log::warn "Value is required."
        sleep 1
    done
}

screen_auth_method() {
    local var_name="$1"
    local title="$2"
    local body="$3"
    local allow_back="${4:-yes}"
    local allow_main="${5:-yes}"
    local value

    while true; do
        ui_clear
        ui_title "$title"
        [[ -n "$body" ]] && printf '%b\n\n' "$body"
        printf '%sAuthentication method%s\n' "$COLOR_SUBTITLE" "$COLOR_RESET"
        printf '  %s1.%s %sPrivate key%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '  %s2.%s %sPassword%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '\n%sChoose [1]%s: ' "$COLOR_TEXT" "$COLOR_RESET"
        read -r value
        value="${value:-1}"

        case "$value" in
            x|X|exit|quit)
                exit_program
                ;;
            b|B|back)
                if [[ "$allow_back" == "yes" ]]; then
                    return 10
                fi
                ;;
            m|M|main)
                if [[ "$allow_main" == "yes" ]]; then
                    return 20
                fi
                ;;
            1|key|private-key|private_key)
                printf -v "$var_name" '%s' "key"
                return 0
                ;;
            2|password)
                printf -v "$var_name" '%s' "password"
                return 0
                ;;
            *)
                log::warn "Use 1 for private key or 2 for password."
                sleep 1
                ;;
        esac
    done
}

resolve_existing_file() {
    local input="$1"
    local expanded candidate
    expanded="${input/#\~/${HOME}}"

    for candidate in \
        "$expanded" \
        "${PWD}/${expanded}" \
        "${script_dir}/${expanded}" \
        "${script_dir}/../${expanded}"; do
        if [[ -f "$candidate" ]]; then
            (
                cd "$(dirname -- "$candidate")" >/dev/null
                printf '%s/%s\n' "$(pwd -P)" "$(basename -- "$candidate")"
            )
            return 0
        fi
    done

    return 1
}

copy_bootstrap_key() {
    local source_path="$1"
    local dest_path="$2"
    local resolved_dir resolved_path

    if ! resolved_path="$(resolve_existing_file "$source_path")"; then
        return 1
    fi

    mkdir -p "$(dirname -- "${dest_path}")"
    cp "${resolved_path}" "${dest_path}"
    chmod 0600 "${dest_path}"
}

generate_management_key() {
    local path="$1"
    local comment="$2"

    mkdir -p "$(dirname -- "${path}")"
    if [[ ! -f "${path}" ]]; then
        ssh-keygen -t ed25519 -N '' -f "${path}" -C "${comment}" >/dev/null
    fi
    chmod 0600 "${path}"
    chmod 0644 "${path}.pub"
}

generate_port() {
    "${port_generator}" "$@"
}

generate_runtime_ports() {
    local state_dir="${generated_dir}/ports"
    local saved_ssh_tun_port="${ssh_tun_port:-}"
    local saved_xhttp_port="${xhttp_port:-}"
    local saved_reality_port="${reality_port:-}"
    local ssh_tun_port xhttp_port reality_port

    mkdir -p "${state_dir}"

    if [[ -f "${state_dir}/ssh_tun_port" ]]; then
        ssh_tun_port="$(<"${state_dir}/ssh_tun_port")"
    elif [[ -n "${saved_ssh_tun_port}" ]]; then
        ssh_tun_port="${saved_ssh_tun_port}"
        printf '%s\n' "${ssh_tun_port}" > "${state_dir}/ssh_tun_port"
    else
        ssh_tun_port="$(generate_port 20000 60000)"
        printf '%s\n' "${ssh_tun_port}" > "${state_dir}/ssh_tun_port"
    fi

    if [[ -f "${generated_dir}/ingress/state/xhttp_port" ]]; then
        xhttp_port="$(<"${generated_dir}/ingress/state/xhttp_port")"
    elif [[ -n "${saved_xhttp_port}" ]]; then
        mkdir -p "${generated_dir}/ingress/state"
        xhttp_port="${saved_xhttp_port}"
        printf '%s\n' "${xhttp_port}" > "${generated_dir}/ingress/state/xhttp_port"
    else
        mkdir -p "${generated_dir}/ingress/state"
        while :; do
            xhttp_port="$(generate_port 20000 60000)"
            [[ "${xhttp_port}" != "${ssh_tun_port}" ]] && break
        done
        printf '%s\n' "${xhttp_port}" > "${generated_dir}/ingress/state/xhttp_port"
    fi

    if [[ -f "${generated_dir}/ingress/state/reality_port" ]]; then
        reality_port="$(<"${generated_dir}/ingress/state/reality_port")"
    elif [[ -n "${saved_reality_port}" ]]; then
        mkdir -p "${generated_dir}/ingress/state"
        reality_port="${saved_reality_port}"
        printf '%s\n' "${reality_port}" > "${generated_dir}/ingress/state/reality_port"
    else
        mkdir -p "${generated_dir}/ingress/state"
        while :; do
            reality_port="$(generate_port 20000 60000)"
            [[ "${reality_port}" != "${ssh_tun_port}" && "${reality_port}" != "${xhttp_port}" ]] && break
        done
        printf '%s\n' "${reality_port}" > "${generated_dir}/ingress/state/reality_port"
    fi

    chmod 0600 \
        "${state_dir}/ssh_tun_port" \
        "${generated_dir}/ingress/state/xhttp_port" \
        "${generated_dir}/ingress/state/reality_port"

    runtime_ssh_tun_port="${ssh_tun_port}"
    runtime_xhttp_port="${xhttp_port}"
    runtime_reality_port="${reality_port}"
}

write_inventory_host() {
    local file="$1"
    local group="$2"
    local host_alias="$3"
    local host_ip="$4"
    local ssh_user="$5"
    local ssh_port="$6"
    local auth_method="$7"
    local key_file="$8"
    local password="$9"

    mkdir -p "$(dirname -- "${file}")"
    : > "${file}"
    chmod 0600 "${file}"
    {
        printf -- '---\n'
        printf 'all:\n'
        printf '  children:\n'
        printf '    %s:\n' "${group}"
        printf '      hosts:\n'
        printf '        %s:\n' "${host_alias}"
        printf '          ansible_host: %s\n' "$(yaml_quote "${host_ip}")"
        printf '          ansible_port: %s\n' "$(yaml_quote "${ssh_port}")"
        printf '          ansible_user: %s\n' "$(yaml_quote "${ssh_user}")"
        if [[ "${auth_method}" == "key" ]]; then
            printf '          ansible_ssh_private_key_file: %s\n' "$(yaml_quote "${key_file}")"
        else
            printf '          ansible_password: %s\n' "$(yaml_quote "${password}")"
            printf '          ansible_become_password: %s\n' "$(yaml_quote "${password}")"
        fi
        printf "          ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o ConnectTimeout=8 -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=2'\n"
    } > "${file}"
}

write_bootstrap_inventory_from_state() {
    require_file "${project_state}"
    # shellcheck disable=SC1090
    source "${project_state}"

    if [[ "${ingress_auth_method}" == "key" ]]; then
        write_inventory_host \
            "${tcp_dir}/inventory/ingress.yml" \
            ingress \
            ingress_node \
            "${ingress_ip}" \
            "${ingress_initial_user}" \
            "${ingress_initial_port}" \
            key \
            "/workspace/tcp/.generated/bootstrap/ingress/id_ed25519" \
            ""
    else
        write_inventory_host \
            "${tcp_dir}/inventory/ingress.yml" \
            ingress \
            ingress_node \
            "${ingress_ip}" \
            "${ingress_initial_user}" \
            "${ingress_initial_port}" \
            password \
            "" \
            "${ingress_initial_password:?missing ingress initial password in encrypted state}"
    fi

    if [[ "${egress_auth_method}" == "key" ]]; then
        write_inventory_host \
            "${tcp_dir}/inventory/egress.yml" \
            egress \
            egress_node \
            "${egress_ip}" \
            "${egress_initial_user}" \
            "${egress_initial_port}" \
            key \
            "/workspace/tcp/.generated/bootstrap/egress/id_ed25519" \
            ""
    else
        write_inventory_host \
            "${tcp_dir}/inventory/egress.yml" \
            egress \
            egress_node \
            "${egress_ip}" \
            "${egress_initial_user}" \
            "${egress_initial_port}" \
            password \
            "" \
            "${egress_initial_password:?missing egress initial password in encrypted state}"
    fi
}

write_group_vars() {
    local ingress_ip="$1"
    local egress_ip="$2"
    local ssh_tun_port="$3"
    local xhttp_port="$4"
    local reality_port="$5"
    local obfs_host="${ingress_xray_obfs_host:-example.com}"
    local obfs_path="${ingress_xray_obfs_path:-/}"
    local obfs_host_yaml obfs_path_yaml

    obfs_host_yaml="$(yaml_quote "$obfs_host")"
    obfs_path_yaml="$(yaml_quote "$obfs_path")"

    mkdir -p "${tcp_dir}/group_vars"
    : > "${tcp_dir}/group_vars/ingress.yml"
    chmod 0644 "${tcp_dir}/group_vars/ingress.yml"
    cat > "${tcp_dir}/group_vars/ingress.yml" <<EOF
---
ingress_remote_dir: /opt/nitka-ingress
ingress_local_dir: "{{ playbook_dir }}/.generated/ingress"
ingress_state_dir: "{{ ingress_local_dir }}/state"
ingress_xray_share_link_path: "{{ ingress_local_dir }}/share-links.txt"
ssh_tun_ssh_key_dir: "{{ playbook_dir }}/.generated/egress/ssh"
ssh_tun_ssh_private_key: "{{ ssh_tun_ssh_key_dir }}/id_ed25519"
ssh_tun_ssh_public_key: "{{ ssh_tun_ssh_key_dir }}/id_ed25519.pub"
ingress_xray_public_host: ${ingress_ip}
ingress_clash_port: 1080
ingress_xray_xhttp_port_min: 20000
ingress_xray_xhttp_port_max: 60000
ingress_xray_xhttp_port: ${xhttp_port}
ingress_xray_reality_port_min: 20000
ingress_xray_reality_port_max: 60000
ingress_xray_reality_port: ${reality_port}
ingress_xray_obfs_host: ${obfs_host_yaml}
ingress_xray_obfs_path: ${obfs_path_yaml}
ingress_xray_xhttp_remarks: vless-xhttp-reality
ingress_xray_reality_remarks: vless-reality-vision

system_base_docker_updater_calendar: "*-*-* 02:20"
system_base_docker_updater_services:
  - service: ssh_tun_client
    container: nitka-ssh-tun-client
    image_type: build
    base_image: alpine:3.23
    cascade_restart:
      - clashrs
      - xray
  - service: clashrs
    container: nitka-clashrs
    image_type: external
    image: local/clash-rs:latest
    release_repo: Watfaq/clash-rs
    release_image_template: ghcr.io/watfaq/clash-rs:latest
    cascade_restart:
      - xray
  - service: xray
    container: nitka-xray
    image_type: external
    image: local/xray-core:auto
    release_repo: XTLS/Xray-core
    release_image_template: ghcr.io/xtls/xray-core:{tag_no_v}
EOF

    : > "${tcp_dir}/group_vars/egress.yml"
    chmod 0644 "${tcp_dir}/group_vars/egress.yml"
    cat > "${tcp_dir}/group_vars/egress.yml" <<EOF
---
egress_remote_dir: /opt/nitka-egress
ssh_tun_ssh_key_dir: "{{ playbook_dir }}/.generated/egress/ssh"
ssh_tun_ssh_username: vpnuser
ssh_tun_ssh_port: ${ssh_tun_port}
ssh_tun_public_host: ${egress_ip}
ssh_tun_network_container_subnet_cidr_ipv4: 172.29.113.0/24
ssh_tun_network_vpn_subnet_cidr_ipv4: 10.11.12.0/30
ssh_tun_network_interface: tun10
ssh_tun_device_number: 10
ssh_tun_network_container_gateway_ipv4: 172.29.113.1
ssh_tun_network_container_vpn_ipv4: 172.29.113.2
ssh_tun_network_vpn_gateway_ipv4: 10.11.12.1
ssh_tun_network_vpn_gateway_cidr_ipv4: 10.11.12.1/30
ssh_tun_network_vpn_client_cidr_ipv4: 10.11.12.2/30

system_base_docker_updater_calendar: "*-*-* 02:00"
system_base_docker_updater_services:
  - service: ssh_tun_server
    container: nitka-ssh-tun-server
    image_type: build
    base_image: alpine:3.23
    cascade_restart:
      - unbound
  - service: unbound
    container: nitka-unbound
    image_type: build
    base_image: alpine:3.23

egress_unbound_forward_servers:
  - address: 1.1.1.1
    tls_name: cloudflare-dns.com
  - address: 1.0.0.1
    tls_name: cloudflare-dns.com
  - address: 94.140.14.140
    tls_name: unfiltered.adguard-dns.com
  - address: 94.140.14.141
    tls_name: unfiltered.adguard-dns.com

egress_unbound_rpz_sources:
  - name: urlhaus
    url: https://urlhaus.abuse.ch/downloads/rpz/
    zonefile: /var/lib/unbound/rpz/urlhaus.rpz
  - name: hagezi-doh
    url: https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/doh.txt
    zonefile: /var/lib/unbound/rpz/hagezi-doh.rpz
  - name: adguard-cname-trackers
    url: https://raw.githubusercontent.com/AdguardTeam/cname-trackers/master/data/combined_disguised_trackers_rpz.txt
    zonefile: /var/lib/unbound/rpz/adguard-cname-trackers.rpz
  - name: adguard-cname-mail
    url: https://raw.githubusercontent.com/AdguardTeam/cname-trackers/master/data/combined_disguised_mail_trackers_rpz.txt
    zonefile: /var/lib/unbound/rpz/adguard-cname-mail.rpz
  - name: threatfox
    url: https://threatfox.abuse.ch/downloads/threatfox.rpz
    zonefile: /var/lib/unbound/rpz/threatfox.rpz
  - name: hagezi-pro-plus
    url: https://raw.githubusercontent.com/hagezi/dns-blocklists/main/rpz/pro.plus.txt
    zonefile: /var/lib/unbound/rpz/hagezi-pro-plus.rpz
EOF
}

append_harden_group_vars() {
    local ingress_initial_user="$1"
    local ingress_ssh_port="$2"
    local egress_initial_user="$3"
    local egress_ssh_port="$4"

    cat >> "${tcp_dir}/group_vars/ingress.yml" <<EOF

harden_ssh_initial_user: ${ingress_initial_user}
harden_ssh_port: ${ingress_ssh_port}
EOF

    cat >> "${tcp_dir}/group_vars/egress.yml" <<EOF

harden_ssh_initial_user: ${egress_initial_user}
harden_ssh_port: ${egress_ssh_port}
EOF
}

print_project_plan() {
    require_file "${project_state}"
    # shellcheck disable=SC1090
    source "${project_state}"

    ui_section "Deployment Plan"
    printf 'Ingress\n'
    ui_kv "IP" "${ingress_ip}"
    ui_kv "initial SSH" "${ingress_initial_user}@${ingress_ip}:${ingress_initial_port}"
    ui_kv "auth" "${ingress_auth_method}"
    ui_kv "management user" "ingress"
    printf '\n'
    printf 'Egress\n'
    ui_kv "IP" "${egress_ip}"
    ui_kv "initial SSH" "${egress_initial_user}@${egress_ip}:${egress_initial_port}"
    ui_kv "auth" "${egress_auth_method}"
    ui_kv "management user" "egress"
    printf '\n'
    printf 'Generated automatically\n'
    ui_kv "SSH TUN port" "${ssh_tun_port}"
    ui_kv "XHTTP port" "${xhttp_port}"
    ui_kv "Reality port" "${reality_port}"
    ui_kv "Xray UUIDs" "yes"
    ui_kv "Reality keys" "yes"
    ui_kv "SSH keypairs" "yes"
}

print_node_status() {
    require_file "${project_state}"
    # shellcheck disable=SC1090
    source "${project_state}"

    ui_section "Nodes"
    printf 'Ingress\n'
    ui_kv "IP" "${ingress_ip}"
    ui_kv "SSH" "${ingress_management_user:-ingress}@${ingress_ip}:${ingress_management_port:-${ingress_initial_port}}"
    ui_kv "sshd listen" "${ingress_sshd_port:-${ingress_management_port:-${ingress_initial_port}}}"
    ui_kv "auth" "key"
    printf '\n'
    printf 'Egress\n'
    ui_kv "IP" "${egress_ip}"
    ui_kv "SSH" "${egress_management_user:-egress}@${egress_ip}:${egress_management_port:-${egress_initial_port}}"
    ui_kv "sshd listen" "${egress_sshd_port:-${egress_management_port:-${egress_initial_port}}}"
    ui_kv "auth" "key"
    printf '\n'
    printf 'Ports\n'
    ui_kv "SSH TUN" "${ssh_tun_port}"
    ui_kv "XHTTP" "${xhttp_port}"
    ui_kv "Reality" "${reality_port}"
    printf '\n'
    printf 'State\n'
    ui_kv "bootstrap" "${bootstrap_completed_at:-pending}"
    ui_kv "SSH hardening" "${harden_ssh_completed_at:-pending}"
}

print_client_links() {
    require_file "${generated_dir}/ingress/share-links.txt"
    ui_clear
    printf '%sClient Links%s\n' "$COLOR_HEADER" "$COLOR_RESET"
    printf '%s============%s\n\n\n' "$COLOR_LINE" "$COLOR_RESET"
    awk 'NF { if (seen++) printf "\n\n"; print }' "${generated_dir}/ingress/share-links.txt"
    printf '\n\n'
}

collect_node_config() {
    local prefix="$1"
    local label="$2"
    local title="$3"
    local description="$4"
    local management_user="$5"
    local key_dest="$6"
    local key_inside_path="$7"
    local allow_first_back="${8:-no}"
    local step=1
    local ip="" user="" port="" auth_method="" key_path="" password="" bootstrap_key=""
    local body status

    while true; do
        case "$step" in
            1)
                body="${description}"
                status=0
                screen_prompt ip "${label} Node" "$body" "${label} VPS IP" "" "$allow_first_back" || status=$?
                case "$status" in
                    0) step=2 ;;
                    10) return 10 ;;
                    20) return 20 ;;
                    *) return "$status" ;;
                esac
                ;;
            2)
                body="Enter the SSH user that works right now.\nA dedicated \"${management_user}\" user will be created later."
                status=0
                screen_prompt user "Initial SSH Access To ${label}" "$body" "SSH user" "root" "yes" || status=$?
                case "$status" in
                    0) step=3 ;;
                    10) step=1 ;;
                    20) return 20 ;;
                    *) return "$status" ;;
                esac
                ;;
            3)
                body="Enter the SSH port that works right now.\nIf the VPS is behind NAT, use the forwarded external port."
                status=0
                screen_prompt port "Initial SSH Access To ${label}" "$body" "SSH port" "22" "yes" || status=$?
                case "$status" in
                    0) step=4 ;;
                    10) step=2 ;;
                    20) return 20 ;;
                    *) return "$status" ;;
                esac
                ;;
            4)
                body="Choose how the installer should connect during bootstrap."
                status=0
                screen_auth_method auth_method "Initial SSH Access To ${label}" "$body" "yes" || status=$?
                case "$status" in
                    0) step=5 ;;
                    10) step=3 ;;
                    20) return 20 ;;
                    *) return "$status" ;;
                esac
                ;;
            5)
                if [[ "$auth_method" == "key" ]]; then
                    body="Enter a local private key path.\nRelative paths are resolved from the current directory and project directory."
                    status=0
                    screen_prompt key_path "Initial SSH Access To ${label}" "$body" "Private key path" "" "yes" || status=$?
                    case "$status" in
                        0)
                            if copy_bootstrap_key "$key_path" "$key_dest"; then
                                bootstrap_key="$key_inside_path"
                                break
                            fi
                            log::warn "SSH key file not found: ${key_path}"
                            sleep 2
                            ;;
                        10) step=4 ;;
                        20) return 20 ;;
                        *) return "$status" ;;
                    esac
                else
                    body="Enter the current SSH password.\nIt will be stored only in encrypted Ansible Vault state."
                    status=0
                    screen_prompt password "Initial SSH Access To ${label}" "$body" "SSH password" "" "yes" "yes" || status=$?
                    case "$status" in
                        0) break ;;
                        10) step=4 ;;
                        20) return 20 ;;
                        *) return "$status" ;;
                    esac
                fi
                ;;
        esac
    done

    printf -v "${prefix}_ip" '%s' "$ip"
    printf -v "${prefix}_user" '%s' "$user"
    printf -v "${prefix}_port" '%s' "$port"
    printf -v "${prefix}_auth_method" '%s' "$auth_method"
    printf -v "${prefix}_key_path" '%s' "$key_path"
    printf -v "${prefix}_bootstrap_key" '%s' "$bootstrap_key"
    printf -v "${prefix}_password" '%s' "$password"
}

init_project() {
    local ingress_ip egress_ip ingress_user ingress_port egress_user egress_port
    local ingress_auth_method ingress_key_path ingress_bootstrap_key ingress_password
    local egress_auth_method egress_key_path egress_bootstrap_key egress_password
    local status

    prepare_state_dirs
    mkdir -p \
        "${generated_dir}/bootstrap/ingress" \
        "${generated_dir}/bootstrap/egress" \
        "${generated_dir}/management/ingress" \
        "${generated_dir}/management/egress" \
        "${tcp_dir}/inventory" \
        "${tcp_dir}/group_vars"

    while true; do
        status=0
        collect_node_config \
            ingress \
            "Ingress" \
            "Ingress Node" \
            "The ingress node is the public VPS where Xray listens.\nClients connect to this server with generated VLESS links." \
            "ingress" \
            "${generated_dir}/bootstrap/ingress/id_ed25519" \
            "/workspace/tcp/.generated/bootstrap/ingress/id_ed25519" \
            "no" || status=$?

        case "$status" in
            0) ;;
            20) return 20 ;;
            *) return "$status" ;;
        esac

        status=0
        collect_node_config \
            egress \
            "Egress" \
            "Egress Node" \
            "The egress node is the exit VPS.\nTraffic selected for PROXY routing leaves through this server." \
            "egress" \
            "${generated_dir}/bootstrap/egress/id_ed25519" \
            "/workspace/tcp/.generated/bootstrap/egress/id_ed25519" \
            "yes" || status=$?

        case "$status" in
            0) break ;;
            10) continue ;;
            20) return 20 ;;
            *) return "$status" ;;
        esac
    done

    generate_management_key "${generated_dir}/management/ingress/id_ed25519" "nitka-ingress-management"
    generate_management_key "${generated_dir}/management/egress/id_ed25519" "nitka-egress-management"
    generate_runtime_ports

    if [[ "${ingress_auth_method}" == "key" ]]; then
        write_inventory_host "${tcp_dir}/inventory/ingress.yml" ingress ingress_node "${ingress_ip}" "${ingress_user}" "${ingress_port}" key "${ingress_bootstrap_key}" ""
    else
        write_inventory_host "${tcp_dir}/inventory/ingress.yml" ingress ingress_node "${ingress_ip}" "${ingress_user}" "${ingress_port}" password "" "${ingress_password}"
    fi

    if [[ "${egress_auth_method}" == "key" ]]; then
        write_inventory_host "${tcp_dir}/inventory/egress.yml" egress egress_node "${egress_ip}" "${egress_user}" "${egress_port}" key "${egress_bootstrap_key}" ""
    else
        write_inventory_host "${tcp_dir}/inventory/egress.yml" egress egress_node "${egress_ip}" "${egress_user}" "${egress_port}" password "" "${egress_password}"
    fi

    write_group_vars "${ingress_ip}" "${egress_ip}" "${runtime_ssh_tun_port}" "${runtime_xhttp_port}" "${runtime_reality_port}"

    initialized_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    ingress_initial_user="${ingress_user}"
    ingress_initial_port="${ingress_port}"
    [[ "${ingress_auth_method}" == "password" ]] && ingress_initial_password="${ingress_password}"
    egress_initial_user="${egress_user}"
    egress_initial_port="${egress_port}"
    [[ "${egress_auth_method}" == "password" ]] && egress_initial_password="${egress_password}"
    ssh_tun_port="${runtime_ssh_tun_port}"
    xhttp_port="${runtime_xhttp_port}"
    reality_port="${runtime_reality_port}"
    ingress_management_user="ingress"
    ingress_management_port="${ingress_initial_port}"
    ingress_sshd_port="${ingress_initial_port}"
    egress_management_user="egress"
    egress_management_port="${egress_initial_port}"
    egress_sshd_port="${egress_initial_port}"
    bootstrap_completed_at=""
    harden_ssh_completed_at=""
    write_project_state

    print_project_plan
}

write_management_inventory() {
    require_file "${project_state}"
    # shellcheck disable=SC1090
    source "${project_state}"

    write_inventory_host \
        "${tcp_dir}/inventory/ingress.yml" \
        ingress \
        ingress_node \
        "${ingress_ip}" \
        ingress \
        "${ingress_initial_port}" \
        key \
        "/workspace/tcp/.generated/management/ingress/id_ed25519" \
        ""

    write_inventory_host \
        "${tcp_dir}/inventory/egress.yml" \
        egress \
        egress_node \
        "${egress_ip}" \
        egress \
        "${egress_initial_port}" \
        key \
        "/workspace/tcp/.generated/management/egress/id_ed25519" \
        ""

    ingress_management_user="ingress"
    ingress_management_port="${ingress_initial_port}"
    egress_management_user="egress"
    egress_management_port="${egress_initial_port}"
    bootstrap_completed_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    write_project_state
}

write_management_inventory_with_ports() {
    local ingress_port="$1"
    local egress_port="$2"

    require_file "${project_state}"
    # shellcheck disable=SC1090
    source "${project_state}"

    write_inventory_host \
        "${tcp_dir}/inventory/ingress.yml" \
        ingress \
        ingress_node \
        "${ingress_ip}" \
        ingress \
        "${ingress_port}" \
        key \
        "/workspace/tcp/.generated/management/ingress/id_ed25519" \
        ""

    write_inventory_host \
        "${tcp_dir}/inventory/egress.yml" \
        egress \
        egress_node \
        "${egress_ip}" \
        egress \
        "${egress_port}" \
        key \
        "/workspace/tcp/.generated/management/egress/id_ed25519" \
        ""
}

last_group_var_value() {
    local file="$1"
    local key="$2"
    awk -F': *' -v key="$key" '$1 == key { value=$2 } END { if (value != "") print value }' "$file" 2>/dev/null || true
}

try_management_port() {
    local group="$1"
    local port="$2"
    local status output

    [[ "$port" =~ ^[0-9]+$ ]] || return 1

    case "$group" in
        ingress)
            write_inventory_host \
                "${tcp_dir}/inventory/ingress.yml" \
                ingress \
                ingress_node \
                "${ingress_ip}" \
                ingress \
                "$port" \
                key \
                "/workspace/tcp/.generated/management/ingress/id_ed25519" \
                ""
            ;;
        egress)
            write_inventory_host \
                "${tcp_dir}/inventory/egress.yml" \
                egress \
                egress_node \
                "${egress_ip}" \
                egress \
                "$port" \
                key \
                "/workspace/tcp/.generated/management/egress/id_ed25519" \
                ""
            ;;
        *)
            return 1
            ;;
    esac

    status=0
    output="$(
        ansible_run ansible \
        -i "inventory/${group}.yml" \
        "$group" \
        -m raw \
        -a "sudo -n true" 2>&1
    )" || status=$?

    if [[ "$status" -eq 0 ]]; then
        printf '%s management access OK on port %s.\n' "${group}" "${port}"
    else
        printf '%s management access failed on port %s.\n' "${group}" "${port}"
    fi

    [[ "$status" -eq 0 ]]
}

recover_bootstrap_management_access() {
    local ingress_candidates egress_candidates port ingress_recovered egress_recovered

    require_file "${project_state}"
    # shellcheck disable=SC1090
    source "${project_state}"

    ingress_candidates="$(
        printf '%s\n' \
            "${ingress_management_port:-}" \
            "${ingress_sshd_port:-}" \
            "$(last_group_var_value "${tcp_dir}/group_vars/ingress.yml" harden_ssh_port)" \
            "${ingress_initial_port:-}" \
        | awk 'NF && !seen[$0]++'
    )"
    egress_candidates="$(
        printf '%s\n' \
            "${egress_management_port:-}" \
            "${egress_sshd_port:-}" \
            "$(last_group_var_value "${tcp_dir}/group_vars/egress.yml" harden_ssh_port)" \
            "${egress_initial_port:-}" \
        | awk 'NF && !seen[$0]++'
    )"

    ingress_recovered="no"
    egress_recovered="no"

    for port in $ingress_candidates; do
        printf 'Trying ingress management recovery on port %s.\n' "$port"
        if try_management_port ingress "$port"; then
            ingress_management_port="$port"
            ingress_sshd_port="${ingress_sshd_port:-$port}"
            ingress_recovered="yes"
            break
        fi
    done

    for port in $egress_candidates; do
        printf 'Trying egress management recovery on port %s.\n' "$port"
        if try_management_port egress "$port"; then
            egress_management_port="$port"
            egress_sshd_port="${egress_sshd_port:-$port}"
            egress_recovered="yes"
            break
        fi
    done

    [[ "$ingress_recovered" == "yes" && "$egress_recovered" == "yes" ]] || return 1

    ingress_management_user="ingress"
    egress_management_user="egress"
    bootstrap_completed_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    write_project_state
    write_management_inventory_with_ports "${ingress_management_port}" "${egress_management_port}"
    printf 'Recovered bootstrap via existing management users.\n'
}

bootstrap_project() {
    require_file "${project_state}"
    require_file "${generated_dir}/management/ingress/id_ed25519.pub"
    require_file "${generated_dir}/management/egress/id_ed25519.pub"

    if recover_bootstrap_management_access; then
        printf 'Existing management access verified; skipping bootstrap through initial users.\n'
        return 0
    fi
    write_bootstrap_inventory_from_state

    if ! ansible_run ansible-playbook -i inventory/egress.yml -i inventory/ingress.yml bootstrap.yml; then
        log::warn "Bootstrap through initial access failed; trying existing management users."
        recover_bootstrap_management_access || return 1
        return 0
    fi
    write_management_inventory
    printf 'Bootstrap completed. Inventory now uses generated management users and keys.\n'
}

require_management_keys() {
    require_file "${generated_dir}/management/ingress/id_ed25519"
    require_file "${generated_dir}/management/ingress/id_ed25519.pub"
    require_file "${generated_dir}/management/egress/id_ed25519"
    require_file "${generated_dir}/management/egress/id_ed25519.pub"
}

choose_harden_port() {
    local connect_var_name="$1"
    local listen_var_name="$2"
    local node_label="$3"
    local current_connect_port="$4"
    local current_listen_port="$5"
    local answer confirm new_port

    printf '%s\n' "${node_label}"
    ui_kv "current connect port" "${current_connect_port}"
    ui_kv "current sshd listen port" "${current_listen_port}"
    printf '\n'
    printf 'Changing SSH port reduces default-port exposure.\n'
    printf 'If this VPS is behind NAT or provider port forwarding, keep the current port unless the new one is forwarded.\n\n'
    prompt_yes_no answer "Rotate ${node_label} SSH port?" "yes"

    if [[ "${answer}" == "yes" ]]; then
        new_port="$(generate_port 20000 60000)"
        printf '\n'
        ui_kv "generated port" "${new_port}"
        printf '\n'
        prompt_yes_no confirm "Apply this port? Confirm firewall/provider/NAT allows it" "no"
        if [[ "${confirm}" != "yes" ]]; then
            printf 'Keeping current SSH connect/listen ports: %s/%s\n' "${current_connect_port}" "${current_listen_port}"
            printf -v "${connect_var_name}" '%s' "${current_connect_port}"
            printf -v "${listen_var_name}" '%s' "${current_listen_port}"
            return 0
        fi
        printf -v "${connect_var_name}" '%s' "${new_port}"
        printf -v "${listen_var_name}" '%s' "${new_port}"
    else
        printf -v "${connect_var_name}" '%s' "${current_connect_port}"
        printf -v "${listen_var_name}" '%s' "${current_listen_port}"
    fi
}

detect_remote_sshd_port() {
    local var_name="$1"
    local group="$2"
    local fallback_port="$3"
    local out port

    out="$(
        ansible_run ansible \
            -i "inventory/${group}.yml" \
            "${group}" \
            -m raw \
            -a "sudo -n sshd -T 2>/dev/null | awk '/^port /{print \$2; exit}'" 2>/dev/null || true
    )"
    port="$(printf '%s\n' "$out" | awk '/^[0-9]+$/ { print; exit }')"
    printf -v "${var_name}" '%s' "${port:-$fallback_port}"
}

sync_direct_management_port() {
    local result_var_name="$1"
    local node_label="$2"
    local current_connect_port="$3"
    local detected_listen_port="$4"
    local inventory_group="$5"
    local status

    printf -v "${result_var_name}" '%s' "${current_connect_port}"
    [[ "${current_connect_port}" != "${detected_listen_port}" ]] || return 0
    [[ "${detected_listen_port}" =~ ^[0-9]+$ ]] || return 0

    printf '\n'
    printf '%s SSH port mismatch detected.\n' "${node_label}"
    ui_kv "current connect port" "${current_connect_port}"
    ui_kv "detected sshd listen port" "${detected_listen_port}"
    printf 'Checking direct SSH access on detected listen port before syncing inventory.\n'

    case "${inventory_group}" in
        egress)
            write_management_inventory_with_ports "${ingress_current_port}" "${detected_listen_port}"
            ;;
        ingress)
            write_management_inventory_with_ports "${detected_listen_port}" "${egress_current_port}"
            ;;
    esac

    status=0
    ansible_run ansible \
        -i "inventory/${inventory_group}.yml" \
        "${inventory_group}" \
        -m raw \
        -a "sudo -n true" || status=$?

    if [[ "$status" -eq 0 ]]; then
        printf '%s inventory synced to port %s.\n' "${node_label}" "${detected_listen_port}"
        printf -v "${result_var_name}" '%s' "${detected_listen_port}"
        return 0
    fi

    printf '%s detected port %s is not reachable from Ansible; keeping connect port %s.\n' "${node_label}" "${detected_listen_port}" "${current_connect_port}" >&2
    write_management_inventory_with_ports "${ingress_current_port}" "${egress_current_port}"
    return 0
}

harden_ssh_project() {
    local ingress_current_port egress_current_port
    local ingress_current_sshd_port egress_current_sshd_port
    local ingress_target_port egress_target_port
    local ingress_target_sshd_port egress_target_sshd_port

    require_file "${project_state}"
    # shellcheck disable=SC1090
    source "${project_state}"
    require_management_keys

    ingress_current_port="${ingress_management_port:-${ingress_initial_port}}"
    egress_current_port="${egress_management_port:-${egress_initial_port}}"
    detect_remote_sshd_port ingress_current_sshd_port ingress "${ingress_sshd_port:-22}"
    detect_remote_sshd_port egress_current_sshd_port egress "${egress_sshd_port:-$egress_current_port}"
    sync_direct_management_port egress_current_port "Egress" "${egress_current_port}" "${egress_current_sshd_port}" egress

    ui_section "SSH Hardening"
    printf 'OpenSSH will be hardened after bootstrap.\n'
    printf 'A rollback timer is created before reload and cancelled only after new access is verified.\n\n'

    choose_harden_port ingress_target_port ingress_target_sshd_port "Ingress" "${ingress_current_port}" "${ingress_current_sshd_port}"
    printf '\n'
    choose_harden_port egress_target_port egress_target_sshd_port "Egress" "${egress_current_port}" "${egress_current_sshd_port}"

    ui_section "SSH Hardening Plan"
    printf 'Ingress\n'
    ui_kv "management user" "ingress"
    ui_kv "connect port" "${ingress_target_port}"
    ui_kv "sshd listen port" "${ingress_target_sshd_port}"
    printf '\n'
    printf 'Egress\n'
    ui_kv "management user" "egress"
    ui_kv "connect port" "${egress_target_port}"
    ui_kv "sshd listen port" "${egress_target_sshd_port}"
    printf '\n'

    refresh_group_vars_from_state
    append_harden_group_vars "${ingress_initial_user}" "${ingress_target_sshd_port}" "${egress_initial_user}" "${egress_target_sshd_port}"

    ansible_run ansible-playbook -i inventory/egress.yml -i inventory/ingress.yml harden_ssh.yml || return $?

    write_management_inventory_with_ports "${ingress_target_port}" "${egress_target_port}"

    if verify_hardened_ssh; then
        ansible_run ansible -i inventory/egress.yml -i inventory/ingress.yml all -m raw -a "sudo systemctl stop nitka-ssh-rollback.timer nitka-ssh-rollback.service 2>/dev/null || true; sudo systemctl reset-failed nitka-ssh-rollback.timer nitka-ssh-rollback.service 2>/dev/null || true" || return $?
        ingress_management_user="ingress"
        ingress_management_port="${ingress_target_port}"
        ingress_sshd_port="${ingress_target_sshd_port}"
        egress_management_user="egress"
        egress_management_port="${egress_target_port}"
        egress_sshd_port="${egress_target_sshd_port}"
        harden_ssh_completed_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        write_project_state
        printf '\nSSH hardening completed and verified.\n'
    else
        printf 'SSH hardening verification failed. Emergency rollback timer should restore sshd_config in about 2 minutes.\n' >&2
        debug_ssh_project || true
        write_management_inventory_with_ports "${ingress_current_port}" "${egress_current_port}"
        return 1
    fi
}

verify_hardened_ssh() {
    local attempt

    for attempt in {1..12}; do
        if ansible_run ansible -i inventory/egress.yml -i inventory/ingress.yml all -m raw -a "sudo -n true"; then
            return 0
        fi
        log::warn "SSH verification failed; retrying (${attempt}/12)."
        sleep 5
    done

    return 1
}

debug_ssh_project() {
    ansible_run ansible-playbook -i inventory/egress.yml -i inventory/ingress.yml debug_ssh.yml
}

debug_docker_project() {
    ansible_run ansible-playbook -i inventory/egress.yml -i inventory/ingress.yml debug_docker.yml
}

audit_images_project() {
    local target="${1:-all}" inventory="" limit="" remote_dir=""
    local audit_template="" audit_remote_script="/tmp/nitka-audit-images.sh"

    case "$target" in
        egress)
            inventory="-i inventory/egress.yml"
            limit="egress"
            remote_dir="/opt/nitka-egress"
            ;;
        ingress)
            inventory="-i inventory/ingress.yml"
            limit="ingress"
            remote_dir="/opt/nitka-ingress"
            ;;
        all|"")
            audit_images_project egress
            audit_images_project ingress
            return 0
            ;;
        *)
            printf 'Usage: ./run.sh --audit-images egress|ingress|all\n' >&2
            return 1
            ;;
    esac

    audit_template="roles/system_base/templates/remote-image-audit.sh.j2"

    # shellcheck disable=SC2086
    ansible_run ansible \
        $inventory \
        "$limit" \
        -b \
        -m template \
        -a "src=${audit_template} dest=${audit_remote_script} mode=0755" \
        -e "audit_remote_dir=${remote_dir}" || return $?

    local rc=0
    set +e
    # shellcheck disable=SC2086
    ansible_run ansible \
        $inventory \
        "$limit" \
        -b \
        -m command \
        -a "$audit_remote_script"
    rc=$?
    # shellcheck disable=SC2086
    ansible_run ansible \
        $inventory \
        "$limit" \
        -b \
        -m file \
        -a "path=${audit_remote_script} state=absent" >/dev/null 2>&1 || true
    set -e
    return "$rc"
}

deploy_project() {
    require_management_keys
    ansible_run ansible-playbook -i inventory/egress.yml egress.yml || {
        [[ "${nitka_debug}" == "1" ]] && debug_docker_project || true
        return 1
    }
    ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml || {
        [[ "${nitka_debug}" == "1" ]] && debug_docker_project || true
        return 1
    }
}

show_state_path_menu() {
    local choice

    while true; do
        ui_clear
        ui_title "State Path"
        printf '%-25s %s\n' "State root:" "${state_root}"
        printf '%-25s %s\n' "Vault file:" "${vault_file}"
        printf '%-25s %s\n' "Materialized cache:" "${generated_dir}"
        ui_controls "yes" "yes"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            b|B|m|M|"")
                return 0
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

show_lock_cache_menu() {
    local choice

    lock_state

    while true; do
        ui_clear
        ui_title "Local Cache Locked"
        printf '%-25s %s\n' "Removed:" "${generated_dir}"
        ui_controls "yes" "yes"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            b|B|m|M|"")
                return 0
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

show_help_menu() {
    local choice

    while true; do
        ui_clear
        usage
        ui_controls "yes" "yes"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            b|B|m|M|"")
                return 0
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

show_setup_menu() {
    local choice

    while true; do
        ui_clear
        ui_title "Nitka"
        printf '%-25s %s\n' "Mode:" "Setup wizard"
        printf '%-25s %s\n' "Ingress:" "Public entrypoint for client connections"
        printf '%-25s %s\n' "Egress:" "Exit node for proxied traffic"
        printf '%-25s %s\n' "Vault:" "${vault_file}"
        printf '%-25s %s\n' "State cache:" "${generated_dir}"
        printf '\n'
        printf '%s1.%s %sStart setup%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s2.%s %sShow state path%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s3.%s %sLock local cache%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s4.%s %sHelp%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        ui_controls "no" "no"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            1|"")
                return 0
                ;;
            2)
                show_state_path_menu
                ;;
            3)
                show_lock_cache_menu
                ;;
            4)
                show_help_menu
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

wait_menu_return() {
    local choice

    ui_controls "yes" "yes"
    printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
    read -r choice || true

    case "$choice" in
        x|X)
            exit_program
            ;;
    esac
}

wait_action_return() {
    local key

    [[ -t 0 ]] || return 0
    printf '\n%sPress Enter or Space to return to the menu.%s' "$COLOR_MUTED" "$COLOR_RESET"
    read -r -s -n 1 key || true
    printf '\n'

    case "$key" in
        x|X)
            exit_program
            ;;
    esac
}

show_node_status_menu() {
    materialize_from_vault
    ui_clear
    ui_title "Current State"
    if [[ -f "${project_state}" ]]; then
        print_node_status
    else
        printf 'No encrypted project state found yet.\n'
    fi
    printf '\n'
    wait_menu_return
    lock_state
}

show_client_links_menu() {
    materialize_from_vault
    if print_client_links; then
        wait_menu_return
    fi
    lock_state
}

show_setup_flow_menu() {
    local choice

    while true; do
        ui_clear
        ui_title "Setup"
        printf 'Use the full wizard for normal installation. Manual steps are only for recovery or debugging.\n\n'
        printf '%s1.%s %sFull setup wizard%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '\n%sAdvanced manual steps%s\n' "$COLOR_SUBTITLE" "$COLOR_RESET"
        printf '%s2.%s %sInitialize encrypted state only%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s3.%s %sBuild Ansible runner only%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s4.%s %sBootstrap management users only%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s5.%s %sHarden SSH only%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        ui_controls "yes" "yes"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            1|"")
                run_setup_project
                wait_action_return
                ;;
            2)
                init_project
                persist_vault
                lock_state
                wait_action_return
                ;;
            3)
                build_ansible_runner
                wait_action_return
                ;;
            4)
                with_materialized_state bootstrap_project
                wait_action_return
                ;;
            5)
                with_materialized_state harden_ssh_project
                wait_action_return
                ;;
            b|B|m|M)
                return 0
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

show_deploy_menu() {
    local choice

    while true; do
        ui_clear
        ui_title "Deploy"
        printf '%s1.%s %sDeploy all: egress then ingress%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s2.%s %sDeploy egress only%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s3.%s %sDeploy ingress only%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s4.%s %sSyntax check%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        ui_controls "yes" "yes"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            1|"")
                with_materialized_state deploy_project
                wait_action_return
                ;;
            2)
                with_materialized_state ansible_run ansible-playbook -i inventory/egress.yml egress.yml
                wait_action_return
                ;;
            3)
                with_materialized_state ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml
                wait_action_return
                ;;
            4)
                prepare_state_dirs
                ansible_run ansible-playbook -i inventory/egress.yml egress.yml --syntax-check
                ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml --syntax-check
                wait_action_return
                ;;
            b|B|m|M)
                return 0
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

show_operations_menu() {
    local choice

    while true; do
        ui_clear
        ui_title "Operations"
        printf '%s1.%s %sShow client links%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s2.%s %sRotate Xray credentials%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s3.%s %sRotate SSH TUN keypair%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s4.%s %sChange Vault password%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s5.%s %sUninstall project from nodes%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s6.%s %sLock local cache%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s7.%s %sAudit Docker images%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s8.%s %sBackup vault state%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s9.%s %sRestore vault state%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        ui_controls "yes" "yes"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            1|"")
                show_client_links_menu
                ;;
            2)
                materialize_from_vault
                regen_xray_keys
                refresh_group_vars_from_state
                ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml
                print_client_links
                persist_vault
                lock_state
                wait_menu_return
                ;;
            3)
                materialize_from_vault
                regen_ssh_tun_key
                ansible_run ansible-playbook -i inventory/egress.yml egress.yml
                ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml
                persist_vault
                lock_state
                wait_action_return
                ;;
            4)
                rekey_vault
                lock_state
                wait_action_return
                ;;
            5)
                with_materialized_state ansible_run ansible-playbook -i inventory/egress.yml -i inventory/ingress.yml uninstall.yml
                wait_action_return
                ;;
            6)
                show_lock_cache_menu
                ;;
            7)
                show_image_audit_menu
                ;;
            8)
                screen_prompt answer "Backup Vault State" "" "Output archive path" "$(state_backup_default_path)" "no" "no" "yes"
                backup_state "$answer"
                wait_action_return
                ;;
            9)
                screen_prompt answer "Restore Vault State" "" "Archive path" "" "no" "no" "yes"
                restore_state "$answer"
                wait_action_return
                ;;
            b|B|m|M)
                return 0
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

show_image_audit_menu() {
    local choice

    while true; do
        ui_clear
        ui_title "Image Audit"
        printf '%s1.%s %sAudit all nodes%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s2.%s %sAudit egress only%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s3.%s %sAudit ingress only%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        ui_controls "yes" "yes"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            1|"")
                with_materialized_state audit_images_project all
                wait_action_return
                ;;
            2)
                with_materialized_state audit_images_project egress
                wait_action_return
                ;;
            3)
                with_materialized_state audit_images_project ingress
                wait_action_return
                ;;
            b|B)
                return 0
                ;;
            m|M)
                return 0
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

show_main_menu() {
    local choice

    while true; do
        ui_clear
        ui_title "Nitka"
        printf '%-25s %s\n' "Vault:" "${vault_file}"
        printf '%-25s %s\n' "State cache:" "${generated_dir}"
        printf '\n'
        printf '%s1.%s %sSetup flow%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s2.%s %sDeploy%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s3.%s %sOperations%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s4.%s %sCurrent node state%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s5.%s %sClient links%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s6.%s %sState path%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '%s7.%s %sHelp%s\n' "$COLOR_LINE" "$COLOR_RESET" "$COLOR_TEXT" "$COLOR_RESET"
        printf '\n%s* Direct non-interactive commands are available via ./run.sh --help. Add --debug to any command or menu launch for verbose logs.%s\n' "$COLOR_MUTED" "$COLOR_RESET"
        ui_controls "no" "no"
        printf '%s?: %s' "$COLOR_LINE" "$COLOR_RESET"
        read -r choice || true

        case "$choice" in
            1|"")
                show_setup_flow_menu
                ;;
            2)
                show_deploy_menu
                ;;
            3)
                show_operations_menu
                ;;
            4)
                show_node_status_menu
                ;;
            5)
                show_client_links_menu
                ;;
            6)
                show_state_path_menu
                ;;
            7)
                show_help_menu
                ;;
            x|X)
                exit_program
                ;;
            *)
                log::warn "Invalid option."
                sleep 1
                ;;
        esac
    done
}

setup_project() {
    local answer status

    show_setup_menu

    ensure_vault_password_file

    materialize_from_vault
    if [[ -f "${project_state}" ]]; then
        ui_section "Existing State"
        printf 'Existing encrypted state was found and materialized.\n\n'
        print_project_plan
        printf '\n'
        prompt_yes_no answer "Reuse this state?" "yes"
        if [[ "${answer}" != "yes" ]]; then
            status=0
            init_project || status=$?
            case "$status" in
                20) setup_project; return ;;
                0) persist_vault || return $? ;;
                *) return "$status" ;;
            esac
        fi
    else
        status=0
        init_project || status=$?
        case "$status" in
            20) setup_project; return ;;
            0) persist_vault || return $? ;;
            *) return "$status" ;;
        esac
    fi

    printf '\n'
    prompt_yes_no answer "Continue deployment with this plan?" "yes"
    if [[ "${answer}" != "yes" ]]; then
        lock_state
        return 0
    fi

    ui_title "Running Setup"

    ui_step "1/6" "Building Ansible runner..."
    build_ansible_runner || return $?

    ui_step "2/6" "Bootstrapping management users..."
    bootstrap_project || return $?
    persist_vault || return $?

    ui_step "3/6" "Hardening SSH..."
    harden_ssh_project || return $?
    persist_vault || return $?

    ui_step "4/6" "Deploying egress stack..."
    ansible_run ansible-playbook -i inventory/egress.yml egress.yml || return $?
    persist_vault || return $?

    ui_step "5/6" "Deploying ingress stack..."
    ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml || return $?

    require_file "${generated_dir}/ingress/share-links.txt" || return $?

    ui_title "Setup Complete"
    printf 'Client Links\n'
    printf '%s\n\n' '------------'
    cat "${generated_dir}/ingress/share-links.txt"
    printf '\nUseful commands\n'
    printf '%s\n\n' '---------------'
    printf 'Show links again:\n'
    printf '  ./run.sh --show-links\n\n'
    printf 'Redeploy:\n'
    printf '  ./run.sh --deploy\n\n'
    printf 'Rotate Xray credentials:\n'
    printf '  ./run.sh --rotate xray\n\n'
    printf 'Lock local decrypted cache:\n'
    printf '  ./run.sh --lock\n\n'
    printf 'Encrypted state:\n'
    printf '  %s\n' "${vault_file}"

    ui_step "6/6" "Saving encrypted state and locking local cache..."
    persist_vault || return $?
    lock_state
}

run_setup_project() {
    local status

    set +e
    setup_project
    status=$?
    set -e

    if [[ "$status" -ne 0 ]]; then
        log::warn "Setup failed; preserving recoverable encrypted state when possible."
        if [[ -f "${project_state}" ]]; then
            if persist_vault; then
                lock_state
                log::warn "Encrypted state was saved. Re-run ./run.sh --setup to converge the nodes."
            else
                log::error "Could not save Vault. Keeping materialized state at: ${generated_dir}"
                log::error "Do not delete this directory until Vault is saved."
            fi
        else
            lock_state
        fi
    fi

    return "$status"
}

regen_ssh_tun_key() {
    rm -f \
        "${generated_dir}/egress/ssh/id_ed25519" \
        "${generated_dir}/egress/ssh/id_ed25519.pub"
    printf 'SSH TUN keypair removed. Run ./run.sh --deploy to regenerate and apply it.\n'
}

regen_xray_keys() {
    rm -f \
        "${generated_dir}/ingress/state/xhttp_uuid" \
        "${generated_dir}/ingress/state/reality_uuid" \
        "${generated_dir}/ingress/state/xhttp_port" \
        "${generated_dir}/ingress/state/reality_port" \
        "${generated_dir}/ingress/state/reality_short_id" \
        "${generated_dir}/ingress/state/reality_private_key" \
        "${generated_dir}/ingress/state/reality_public_key" \
        "${generated_dir}/ingress/share-links.txt"
    remove_project_state_keys \
        xhttp_port \
        reality_port \
        xray_xhttp_uuid \
        xray_reality_uuid \
        xray_reality_short_id \
        xray_reality_private_key \
        xray_reality_public_key
    printf 'Xray generated state removed. Run ./run.sh --ingress to regenerate and apply it.\n'
}

refresh_group_vars_from_state() {
    require_file "${project_state}"
    # shellcheck disable=SC1090
    source "${project_state}"
    generate_runtime_ports
    write_group_vars "${ingress_ip}" "${egress_ip}" "${runtime_ssh_tun_port}" "${runtime_xhttp_port}" "${runtime_reality_port}"
}

args=()
for arg in "$@"; do
    case "${arg}" in
        --debug)
            nitka_debug=1
            ;;
        *)
            args+=("${arg}")
            ;;
    esac
done
if ((${#args[@]} > 0)); then
    set -- "${args[@]}"
else
    set --
fi
export NITKA_DEBUG="${nitka_debug}"

cmd="${1:-}"
if [[ -n "${cmd}" && "${cmd}" != --* && "${cmd}" != "-h" ]]; then
    theme::init
    printf 'Unknown command: %s\n\n' "${cmd}" >&2
    usage >&2
    exit 1
fi
cmd="${cmd#--}"

case "${cmd}" in
    setup)
        theme::init
        run_setup_project
        ;;

    init)
        theme::init
        init_project
        persist_vault
        lock_state
        ;;

    bootstrap)
        theme::init
        with_materialized_state bootstrap_project
        ;;

    harden-ssh)
        theme::init
        with_materialized_state harden_ssh_project
        ;;

    build)
        theme::init
        build_ansible_runner
        ;;

    rebuild)
        theme::init
        build_ansible_runner_no_cache
        ;;

    shell)
        theme::init
        prepare_state_dirs
        ansible_run bash
        ;;

    syntax)
        theme::init
        prepare_state_dirs
        ansible_run ansible-playbook -i inventory/egress.yml egress.yml --syntax-check
        ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml --syntax-check
        ;;

    egress)
        theme::init
        with_materialized_state ansible_run ansible-playbook -i inventory/egress.yml egress.yml
        ;;

    ingress)
        theme::init
        with_materialized_state ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml
        ;;

    deploy)
        theme::init
        with_materialized_state deploy_project
        ;;

    debug-ssh)
        theme::init
        with_materialized_state debug_ssh_project
        ;;

    debug-docker)
        theme::init
        with_materialized_state debug_docker_project
        ;;

    uninstall)
        theme::init
        with_materialized_state ansible_run ansible-playbook -i inventory/egress.yml -i inventory/ingress.yml uninstall.yml
        ;;

    rotate)
        theme::init
        case "${2:-}" in
            xray)
                materialize_from_vault
                regen_xray_keys
                refresh_group_vars_from_state
                ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml
                require_file "${generated_dir}/ingress/share-links.txt"
                cat "${generated_dir}/ingress/share-links.txt"
                persist_vault
                lock_state
                ;;
            ssh-tun)
                materialize_from_vault
                regen_ssh_tun_key
                ansible_run ansible-playbook -i inventory/egress.yml egress.yml
                ansible_run ansible-playbook -i inventory/ingress.yml ingress.yml
                persist_vault
                lock_state
                ;;
            *)
                printf 'Usage: ./run.sh rotate xray|ssh-tun\n' >&2
                exit 1
                ;;
        esac
        ;;

    rekey)
        theme::init
        rekey_vault
        lock_state
        ;;

    regen-ssh-tun-key)
        theme::init
        materialize_from_vault
        regen_ssh_tun_key
        persist_vault
        lock_state
        ;;

    regen-xray-keys)
        theme::init
        materialize_from_vault
        regen_xray_keys
        persist_vault
        lock_state
        ;;

    show-links)
        theme::init
        materialize_from_vault
        print_client_links
        lock_state
        ;;

    state-path)
        theme::init
        printf 'state_root=%s\nvault_file=%s\nmaterialized_dir=%s\n' "${state_root}" "${vault_file}" "${generated_dir}"
        ;;

    check-updates)
        theme::init
        check_updates
        ;;

    audit-images)
        theme::init
        with_materialized_state audit_images_project "${2:-all}"
        ;;

    lock)
        theme::init
        lock_state
        ;;

    save-state)
        theme::init
        persist_vault
        lock_state
        ;;

    backup-state)
        theme::init
        backup_state "${2:-}"
        ;;

    restore-state)
        theme::init
        restore_state "${2:-}"
        ;;

    "")
        theme::init
        show_main_menu
        ;;

    -h|--help|help)
        theme::init
        usage
        ;;

    *)
        theme::init
        printf 'Unknown command: %s\n\n' "${cmd}" >&2
        usage >&2
        exit 1
        ;;
esac
