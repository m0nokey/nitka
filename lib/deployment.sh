#!/usr/bin/env bash
# shellcheck disable=SC2034

show_ssh_proxy_details() {
    local node="$1" state="$2" info
    if ! info="$(python3 "$ROOT_DIR/scripts/state_cli.py" ssh-transport-info "$node" <"$state")"; then
        show_result_screen "SSH proxy was deployed, but its connection details could not be read from the Vault."
        return 1
    fi
    clear_screen
    menu_heading "SSH proxy access:"
    echo
    python3 -c '
import json
import sys
from urllib.parse import quote

data = json.load(sys.stdin)
host = data["host"]
port = data["port"]
print()
print(f"SSH address:          {host}")
print(f"SSH port:             {port}")
print("TCP proxy:            supported")
print("Native UDP:           not supported")
print("UDP relay:            best effort, UDP-over-TCP")
for index, key in enumerate(data["access_keys"], 1):
    username = key["username"]
    access_fingerprint = key.get("fingerprint", "")
    print()
    print(f"{index}. Access key {index}")
    print(f"Username:  {username}")
    if access_fingerprint:
        print(f"Public key fingerprint: {access_fingerprint}")
    print()
    print("Terminal command:")
    print(f"ssh -N -T -D 127.0.0.1:1080 -p {port} -i /path/to/private_key {username}@{host}")
    print()
    print("Shadowrocket link:")
    private_key = quote(key["private_key"].rstrip("\r\n"), safe="/")
    shadowrocket_host = quote(str(host), safe="")
    shadowrocket_username = quote(username, safe="")
    print(
        f"ssh://{shadowrocket_username}:@{shadowrocket_host}:{port}"
        f"?path=id_25519&pk={private_key}#SSH%20Proxy"
    )
    print()
    print("Private key:")
    print(key["private_key"], end="")
' <<<"$info"
    wait_action_return
}

show_saved_ssh_proxy_details() {
    local node="$1" state
    state="$(mktemp "$RUNTIME_TMP_DIR/.ssh-proxy-details.XXXXXX")"
    if ! read_vault_state "$state"; then
        rm -f "$state"
        return 1
    fi
    capture_ssh_proxy_host_fingerprint "$node" "$state" || true
    show_ssh_proxy_details "$node" "$state"
    local result=$?
    rm -f "$state"
    return "$result"
}

pending_install_details() {
    local state="$1" transaction_id="$2"
    python3 - "$transaction_id" "$state" <<'PY'
import json
import sys

transaction_id, state_path = sys.argv[1:]
state = json.load(open(state_path, encoding="utf-8"))
operation = state.get("pending_operations", {}).get(transaction_id, {})
node = operation.get("node", {})
print(operation.get("node_name", ""))
print(node.get("host", ""))
print(operation.get("phase", ""))
PY
}

pending_install_resume() {
    local transaction_id="$1" node="$2" state candidate
    state="$(mktemp "$RUNTIME_TMP_DIR/.pending-view.XXXXXX")"
    candidate="$(mktemp "$RUNTIME_TMP_DIR/.pending-resume.XXXXXX")"
    if ! read_vault_state "$state" || ! python3 "$ROOT_DIR/scripts/state_cli.py" \
        restore-pending "$transaction_id" <"$state" >"$candidate"; then
        rm -f "$state" "$candidate"
        return 1
    fi
    rm -f "$state"
    PENDING_TRANSACTION_ID="$transaction_id"
    pipeline_start "Resuming VPN installation" install
    if deploy_node "$node" "$candidate" "" 1; then
        if pending_install_commit "$transaction_id"; then
            PENDING_TRANSACTION_ID=""
            rm -f "$candidate"
            pipeline_complete "VPN server installation resumed successfully." 1
            return 0
        fi
    fi
    pipeline_abort
    PENDING_TRANSACTION_ID=""
    rm -f "$candidate"
    show_result_screen "The installation is still incomplete." \
        "The pending transaction was kept in the encrypted Vault."
    return 1
}

pending_install_cleanup() {
    local transaction_id="$1" state candidate extra inventory_dir inventory host user port management_port password password_yaml private_key_value private_key_path management_private_key node rc
    local -a inventory_args=()
    state="$(mktemp "$RUNTIME_TMP_DIR/.pending-view.XXXXXX")"
    candidate="$(mktemp "$RUNTIME_TMP_DIR/.pending-cleanup-state.XXXXXX")"
    extra="$(mktemp "$RUNTIME_TMP_DIR/.pending-cleanup-extra.XXXXXX")"
    inventory_dir="$(mktemp -d "$RUNTIME_TMP_DIR/.pending-cleanup.XXXXXX")"
    inventory="$inventory_dir/hosts.yml"
    private_key_path="$inventory_dir/id_ed25519"
    if ! read_vault_state "$state" || ! python3 "$ROOT_DIR/scripts/state_cli.py" \
        restore-pending "$transaction_id" <"$state" >"$candidate"; then
        rm -f "$state" "$candidate" "$extra" "$private_key_path"
        rm -rf "$inventory_dir"
        return 1
    fi
    node="$(python3 -c 'import json,sys; print(next(iter(json.load(open(sys.argv[1]))["nodes"])))' "$candidate")"
    host="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nodes"][sys.argv[2]]["host"])' "$candidate" "$node")"
    user="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nodes"][sys.argv[2]]["bootstrap"].get("user", "root"))' "$candidate" "$node")"
    port="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nodes"][sys.argv[2]]["bootstrap"].get("port", 22))' "$candidate" "$node")"
    management_port="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nodes"][sys.argv[2]]["management"].get("port", 22))' "$candidate" "$node")"
    password="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nodes"][sys.argv[2]]["bootstrap"].get("password", ""), end="")' "$candidate" "$node")"
    management_private_key="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nodes"][sys.argv[2]]["management"].get("private_key", ""), end="")' "$candidate" "$node")"
    python3 "$ROOT_DIR/scripts/state_cli.py" extract "$node" <"$candidate" >"$extra"
    if [[ -n "$password" ]]; then
        password_yaml="$(yaml_scalar "$password")"
        printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $node:" "          ansible_host: $host" "          ansible_user: $user" "          ansible_port: $port" "          ansible_password: $password_yaml" "          ansible_become_password: $password_yaml" "          ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o PubkeyAuthentication=no -o PreferredAuthentications=password'" >"$inventory"
        unset password_yaml password
        inventory_args=(-i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/remove.yml")
    else
        private_key_value="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nodes"][sys.argv[2]]["bootstrap"].get("private_key", ""), end="")' "$candidate" "$node")"
        [[ -n "$private_key_value" ]] || { rm -f "$state" "$candidate" "$extra" "$private_key_path"; rm -rf "$inventory_dir"; return 1; }
        printf '%s' "$private_key_value" >"$private_key_path"
        chmod 600 "$private_key_path"
        printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $node:" "          ansible_host: $host" "          ansible_user: $user" "          ansible_port: $port" "          ansible_ssh_private_key_file: $private_key_path" "          ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes'" >"$inventory"
        inventory_args=(-i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/remove.yml" --private-key "$private_key_path")
    fi
    chmod 600 "$inventory"
    pipeline_start "Cleaning incomplete installation" remove
    if run_ansible_playbook --quiet "${inventory_args[@]}"; then
        rc=0
    else
        rc=$?
    fi
    if ((rc != 0)) && [[ -n "$management_private_key" ]]; then
        printf '%s' "$management_private_key" >"$private_key_path"
        chmod 600 "$private_key_path"
        printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $node:" "          ansible_host: $host" "          ansible_user: deploy" "          ansible_port: $management_port" "          ansible_ssh_private_key_file: $private_key_path" "          ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes'" >"$inventory"
        inventory_args=(-i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/remove.yml" --private-key "$private_key_path")
        if run_ansible_playbook --quiet "${inventory_args[@]}"; then
            rc=0
        else
            rc=$?
        fi
    fi
    rm -f "$state" "$candidate" "$extra" "$private_key_path"
    rm -rf "$inventory_dir"
    if ((rc != 0)); then
        pipeline_abort
        show_result_screen "The VPS could not be cleaned." \
            "The pending installation remains in the encrypted Vault."
        return "$rc"
    fi
    if ! pending_install_abort "$transaction_id"; then
        pipeline_abort
        show_result_screen "The VPS was cleaned, but the pending Vault transaction could not be removed."
        return 1
    fi
    pipeline_complete "The incomplete installation was removed and the original SSH access was restored." 1
}

recover_pending_installation() {
    local state pending transaction_id node host phase choice
    [[ -f "$VAULT_FILE" ]] || return 0
    state="$(mktemp "$RUNTIME_TMP_DIR/.pending-view.XXXXXX")"
    if ! read_vault_state "$state"; then
        rm -f "$state"
        return 1
    fi
    pending="$(python3 "$ROOT_DIR/scripts/state_cli.py" pending-list <"$state")"
    transaction_id="$(python3 -c 'import json,sys; data=json.load(sys.stdin); print(data[0]["transaction_id"] if data else "")' <<<"$pending")"
    if [[ -z "$transaction_id" ]]; then
        rm -f "$state"
        return 0
    fi
    mapfile -t details < <(pending_install_details "$state" "$transaction_id")
    node="${details[0]:-}"
    host="${details[1]:-unknown}"
    phase="${details[2]:-unknown}"
    rm -f "$state"

    if pending_install_recovery_menu "$host" "$phase"; then
        choice=0
    else
        choice=$?
    fi
    case "$choice" in
        0)
            pending_install_resume "$transaction_id" "$node"
            ;;
        1)
            pending_install_cleanup "$transaction_id"
            ;;
        3)
            if pending_install_abort "$transaction_id"; then
                show_result_screen "The pending installation was discarded." \
                    "Start a new installation after confirming that the VPS was cleaned manually."
            fi
            ;;
        2) return 0 ;;
        *) return 0 ;;
    esac
}

add_node() {
    local name host access_transport server_name port_mode vision_port xhttp_port dns_profile dns_lists bootstrap_user bootstrap_password bootstrap_port before after existing_node recovery_rc saved_bootstrap_user saved_bootstrap_port review_status probe_rc auth_action internet_action transaction_id
    local -a port_args=()
    while true; do
        clear_screen
        printf '%s\n' "Checking local Internet connection..."
        if ! local_internet_available; then
            if local_internet_failure_menu; then
                internet_action=0
            else
                internet_action=$?
            fi
            if ((internet_action == 0)); then
                continue
            fi
            return 0
        fi
        if ! add_node_ip_prompt; then
            return 0
        fi
        host="$ADD_NODE_HOST"
        unset ADD_NODE_HOST
        if ! add_node_user_prompt; then
            return 0
        fi
        bootstrap_user="$ADD_NODE_USER"
        unset ADD_NODE_USER
        if ! add_node_port_prompt; then
            return 0
        fi
        bootstrap_port="$ADD_NODE_PORT"
        unset ADD_NODE_PORT
        before="$(mktemp "$RUNTIME_TMP_DIR/.before.XXXXXX")"
        after="$(mktemp "$RUNTIME_TMP_DIR/.after.XXXXXX")"
        if ! read_vault_state "$before"; then
            rm -f "$before" "$after"
            return 1
        fi

        existing_node="$(find_node_by_connection "$before" "$host" "$bootstrap_port")"
        if [[ -n "$existing_node" ]]; then
        saved_bootstrap_user="$(python3 -c 'import json,sys; node=json.load(sys.stdin)["nodes"][sys.argv[1]]; print(node["bootstrap"].get("user", "root"), end="")' "$existing_node" <"$before")"
        saved_bootstrap_port="$(python3 -c 'import json,sys; node=json.load(sys.stdin)["nodes"][sys.argv[1]]; print(node["bootstrap"].get("port", 22), end="")' "$existing_node" <"$before")"
        if [[ "$bootstrap_user" != "$saved_bootstrap_user" || "$bootstrap_port" != "$saved_bootstrap_port" ]]; then
            clear_screen
            printf '%s\n' "A VPN server for this IP address and SSH port already exists in the Vault."
            printf '%s\n' "The saved server uses SSH user ${saved_bootstrap_user} on port ${saved_bootstrap_port}."
            printf '%s\n' "The entered user and port will be used to recover or redeploy this server."
            echo
            if retry_existing_node_with_bootstrap "$existing_node" "$before" "$host" "$bootstrap_user" "$bootstrap_port"; then
                rm -f "$before" "$after"
                show_result_screen "VPN server already exists in Vault. Bootstrap deployment completed idempotently."
            else
                rm -f "$before" "$after"
                show_result_screen "Deployment failed. The existing Vault was not changed."
            fi
            return 0
        fi
        if deploy_node "$existing_node" "$before"; then
            if vault_save "$before"; then
                rm -f "$before" "$after"
                show_result_screen "VPN server already exists in Vault. Deployment completed idempotently."
            else
                rm -f "$before" "$after"
                show_result_screen "The VPN server was deployed, but the encrypted Vault could not be updated."
            fi
        elif retry_existing_node_with_saved_key "$existing_node" "$before" "$bootstrap_port"; then
            rm -f "$before" "$after"
            show_result_screen "VPN server already exists in Vault. SSH access recovered on the bootstrap port."
        else
            recovery_rc=$?
            if ((recovery_rc == NO_SAVED_SSH_ACCESS)) && retry_existing_node_with_bootstrap "$existing_node" "$before" "$host" "$bootstrap_user" "$bootstrap_port" 1; then
                rm -f "$before" "$after"
                show_result_screen "VPN server already exists in Vault. Bootstrap deployment completed idempotently."
            else
                rm -f "$before" "$after"
                show_result_screen "Deployment failed. The existing Vault was not changed."
            fi
        fi
            return 0
        fi

        while true; do
            if ! add_node_password_prompt; then
                rm -f "$before" "$after"
                return 0
            fi
            bootstrap_password="$ADD_NODE_PASSWORD"
            unset ADD_NODE_PASSWORD
            if review_node_connection "$host" "$bootstrap_user" "$bootstrap_port"; then
                review_status=0
            else
                review_status=$?
            fi
            case "$review_status" in
                0) ;;
                1)
                    unset bootstrap_password
                    rm -f "$before" "$after"
                    continue 2
                    ;;
                *) rm -f "$before" "$after"; return 0 ;;
            esac

            pipeline_start "Checking VPS resources" preflight
            if probe_vps_resources "$host" "$bootstrap_user" "$bootstrap_port" "$bootstrap_password"; then
                pipeline_complete "VPS resources available"
                break 2
            else
                probe_rc=$?
                pipeline_abort
            fi
            if ((probe_rc == UNAVAILABLE_BOOTSTRAP_CONNECTION)); then
                if bootstrap_connection_failure_menu "$host" "$bootstrap_port"; then
                    auth_action=0
                else
                    auth_action=$?
                fi
                unset bootstrap_password
                rm -f "$before" "$after"
                if ((auth_action == 0)); then
                    continue 2
                fi
                return 0
            fi
            if ((probe_rc == BOOTSTRAP_PREFLIGHT_FAILED)); then
                if bootstrap_preflight_failure_menu; then
                    auth_action=0
                else
                    auth_action=$?
                fi
                unset bootstrap_password
                rm -f "$before" "$after"
                if ((auth_action == 0)); then
                    continue 2
                fi
                return 0
            fi
            if ((probe_rc != INVALID_BOOTSTRAP_CREDENTIALS)); then
                unset bootstrap_password
                rm -f "$before" "$after"
                return 1
            fi
            if bootstrap_auth_failure_menu; then
                continue
            else
                auth_action=$?
            fi
            unset bootstrap_password
            rm -f "$before" "$after"
            if ((auth_action == 1)); then
                continue 2
            fi
            return 0
        done
    done
    if ! add_node_access_transport_prompt; then
        unset bootstrap_password
        rm -f "$before" "$after"
        return 0
    fi
    access_transport="$ADD_NODE_ACCESS_TRANSPORT"
    unset ADD_NODE_ACCESS_TRANSPORT
    if [[ "$access_transport" == ssh-proxy ]]; then
        server_name=github.com
        port_mode=random
        dns_profile=disabled
        dns_lists=''
        port_args=()
    else
        if ! add_node_domain_prompt; then
            unset bootstrap_password
            rm -f "$before" "$after"
            return 0
        fi
        server_name="$ADD_NODE_SERVER_NAME"
        unset ADD_NODE_SERVER_NAME
        if ! add_node_port_mode_prompt; then
            unset bootstrap_password
            rm -f "$before" "$after"
            return 0
        fi
        port_mode="$ADD_NODE_PORT_MODE"
        vision_port="${ADD_NODE_VISION_PORT:-}"
        xhttp_port="${ADD_NODE_XHTTP_PORT:-}"
        unset ADD_NODE_PORT_MODE ADD_NODE_VISION_PORT ADD_NODE_XHTTP_PORT
        if [[ "$port_mode" == manual ]]; then
            port_args=(--vision-port "$vision_port" --xhttp-port "$xhttp_port")
        fi
        unset DNS_FILTER_CURRENT_PROFILE DNS_FILTER_LISTS
        if ! select_dns_profile initial; then
            unset bootstrap_password
            rm -f "$before" "$after"
            return 0
        fi
        dns_profile="$DNS_FILTER_PROFILE"
        dns_lists="${DNS_FILTER_LISTS:-}"
    fi

    name="auto"
    pipeline_start "Installing VPN server" install
    if ! NITKA_BOOTSTRAP_USER="$bootstrap_user" NITKA_BOOTSTRAP_PASSWORD="$bootstrap_password" NITKA_BOOTSTRAP_PORT="$bootstrap_port" python3 "$ROOT_DIR/scripts/state_cli.py" --access-transport "$access_transport" --server-name "$server_name" --port-mode "$port_mode" "${port_args[@]}" --dns-profile "$dns_profile" --dns-lists "$dns_lists" add-node "$name" "$host" <"$before" >"$after"; then
        pipeline_abort
        printf '%s\n' "The local deployment state could not be generated. The VPS was not changed." >&2
        if ((DEBUG_MODE)); then
            printf '%s\n' "Check the Python state CLI and its error output." >&2
        fi
        unset bootstrap_password
        rm -f "$before" "$after"
        show_result_screen "The local deployment state could not be generated. The VPS was not changed."
        return 1
    fi
    unset bootstrap_password
    if ! name="$(python3 - "$before" "$after" <<'PY'
import json
import sys

before = json.load(open(sys.argv[1], encoding="utf-8"))
after = json.load(open(sys.argv[2], encoding="utf-8"))
print(next(name for name in after["nodes"] if name not in before.get("nodes", {})))
PY
    )"; then
        pipeline_abort
        printf '%s\n' "The generated VPN state is invalid; the server was not added to the Vault." >&2
        if ((DEBUG_MODE)); then
            printf '%s\n' "Generated state file: %s" "$after" >&2
            cat "$after" >&2 || true
        fi
        rm -f "$before" "$after"
        return 1
    fi
    transaction_id="install-$(date -u +%Y%m%dT%H%M%SZ)-${BASHPID}"
    if ! pending_install_begin "$after" "$name" "$transaction_id"; then
        pipeline_abort
        rm -f "$before" "$after"
        show_result_screen "The installation could not be prepared in the encrypted Vault." \
            "The VPS was not changed."
        return 1
    fi
    PENDING_TRANSACTION_ID="$transaction_id"
    rm -f "$before"
    if ! deploy_node "$name" "$after" "" 1; then
        pipeline_abort
        rm -f "$after"
        recover_pending_installation || true
        PENDING_TRANSACTION_ID=""
        return 1
    fi
    pipeline_stage 95 'Saving encrypted Vault'
    pipeline_render
    if ! pending_install_commit "$transaction_id"; then
        pipeline_abort
        rm -f "$after"
        PENDING_TRANSACTION_ID=""
        show_result_screen \
            "The VPN server was deployed, but the encrypted Vault could not be saved." \
            "The server was not added to the local menu."
        return 1
    fi
    PENDING_TRANSACTION_ID=""
    if [[ "$access_transport" == ssh-proxy ]]; then
        pipeline_complete "SSH proxy deployed successfully."
        show_ssh_proxy_details "$name" "$after"
    else
        pipeline_complete "VPN server added successfully." 1
    fi
    rm -f "$after"
}

cascade_role_intro() {
    local role="$1" choice
    while true; do
        clear_screen
        menu_heading "Add VPN server"
        echo
        if [[ "$role" == egress ]]; then
            printf '%s\n' "This VPS will be used as the egress server."
            printf '%s\n' "It provides the remote exit, DNS, and SSH TUN server."
        else
            printf '%s\n' "This VPS will be used as the ingress server."
            printf '%s\n' "Clients connect here; whitelist traffic is sent to egress."
        fi
        printf '%s\n' "If you are not sure what this role means, press i for information."
        echo
        menu_option 1 Continue
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice choice '?: ' '1, or b, m, i, x'; then continue; fi
        case "$choice" in
            1) return 0 ;;
            b) return 1 ;;
            m) MAIN_MENU_REQUESTED=1; return 1 ;;
            i) show_info general ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done
}

collect_cascade_node_access() {
    local role="$1" host user port password review_status probe_rc auth_action
    while true; do
        if ! cascade_role_intro "$role"; then return 1; fi
        if ! add_node_ip_prompt; then return 1; fi
        host="$ADD_NODE_HOST"
        unset ADD_NODE_HOST
        if ! add_node_user_prompt; then return 1; fi
        user="$ADD_NODE_USER"
        unset ADD_NODE_USER
        if ! add_node_port_prompt; then return 1; fi
        port="$ADD_NODE_PORT"
        unset ADD_NODE_PORT
        if ! add_node_password_prompt; then return 1; fi
        password="$ADD_NODE_PASSWORD"
        unset ADD_NODE_PASSWORD
        if review_node_connection "$host" "$user" "$port"; then
            review_status=0
        else
            review_status=$?
        fi
        case "$review_status" in
            0) ;;
            1) unset password; continue ;;
            *) unset password; return 1 ;;
        esac

        pipeline_start "Checking VPS resources" preflight
        if probe_vps_resources "$host" "$user" "$port" "$password"; then
            pipeline_complete "VPS resources available"
            CASCADE_NODE_HOST="$host"
            CASCADE_NODE_USER="$user"
            CASCADE_NODE_PORT="$port"
            CASCADE_NODE_PASSWORD="$password"
            return 0
        fi
        probe_rc=$?
        pipeline_abort
        if ((probe_rc == INVALID_BOOTSTRAP_CREDENTIALS)); then
            if bootstrap_auth_failure_menu; then
                unset password
                continue
            fi
            auth_action=$?
            unset password
            ((auth_action == 1)) && continue
        fi
        unset password
        return 1
    done
}

cascade_next_deployment_id() {
    local state="$1"
    python3 - "$state" <<'PY'
import json
import sys

deployments = json.load(open(sys.argv[1], encoding="utf-8")).get("deployments", {})
index = 1
while f"cascade-{index}" in deployments:
    index += 1
print(f"cascade-{index}")
PY
}

cascade_new_node_name() {
    local before="$1" after="$2"
    python3 - "$before" "$after" <<'PY'
import json
import sys

before = json.load(open(sys.argv[1], encoding="utf-8"))
after = json.load(open(sys.argv[2], encoding="utf-8"))
print(next(name for name in after["nodes"] if name not in before.get("nodes", {})))
PY
}

add_cascade() {
    local before egress_state ingress_state shared_state cascade_state deployment_id
    local egress_node ingress_node egress_host ingress_host egress_user egress_port ingress_user ingress_port choice
    local server_name port_mode vision_port xhttp_port dns_profile dns_lists
    local -a port_args=()
    before="$(mktemp "$RUNTIME_TMP_DIR/.cascade-before.XXXXXX")"
    egress_state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-egress.XXXXXX")"
    ingress_state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-ingress.XXXXXX")"
    shared_state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-shared.XXXXXX")"
    cascade_state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-state.XXXXXX")"
    if ! local_internet_available; then
        show_result_screen "The computer running Nitka cannot reach the Internet."
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 0
    fi
    if ! read_vault_state "$before"; then
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 1
    fi

    if ! collect_cascade_node_access egress; then
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 0
    fi
    egress_host="$CASCADE_NODE_HOST"
    egress_user="$CASCADE_NODE_USER"
    egress_port="$CASCADE_NODE_PORT"
    if [[ -n "$(find_node_by_connection "$before" "$egress_host" "$CASCADE_NODE_PORT")" ]]; then
        show_result_screen "The egress VPS already exists in the Vault. Select it from VPN servers instead."
        unset CASCADE_NODE_HOST CASCADE_NODE_USER CASCADE_NODE_PORT CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 0
    fi
    if ! NITKA_BOOTSTRAP_USER="$CASCADE_NODE_USER" NITKA_BOOTSTRAP_PASSWORD="$CASCADE_NODE_PASSWORD" NITKA_BOOTSTRAP_PORT="$CASCADE_NODE_PORT" \
        python3 "$ROOT_DIR/scripts/state_cli.py" --node-role egress --server-name github.com --port-mode random --dns-profile disabled \
        add-node auto "$egress_host" <"$before" >"$egress_state"; then
        unset CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 1
    fi
    egress_node="$(cascade_new_node_name "$before" "$egress_state")"
    unset CASCADE_NODE_PASSWORD

    if ! collect_cascade_node_access ingress; then
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 0
    fi
    ingress_host="$CASCADE_NODE_HOST"
    ingress_user="$CASCADE_NODE_USER"
    ingress_port="$CASCADE_NODE_PORT"
    if [[ -n "$(find_node_by_connection "$egress_state" "$ingress_host" "$CASCADE_NODE_PORT")" ]]; then
        show_result_screen "The ingress VPS already exists in the Vault. Select it from VPN servers instead."
        unset CASCADE_NODE_HOST CASCADE_NODE_USER CASCADE_NODE_PORT CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 0
    fi

    if ! add_node_domain_prompt; then
        unset CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 0
    fi
    server_name="$ADD_NODE_SERVER_NAME"
    unset ADD_NODE_SERVER_NAME
    if ! add_node_port_mode_prompt; then
        unset CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 0
    fi
    port_mode="$ADD_NODE_PORT_MODE"
    vision_port="${ADD_NODE_VISION_PORT:-}"
    xhttp_port="${ADD_NODE_XHTTP_PORT:-}"
    unset ADD_NODE_PORT_MODE ADD_NODE_VISION_PORT ADD_NODE_XHTTP_PORT
    if [[ "$port_mode" == manual ]]; then
        port_args=(--vision-port "$vision_port" --xhttp-port "$xhttp_port")
    fi
    unset DNS_FILTER_CURRENT_PROFILE DNS_FILTER_LISTS
    if ! select_dns_profile initial; then
        unset CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 0
    fi
    dns_profile="$DNS_FILTER_PROFILE"
    dns_lists="${DNS_FILTER_LISTS:-}"
    if ! NITKA_BOOTSTRAP_USER="$CASCADE_NODE_USER" NITKA_BOOTSTRAP_PASSWORD="$CASCADE_NODE_PASSWORD" NITKA_BOOTSTRAP_PORT="$CASCADE_NODE_PORT" \
        python3 "$ROOT_DIR/scripts/state_cli.py" --node-role ingress --server-name "$server_name" --port-mode "$port_mode" \
        "${port_args[@]}" --dns-profile "$dns_profile" --dns-lists "$dns_lists" add-node auto "$ingress_host" <"$egress_state" >"$ingress_state"; then
        unset CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 1
    fi
    ingress_node="$(cascade_new_node_name "$egress_state" "$ingress_state")"
    unset CASCADE_NODE_PASSWORD

    deployment_id="$(cascade_next_deployment_id "$ingress_state")"
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" share-management-key "$ingress_node" "$egress_node" <"$ingress_state" >"$shared_state"; then
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 1
    fi
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" add-cascade "$deployment_id" "$ingress_node" "$egress_node" <"$shared_state" >"$cascade_state"; then
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        return 1
    fi

    while true; do
        clear_screen
        menu_heading "Review Cascade VPN"
        echo
        ui_print_table "  " "   " 0 \
            $'ROLE\tIP\tSSH USER\tPORT\tACCESS' \
            $'egress\t'"$egress_host"$'\t'"$egress_user"$'\t'"$egress_port"$'\tshared deploy key' \
            $'ingress\t'"$ingress_host"$'\t'"$ingress_user"$'\t'"$ingress_port"$'\tshared deploy key'
        echo
        ui_print_table "  " "   " 0 \
            $'FIELD\tVALUE' \
            $'Deployment\t'"$deployment_id" \
            $'Management\tdeploy + shared SSH key' \
            $'Route\tclient → ingress → egress → Internet'
        echo
        menu_option 1 Continue
        menu_option 2 Cancel
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice choice '?: ' '1 or 2, or b, m, i, x'; then continue; fi
        case "$choice" in
            1) break ;;
            2|b) rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"; return 0 ;;
            m) MAIN_MENU_REQUESTED=1; rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"; return 0 ;;
            i) show_info general ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done

    pipeline_start "Installing Cascade VPN" install
    if ! deploy_node "$egress_node" "$cascade_state" "" 1 bootstrap-only cascade-egress; then
        pipeline_abort
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        show_result_screen "Cascade egress bootstrap failed. The Vault was not changed."
        return 1
    fi
    if ! deploy_node "$ingress_node" "$cascade_state" "" 1 bootstrap-only cascade-ingress; then
        pipeline_abort
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        show_result_screen "Cascade ingress bootstrap failed. The Vault was not changed."
        return 1
    fi
    if ! run_cascade_playbooks "$deployment_id" "$cascade_state"; then
        pipeline_abort
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        show_result_screen "Cascade services failed to deploy. The Vault was not changed."
        return 1
    fi
    if ! deploy_node "$egress_node" "$cascade_state" "" 0 hardening-only cascade-egress; then
        pipeline_abort
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        show_result_screen "Cascade egress SSH hardening failed. The Vault was not changed."
        return 1
    fi
    if ! deploy_node "$ingress_node" "$cascade_state" "" 0 hardening-only cascade-ingress; then
        pipeline_abort
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        show_result_screen "Cascade ingress SSH hardening failed. The Vault was not changed."
        return 1
    fi
    if ! vault_save "$cascade_state"; then
        pipeline_abort
        rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
        show_result_screen "Cascade was deployed, but the encrypted Vault could not be saved."
        return 1
    fi
    rm -f "$before" "$egress_state" "$ingress_state" "$shared_state" "$cascade_state"
    pipeline_complete "Cascade VPN added successfully." 1
}

capture_ssh_proxy_host_fingerprint() {
    local node="$1" state_file="$2" host ssh_transport_port scan_attempt
    local public_key fingerprint key_file updated_state
    local access_transport

    access_transport="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["access"].get("transport", ""))' "$node" <"$state_file")"
    [[ "$access_transport" == ssh-proxy ]] || return 0
    host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["host"])' "$node" <"$state_file")"
    ssh_transport_port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["access"]["ssh_proxy"]["port"])' "$node" <"$state_file")"

    public_key=""
    for scan_attempt in {1..12}; do
        public_key="$(scan_ssh_ed25519_host_key "$host" "$ssh_transport_port")"
        [[ -n "$public_key" ]] && break
        ((scan_attempt < 12)) && sleep 2
    done
    if [[ -z "$public_key" ]]; then
        printf '%s\n' "SSH proxy started, but its host key could not be verified."
        return 1
    fi

    key_file="$(mktemp "$RUNTIME_TMP_DIR/.ssh-proxy-host-key.XXXXXX")"
    printf '%s\n' "$public_key" >"$key_file"
    fingerprint="$(ssh-keygen -lf "$key_file" -E sha256 2>/dev/null | sed -nE 's/^[^[:space:]]+[[:space:]]+(SHA256:[^[:space:]]+).*/\1/p')"
    if [[ -z "$fingerprint" ]]; then
        rm -f "$key_file"
        return 1
    fi

    updated_state="$(mktemp "$RUNTIME_TMP_DIR/.ssh-proxy-state.XXXXXX")"
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" set-ssh-transport-host-key \
        "$node" "$key_file" "$fingerprint" <"$state_file" >"$updated_state"; then
        rm -f "$key_file" "$updated_state"
        return 1
    fi
    mv -f "$updated_state" "$state_file"
    rm -f "$key_file"
}

deploy_node() {
    local node="$1" state_file="${2:-}" connect_port="${3:-}" bootstrap_mode="${4:-0}" deployment_mode="${5:-xray}"
    local inventory_alias="${6:-$node}"
    local before extra inventory inventory_dir key_file known_hosts_file host_key_file user host port target_port management_port bootstrap bootstrap_password bootstrap_user host_public_key ssh_common_args rc marked hardened_state mapped_state ssh_host_public_key ssh_host_fingerprint actual_fingerprint target_host_public_key management_host_public_key scan_attempt repaired_state
    before="$(mktemp)"
    extra="$(mktemp)"
    inventory_dir="$(mktemp -d "$RUNTIME_TMP_DIR/nitka-inventory.XXXXXX")"
    inventory="$inventory_dir/hosts.yml"
    known_hosts_file="$inventory_dir/known_hosts"
    host_key_file="$inventory_dir/ssh_host_ed25519_key.pub"
    if [[ -n "$state_file" ]]; then
        cp "$state_file" "$before"
    else
        read_vault_state "$before" || { rm -f "$before" "$extra"; rm -rf "$inventory_dir"; return 1; }
    fi
    repaired_state="$before.repaired"
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" repair-access-port "$node" <"$before" >"$repaired_state"; then
        rm -f "$before" "$extra" "$repaired_state"
        rm -rf "$inventory_dir"
        return 1
    fi
    mv -f "$repaired_state" "$before"
    python3 "$ROOT_DIR/scripts/state_cli.py" extract "$node" <"$before" >"$extra"
    host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["host"])' "$node" <"$before")"
    target_port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["sshd_port"])' "$node" <"$before")"
    management_port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["port"])' "$node" <"$before")"
    port="${connect_port:-${management_port:-$target_port}}"
    key_file="$(mktemp)"
    bootstrap="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap"].get("private_key", ""), end="")' "$node" <"$before")"
    bootstrap_password="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap"].get("password", ""), end="")' "$node" <"$before")"
    if [[ "$bootstrap_mode" != 1 ]]; then
        bootstrap_password=""
    fi
    bootstrap_user="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap"]["user"], end="")' "$node" <"$before")"
    host_public_key="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"].get("host_public_key", ""), end="")' "$node" <"$before")"
    if [[ -n "$bootstrap_password" ]]; then
        user="$bootstrap_user"
        port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap"].get("port", 22))' "$node" <"$before")"
        bootstrap_password="$(yaml_scalar "$bootstrap_password")"
        printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $inventory_alias:" "          ansible_host: $host" "          ansible_user: $user" "          ansible_port: $port" "          ansible_password: $bootstrap_password" "          ansible_become_password: $bootstrap_password" "          ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o PubkeyAuthentication=no -o PreferredAuthentications=password'" >"$inventory"
    elif [[ -n "$bootstrap" ]]; then
        user="$bootstrap_user"
        printf '%s' "$bootstrap" >"$key_file"
        if [[ -n "$host_public_key" ]]; then
            if ! write_node_known_hosts "$before" "$node" "$known_hosts_file" "$port"; then
                rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
                return 1
            fi
            ssh_common_args="-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts_file -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes"
        else
            ssh_common_args="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes"
        fi
        printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $inventory_alias:" "          ansible_host: $host" "          ansible_user: $user" "          ansible_port: $port" "          ansible_ssh_private_key_file: $key_file" "          ansible_ssh_common_args: '$ssh_common_args'" >"$inventory"
    else
        user=deploy
        python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["private_key"], end="")' "$node" <"$before" >"$key_file"
        if [[ -n "$host_public_key" ]]; then
            if ! write_node_known_hosts "$before" "$node" "$known_hosts_file" "$port"; then
                rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
                return 1
            fi
            ssh_common_args="-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts_file -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes"
        else
            ssh_common_args="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes"
        fi
        printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $inventory_alias:" "          ansible_host: $host" "          ansible_user: $user" "          ansible_port: $port" "          ansible_ssh_private_key_file: $key_file" "          ansible_ssh_common_args: '$ssh_common_args'" >"$inventory"
    fi
    chmod 600 "$key_file"
    if [[ "$deployment_mode" != hardening-only ]]; then
        if [[ -n "$bootstrap_password" ]]; then
            if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/bootstrap.yml"; then
                :
            else
                rc=$?
                rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
                return "$rc"
            fi
        else
            if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/bootstrap.yml" --private-key "$key_file"; then
                :
            else
                rc=$?
                rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
                return "$rc"
            fi
        fi
    fi
    if ! pending_install_sync "$before" "$PENDING_TRANSACTION_ID" "bootstrap-complete"; then
        rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
        return 1
    fi
    if [[ -n "$bootstrap_password" || -n "$bootstrap" ]]; then
        python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["private_key"], end="")' "$node" <"$before" >"$key_file"
        chmod 600 "$key_file"
    fi

    if [[ -n "$host_public_key" ]]; then
        if ! write_node_known_hosts "$before" "$node" "$known_hosts_file" "$port"; then
            rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
            return 1
        fi
        ssh_common_args="-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts_file -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes"
    else
        ssh_common_args="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes"
    fi
    printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $inventory_alias:" "          ansible_host: $host" "          ansible_user: deploy" "          ansible_port: $port" "          ansible_ssh_private_key_file: $key_file" "          ansible_ssh_common_args: '$ssh_common_args'" >"$inventory"

    if [[ "$deployment_mode" == bootstrap-only ]]; then
        rm -f "$before" "$extra" "$key_file"
        rm -rf "$inventory_dir"
        return 0
    fi

    if [[ "$deployment_mode" != hardening-only ]]; then
        if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/deploy_standalone.yml" --private-key "$key_file"; then
            :
        else
            rc=$?
            rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
            return "$rc"
        fi
        if ! pending_install_sync "$before" "$PENDING_TRANSACTION_ID" "service-verified"; then
            rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
            return 1
        fi

        if ! capture_ssh_proxy_host_fingerprint "$node" "$before"; then
            rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
            return 1
        fi
        if [[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["access"].get("transport", ""))' "$node" <"$before")" == ssh-proxy ]]; then
            if ! pending_install_sync "$before" "$PENDING_TRANSACTION_ID" "ssh-proxy-verified"; then
                rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
                return 1
            fi
        fi
    fi

    if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/harden_ssh.yml" --private-key "$key_file"; then
        :
    else
        rc=$?
        rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
        return "$rc"
    fi

    # During the transition, the old and new listeners can briefly expose
    # different host keys. Scan each port independently; never reuse the
    # fallback port's key for the new management port.
    target_host_public_key=""
    management_host_public_key=""
    for scan_attempt in {1..12}; do
        target_host_public_key="$(scan_ssh_ed25519_host_key "$host" "$target_port")"
        if [[ "$management_port" == "$target_port" ]]; then
            management_host_public_key="$target_host_public_key"
        else
            management_host_public_key="$(scan_ssh_ed25519_host_key "$host" "$management_port")"
        fi
        [[ -n "$target_host_public_key" ]] && break
        ((scan_attempt < 12)) && sleep 2
    done
    ssh_host_public_key="$target_host_public_key"
    ssh_host_fingerprint=""
    if [[ -n "$ssh_host_public_key" ]]; then
        printf '%s\n' "$ssh_host_public_key" >"$host_key_file"
        ssh_host_fingerprint="$(ssh-keygen -lf "$host_key_file" -E sha256 2>/dev/null | sed -nE 's/^[^[:space:]]+[[:space:]]+(SHA256:[^[:space:]]+).*/\1/p')"
    fi
    if [[ -z "$ssh_host_public_key" || -z "$ssh_host_fingerprint" ]]; then
        printf '%s\n' "SSH hardening completed, but the VPS host key could not be verified."
        rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
        return 1
    fi
    actual_fingerprint="$(ssh-keygen -lf "$host_key_file" -E sha256 2>/dev/null | sed -nE 's/^[^[:space:]]+[[:space:]]+(SHA256:[^[:space:]]+).*/\1/p')"
    if [[ "$actual_fingerprint" != "$ssh_host_fingerprint" ]]; then
        printf '%s\n' "The returned SSH host key fingerprint is invalid."
        rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"
        return 1
    fi
    hardened_state="$(mktemp)"
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" set-ssh-host-key "$node" "$host_key_file" "$actual_fingerprint" <"$before" >"$hardened_state"; then
        rm -f "$before" "$extra" "$key_file" "$hardened_state"; rm -rf "$inventory_dir"
        return 1
    fi
    mv -f "$hardened_state" "$before"
    : >"$known_hosts_file"
    if [[ "$target_port" == 22 ]]; then
        printf '%s %s\n' "$host" "$target_host_public_key" >>"$known_hosts_file"
    else
        printf '[%s]:%s %s\n' "$host" "$target_port" "$target_host_public_key" >>"$known_hosts_file"
    fi
    if [[ "$management_port" != "$target_port" && -n "$management_host_public_key" ]]; then
        if [[ "$management_port" == 22 ]]; then
            printf '%s %s\n' "$host" "$management_host_public_key" >>"$known_hosts_file"
        else
            printf '[%s]:%s %s\n' "$host" "$management_port" "$management_host_public_key" >>"$known_hosts_file"
        fi
    fi
    chmod 600 "$known_hosts_file"

    if [[ "$PIPELINE_OPERATION" == install ]]; then
        pipeline_stage 55 'Verifying external SSH access'
    else
        pipeline_stage 70 'Verifying hardened SSH access'
    fi
    pipeline_render
    local verify_attempt verified=0 verified_port=""
    for verify_attempt in {1..12}; do
        if ssh -i "$key_file" -p "$target_port" \
            -o IdentitiesOnly=yes \
            -o BatchMode=yes \
            -o ConnectTimeout=8 \
            -o StrictHostKeyChecking=yes \
            -o UserKnownHostsFile="$known_hosts_file" \
            -o LogLevel=ERROR \
            deploy@"$host" true; then
            verified=1
            verified_port="$target_port"
            break
        fi
        if ((verify_attempt < 12)); then
            sleep 5
        fi
    done
    if ((verified == 0)); then
        printf '%s\n' "Deployment completed, but no external SSH port could be verified."
        rm -f "$before" "$extra" "$key_file" "$host_key_file" "$known_hosts_file"
        rm -rf "$inventory_dir"
        return 1
    fi
    mapped_state="$(mktemp "$RUNTIME_TMP_DIR/.mapped.XXXXXX")"
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" set-ssh-mapping "$node" "$verified_port" "$target_port" <"$before" >"$mapped_state"; then
        rm -f "$before" "$extra" "$key_file" "$host_key_file" "$known_hosts_file" "$mapped_state"
        rm -rf "$inventory_dir"
        return 1
    fi
    mv -f "$mapped_state" "$before"

    printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $inventory_alias:" "          ansible_host: $host" "          ansible_user: deploy" "          ansible_port: $verified_port" "          ansible_ssh_private_key_file: $key_file" "          ansible_ssh_common_args: '$ssh_common_args'" >"$inventory"
    if ! run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/finalize_ssh.yml" --private-key "$key_file"; then
        rm -f "$before" "$extra" "$key_file" "$host_key_file" "$known_hosts_file"
        rm -rf "$inventory_dir"
        return 1
    fi

    if ! pending_install_sync "$before" "$PENDING_TRANSACTION_ID" "ssh-access-verified"; then
        rm -f "$before" "$extra" "$key_file" "$host_key_file" "$known_hosts_file"
        rm -rf "$inventory_dir"
        return 1
    fi

    if [[ -n "$state_file" || "$user" == root || -n "$bootstrap" || -n "$bootstrap_password" ]]; then
        marked="$(mktemp "$RUNTIME_TMP_DIR/.marked.XXXXXX")"
        if ! python3 "$ROOT_DIR/scripts/state_cli.py" mark-deployed "$node" <"$before" >"$marked"; then
            rm -f "$before" "$extra" "$key_file" "$marked"; rm -rf "$inventory_dir"
            return 1
        fi
        mv -f "$marked" "$before"
        if [[ -n "$state_file" ]]; then
            cp "$before" "$state_file"
        else
            if ! vault_save "$before"; then
                rm -f "$before" "$extra" "$key_file" "$host_key_file" "$known_hosts_file"; rm -rf "$inventory_dir"
                return 1
            fi
        fi
        if ! pending_install_sync "$before" "$PENDING_TRANSACTION_ID" "ready-to-commit"; then
            rm -f "$before" "$extra" "$key_file" "$host_key_file" "$known_hosts_file"
            rm -rf "$inventory_dir"
            return 1
        fi
    fi
    rm -f "$before" "$extra" "$key_file" "$host_key_file" "$known_hosts_file"
    rm -rf "$inventory_dir"
}

cascade_deployment_for_node() {
    local node="$1" state_file="$2"
    python3 - "$node" "$state_file" <<'PY'
import json
import sys

state = json.load(open(sys.argv[2], encoding="utf-8"))
for deployment_id, deployment in state.get("deployments", {}).items():
    ingress = deployment.get("roles", {}).get("ingress", {})
    if ingress.get("node") == sys.argv[1]:
        print(deployment_id)
        break
PY
}

run_cascade_ingress_playbook() {
    local deployment_id="$1" state_file="${2:-}" operation_title="${3:-Updating Cascade ingress}" operation="${4:-}"
    local before extra inventory inventory_dir key_file known_hosts_file ingress_node host user port management_sshd_port bootstrap_port selected_port rc pipeline_owned=0
    local inventory_alias="cascade-ingress"
    local -a ansible_extra_args
    before="$(mktemp)"
    extra="$(mktemp)"
    inventory_dir="$(mktemp -d /tmp/nitka-cascade-ingress-inventory.XXXXXX)"
    inventory="$inventory_dir/hosts.yml"
    key_file="$inventory_dir/id_ed25519"
    known_hosts_file="$inventory_dir/known_hosts"

    if [[ -n "$state_file" ]]; then
        cp "$state_file" "$before"
    elif ! read_vault_state "$before"; then
        rm -f "$before" "$extra"
        rm -rf "$inventory_dir"
        return 1
    fi
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" \
        --cascade-local-root "$STATE_DIR/cascade" \
        extract-cascade "$deployment_id" <"$before" >"$extra"; then
        rm -f "$before" "$extra"
        rm -rf "$inventory_dir"
        return 1
    fi
    ansible_extra_args=(-e "@$extra")
    ingress_node="$(python3 - "$deployment_id" "$before" <<'PY'
import json
import sys

state = json.load(open(sys.argv[2], encoding="utf-8"))
print(state["deployments"][sys.argv[1]]["roles"]["ingress"]["node"])
PY
)"
    host="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["host"])
PY
)"
    user="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["user"])
PY
)"
    port="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["port"])
PY
)"
    management_sshd_port="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"].get("sshd_port", ""))
PY
)"
    bootstrap_port="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["bootstrap"].get("port", ""))
PY
)"
    python3 - "$ingress_node" "$before" >"$key_file" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["private_key"], end="")
PY
    chmod 600 "$key_file"

    # A provider NAT rule or an interrupted SSH transition can leave the
    # saved external port stale while the recorded daemon port is reachable.
    # Probe only saved candidates, always with the pinned host key and the
    # management key.  Do this before Ansible so a bad endpoint is reported as
    # an SSH access failure instead of an opaque Gathering Facts error.
    for selected_port in "$port" "$management_sshd_port" "$bootstrap_port"; do
        [[ -n "$selected_port" ]] || continue
        if ! write_node_known_hosts "$before" "$ingress_node" "$known_hosts_file" "$selected_port"; then
            continue
        fi
        if probe_ssh_endpoint "$host" "$user" "$selected_port" "$key_file" "$known_hosts_file"; then
            port="$selected_port"
            break
        fi
        port=""
    done
    if [[ -z "$port" ]]; then
        printf '%s\n' "Cascade ingress management SSH is unavailable on the saved management, daemon, or bootstrap port." >&2
        rm -f "$before" "$extra" "$key_file"
        rm -rf "$inventory_dir"
        return 1
    fi
    printf '%s\n' \
        "---" \
        "all:" \
        "  children:" \
        "    cascade_ingress:" \
        "      hosts:" \
        "        $inventory_alias:" \
        "          ansible_host: $host" \
        "          ansible_user: $user" \
        "          ansible_port: $port" \
        "          ansible_ssh_private_key_file: $key_file" \
        "          ansible_ssh_common_args: '-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts_file -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes'" \
        >"$inventory"
    chmod 600 "$inventory"
    if ((DEBUG_MODE == 0 && PIPELINE_ACTIVE == 0)); then
        pipeline_start "$operation_title" "$operation"
        pipeline_owned=1
    fi
    if run_ansible_playbook -i "$inventory" "${ansible_extra_args[@]}" "$ROOT_DIR/ansible/playbooks/deploy_cascade_ingress.yml" --private-key "$key_file"; then
        rc=0
    else
        rc=$?
    fi
    rm -f "$before" "$extra" "$key_file"
    rm -rf "$inventory_dir"
    if ((pipeline_owned)) && ((rc != 0)); then
        pipeline_abort
    fi
    if ((rc == 0)) && [[ -n "$state_file" ]]; then
        local normalized
        normalized="$(mktemp)"
        if ! python3 "$ROOT_DIR/scripts/state_cli.py" set-management-user "$ingress_node" deploy <"$state_file" >"$normalized"; then
            rm -f "$normalized"
            return 1
        fi
        mv -f "$normalized" "$state_file"
    fi
    return "$rc"
}

run_node_playbook() {
    local node="$1" playbook="$2" state_file="${3:-}" operation_title="${4:-Updating VPN server}" operation="${5:-}"
    local inventory_alias="${6:-$node}" operation_override="${7:-}"
    local before extra inventory inventory_dir key_file known_hosts_file host user port playbook_path rc pipeline_owned=0
    local -a operation_args=()
    before="$(mktemp)"
    extra="$(mktemp)"
    inventory_dir="$(mktemp -d /tmp/xray-inventory.XXXXXX)"
    inventory="$inventory_dir/hosts.yml"
    key_file="$(mktemp)"
    known_hosts_file="$inventory_dir/known_hosts"
    if [[ -n "$state_file" ]]; then
        cp "$state_file" "$before"
    else
        read_vault_state "$before" || { rm -f "$before" "$extra" "$key_file"; rm -rf "$inventory_dir"; return 1; }
    fi
    python3 "$ROOT_DIR/scripts/state_cli.py" extract "$node" <"$before" >"$extra"
    playbook_path="$ROOT_DIR/ansible/$playbook"
    if [[ ! -f "$playbook_path" ]]; then
        playbook_path="$ROOT_DIR/ansible/playbooks/$playbook"
    fi
    if [[ ! -f "$playbook_path" ]]; then
        printf '%s\n' "Ansible playbook not found: $playbook" >&2
        rm -f "$before" "$extra" "$key_file"
        rm -rf "$inventory_dir"
        return 1
    fi
    if [[ -n "$operation_override" ]]; then
        operation_args=(-e "access_transport_operation=$operation_override")
    fi
    if ! write_node_known_hosts "$before" "$node" "$known_hosts_file"; then
        printf '%s\n' "The SSH host key is not pinned for this VPN server. Redeploy it before changing settings."
        rm -f "$before" "$extra" "$key_file"
        rm -rf "$inventory_dir"
        return 1
    fi
    host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["host"])' "$node" <"$before")"
    user="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["user"])' "$node" <"$before")"
    port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["port"])' "$node" <"$before")"
    python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["private_key"], end="")' "$node" <"$before" >"$key_file"
    printf '%s\n' "---" "all:" "  children:" "    xray_nodes:" "      hosts:" "        $inventory_alias:" "          ansible_host: $host" "          ansible_user: $user" "          ansible_port: $port" "          ansible_ssh_common_args: '-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts_file -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes'" >"$inventory"
    chmod 600 "$key_file"
    if ((DEBUG_MODE == 0 && PIPELINE_ACTIVE == 0)); then
        pipeline_start "$operation_title" "$operation"
        pipeline_owned=1
    fi
    if run_ansible_playbook -i "$inventory" -e "@$extra" "${operation_args[@]}" "$playbook_path" --private-key "$key_file"; then
        rc=0
    else
        rc=$?
    fi
    if ((rc == 0)) && [[ -n "$state_file" ]] && ! capture_ssh_proxy_host_fingerprint "$node" "$state_file"; then
        rc=1
    fi
    rm -f "$before" "$extra" "$key_file"
    rm -rf "$inventory_dir"
    if ((pipeline_owned)); then
        ((rc != 0)) && pipeline_abort
    fi
    if ((rc == 0)) && [[ -n "$state_file" ]]; then
        local normalized
        normalized="$(mktemp)"
        if ! python3 "$ROOT_DIR/scripts/state_cli.py" set-management-user "$node" deploy <"$state_file" >"$normalized"; then
            rm -f "$normalized"
            return 1
        fi
        mv -f "$normalized" "$state_file"
    fi
    return "$rc"
}

run_cascade_playbooks() {
    local deployment_id="$1" state_file="${2:-}" mode="${3:-full}" before extra inventory inventory_dir
    local known_hosts_file ingress_known_hosts egress_known_hosts ingress_key egress_key
    local ingress_node egress_node ingress_host egress_host ingress_port egress_port
    local ingress_user egress_user ingress_alias egress_alias node normalized rc policy_present
    before="$(mktemp)"
    extra="$(mktemp)"
    inventory_dir="$(mktemp -d /tmp/xray-cascade-inventory.XXXXXX)"
    inventory="$inventory_dir/hosts.yml"
    known_hosts_file="$inventory_dir/known_hosts"
    ingress_known_hosts="$inventory_dir/ingress_known_hosts"
    egress_known_hosts="$inventory_dir/egress_known_hosts"
    ingress_key="$inventory_dir/ingress_key"
    egress_key="$inventory_dir/egress_key"

    if [[ -n "$state_file" ]]; then
        cp "$state_file" "$before"
    else
        read_vault_state "$before" || {
            rm -f "$before" "$extra"
            rm -rf "$inventory_dir"
            return 1
        }
    fi
    normalized="$(mktemp)"
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" \
        normalize-cascade-transport "$deployment_id" \
        <"$before" >"$normalized"; then
        rm -f "$before" "$extra" "$normalized"
        rm -rf "$inventory_dir"
        return 1
    fi
    mv -f "$normalized" "$before"
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" \
        --cascade-local-root "$STATE_DIR/cascade" \
        extract-cascade "$deployment_id" <"$before" >"$extra"; then
        rm -f "$before" "$extra"
        rm -rf "$inventory_dir"
        return 1
    fi

    ingress_node="$(python3 - "$deployment_id" "$before" <<'PY'
import json
import sys

state = json.load(open(sys.argv[2], encoding="utf-8"))
deployment = state["deployments"][sys.argv[1]]
print(deployment["roles"]["ingress"]["node"])
PY
)"
    egress_node="$(python3 - "$deployment_id" "$before" <<'PY'
import json
import sys

state = json.load(open(sys.argv[2], encoding="utf-8"))
deployment = state["deployments"][sys.argv[1]]
print(deployment["roles"]["egress"]["node"])
PY
)"
    ingress_alias="cascade-ingress"
    egress_alias="cascade-egress"
    policy_present="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

state = json.load(open(sys.argv[2], encoding="utf-8"))
xray = state["nodes"][sys.argv[1]]["access"]["xray_reality"]
policy = xray.get("routing_policy") if isinstance(xray, dict) else None
print("1" if isinstance(policy, dict) and policy.get("effective") is not None else "0")
PY
)"
    if [[ "$mode" != management && "$policy_present" != 1 ]]; then
        printf '%s\n' "Routing policy is missing from the encrypted Vault." >&2
        printf '%s\n' "Import the routing policy before deploying this Cascade." >&2
        rm -f "$before" "$extra"
        rm -rf "$inventory_dir"
        return 1
    fi
    ingress_host="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["host"])
PY
)"
    egress_host="$(python3 - "$egress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["host"])
PY
)"
    ingress_port="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["port"])
PY
)"
    egress_port="$(python3 - "$egress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["port"])
PY
)"
    ingress_user="$(python3 - "$ingress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["user"])
PY
)"
    egress_user="$(python3 - "$egress_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["user"])
PY
)"
    python3 - "$ingress_node" "$before" >"$ingress_key" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["private_key"], end="")
PY
    python3 - "$egress_node" "$before" >"$egress_key" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["management"]["private_key"], end="")
PY
    chmod 600 "$ingress_key" "$egress_key"
    if ! write_node_known_hosts "$before" "$ingress_node" "$ingress_known_hosts"; then
        rm -f "$before" "$extra" "$ingress_key" "$egress_key"
        rm -rf "$inventory_dir"
        return 1
    fi
    if ! write_node_known_hosts "$before" "$egress_node" "$egress_known_hosts"; then
        rm -f "$before" "$extra" "$ingress_key" "$egress_key"
        rm -rf "$inventory_dir"
        return 1
    fi
    cat "$ingress_known_hosts" "$egress_known_hosts" >"$known_hosts_file"
    chmod 600 "$known_hosts_file"

    printf '%s\n' \
        "---" \
        "all:" \
        "  children:" \
        "    cascade_ingress:" \
        "      hosts:" \
        "        $ingress_alias:" \
        "          ansible_host: $ingress_host" \
        "          ansible_user: $ingress_user" \
        "          ansible_port: $ingress_port" \
        "          ansible_ssh_private_key_file: $ingress_key" \
        "          ansible_ssh_common_args: '-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts_file -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes'" \
        "    cascade_egress:" \
        "      hosts:" \
        "        $egress_alias:" \
        "          ansible_host: $egress_host" \
        "          ansible_user: $egress_user" \
        "          ansible_port: $egress_port" \
        "          ansible_ssh_private_key_file: $egress_key" \
        "          ansible_ssh_common_args: '-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts_file -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes'" \
        >"$inventory"
    chmod 600 "$inventory"

    if [[ "$mode" == management ]]; then
        if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/manage_management_ssh.yml"; then
            rc=0
        else
            rc=$?
        fi
    elif [[ "$mode" == ingress-only ]]; then
        if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/deploy_cascade_ingress.yml"; then
            rc=0
        else
            rc=$?
            run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/rollback_cascade_ingress.yml" || true
        fi
    elif [[ "$mode" == egress-only ]]; then
        if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/deploy_cascade_egress.yml"; then
            rc=0
        else
            rc=$?
            run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/rollback_cascade_egress.yml" || true
        fi
    else
        if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/deploy_cascade_egress.yml"; then
            :
        else
            rc=$?
            run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/rollback_cascade_egress.yml" || true
            rm -f "$before" "$extra" "$ingress_key" "$egress_key"
            rm -rf "$inventory_dir"
            return "$rc"
        fi
        normalized="$(mktemp)"
        if ! python3 "$ROOT_DIR/scripts/state_cli.py" \
            capture-cascade-transport-keys "$deployment_id" \
            "$STATE_DIR/cascade/$deployment_id/ssh" \
            <"$before" >"$normalized"; then
            run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/rollback_cascade_egress.yml" || true
            rm -f "$before" "$extra" "$ingress_key" "$egress_key" "$normalized"
            rm -rf "$inventory_dir"
            return 1
        fi
        mv -f "$normalized" "$before"
        next_extra="$(mktemp)"
        if python3 "$ROOT_DIR/scripts/state_cli.py" \
            --cascade-local-root "$STATE_DIR/cascade" \
            extract-cascade "$deployment_id" <"$before" >"$next_extra"; then
            mv -f "$next_extra" "$extra"
        else
            run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/rollback_cascade_egress.yml" || true
            rm -f "$before" "$extra" "$next_extra" "$ingress_key" "$egress_key"
            rm -rf "$inventory_dir"
            return 1
        fi
        if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/deploy_cascade_ingress.yml"; then
            rc=0
        else
            rc=$?
            run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/rollback_cascade_ingress.yml" || true
            run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/rollback_cascade_egress.yml" || true
        fi
    fi
    if ((rc == 0)) && [[ -n "$state_file" ]]; then
        for node in "$ingress_node" "$egress_node"; do
            normalized="$(mktemp)"
            if ! python3 "$ROOT_DIR/scripts/state_cli.py" set-management-user "$node" deploy <"$before" >"$normalized"; then
                rm -f "$before" "$extra" "$ingress_key" "$egress_key" "$normalized"
                rm -rf "$inventory_dir"
                return 1
            fi
            mv -f "$normalized" "$before"
        done
        cp "$before" "$state_file"
    fi
    rm -f "$before" "$extra" "$ingress_key" "$egress_key"
    rm -rf "$inventory_dir"
    return "$rc"
}

run_remove_with_management_key() {
    local node="$1" extra_source="${2:-}" topology_role="${3:-}"
    local before extra inventory inventory_dir key_file host user port private_key rc pipeline_owned=0
    before="$(mktemp)"
    extra="$(mktemp)"
    inventory_dir="$(mktemp -d /tmp/xray-inventory.XXXXXX)"
    inventory="$inventory_dir/hosts.yml"
    key_file="$inventory_dir/id_ed25519"
    if ! read_vault_state "$before"; then
        rm -f "$before" "$extra" "$key_file"
        rm -rf "$inventory_dir"
        return 1
    fi
    host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["host"])' "$node" <"$before")"
    user="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["user"])' "$node" <"$before")"
    port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["port"])' "$node" <"$before")"
    private_key="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["private_key"], end="")' "$node" <"$before")"
    if [[ -z "$host" || -z "$port" || -z "$private_key" ]]; then
        rm -f "$before" "$extra" "$key_file"
        rm -rf "$inventory_dir"
        return 1
    fi
    printf '%s\n' "$private_key" >"$key_file"
    chmod 600 "$key_file"
    if [[ -n "$extra_source" ]]; then
        cp "$extra_source" "$extra"
    else
        python3 "$ROOT_DIR/scripts/state_cli.py" extract "$node" <"$before" >"$extra"
    fi
    if [[ -n "$topology_role" ]]; then
        printf '%s\n' \
            "topology_selection: cascade" \
            "topology_cascade_node_role: $topology_role" >>"$extra"
    fi
    printf '%s\n' \
        "---" \
        "all:" \
        "  children:" \
        "    xray_nodes:" \
        "      hosts:" \
        "        $node:" \
        "          ansible_host: $host" \
        "          ansible_user: $user" \
        "          ansible_port: $port" \
        "          ansible_ssh_private_key_file: $key_file" \
        "          ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o IdentitiesOnly=yes'" \
        >"$inventory"
    chmod 600 "$inventory"

    if ((DEBUG_MODE == 0 && PIPELINE_ACTIVE == 0)); then
        pipeline_start "Deleting VPN server" remove
        pipeline_owned=1
    fi
    if run_ansible_playbook --quiet -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/remove.yml" --private-key "$key_file"; then
        rc=0
    else
        rc=$?
    fi
    rm -f "$before" "$extra" "$key_file"
    rm -rf "$inventory_dir"
    if ((pipeline_owned)); then
        if ((rc != 0)); then pipeline_abort; fi
    fi
    return "$rc"
}

run_remove_with_bootstrap() {
    local node="$1" extra_source="${2:-}" topology_role="${3:-}"
    local before extra inventory inventory_dir host user port password password_yaml rc pipeline_owned=0
    before="$(mktemp)"
    extra="$(mktemp)"
    inventory_dir="$(mktemp -d /tmp/xray-inventory.XXXXXX)"
    inventory="$inventory_dir/hosts.yml"
    if ! read_vault_state "$before"; then
        rm -f "$before" "$extra"
        rm -rf "$inventory_dir"
        return 1
    fi
    host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["host"])' "$node" <"$before")"
    user="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap"].get("user", "root"))' "$node" <"$before")"
    port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap"]["port"])' "$node" <"$before")"
    password="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap"].get("password", ""), end="")' "$node" <"$before")"
    if [[ -n "$extra_source" ]]; then
        cp "$extra_source" "$extra"
    else
        python3 "$ROOT_DIR/scripts/state_cli.py" extract "$node" <"$before" >"$extra"
    fi
    if [[ -n "$topology_role" ]]; then
        printf '%s\n' \
            "topology_selection: cascade" \
            "topology_cascade_node_role: $topology_role" >>"$extra"
    fi

    if [[ -n "$password" ]]; then
        :
    else
        clear_screen
        printf '%s\n' "Remote deletion needs the initial SSH password."
        printf '%s\n' "VPS address: ${host}"
        printf '%s\n' "SSH user: ${user}"
        printf '%s\n' "SSH port: ${port}"
        echo
        if ! read_secret 'Initial SSH password: '; then
            rm -f "$before" "$extra"
            rm -rf "$inventory_dir"
            return 1
        fi
        password="$REPLY"
    fi
    password_yaml="$(yaml_scalar "$password")"
    unset password
    printf '%s\n' \
        "---" \
        "all:" \
        "  children:" \
        "    xray_nodes:" \
        "      hosts:" \
        "        $node:" \
        "          ansible_host: $host" \
        "          ansible_user: $user" \
        "          ansible_port: $port" \
        "          ansible_password: $password_yaml" \
        "          ansible_become_password: $password_yaml" \
        "          ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o ConnectionAttempts=1 -o PubkeyAuthentication=no -o PreferredAuthentications=password'" \
        >"$inventory"
    unset password_yaml
    chmod 600 "$inventory"

    if ((DEBUG_MODE == 0 && PIPELINE_ACTIVE == 0)); then
        pipeline_start "Deleting VPN server" remove
        pipeline_owned=1
    fi
    if run_ansible_playbook --quiet -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/remove.yml"; then
        rc=0
    else
        rc=$?
    fi
    rm -f "$before" "$extra"
    rm -rf "$inventory_dir"
    if ((pipeline_owned)); then
        if ((rc != 0)); then pipeline_abort; fi
    fi
    return "$rc"
}
